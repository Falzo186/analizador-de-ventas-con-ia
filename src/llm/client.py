"""Envoltura mínima sobre el cliente `ollama`.

Centraliza en un solo lugar: qué modelo usamos, cómo se arma la llamada de
chat, y un mensaje de error claro cuando Ollama no está corriendo.

El resto de `src.llm` llama a `chat_con_modelo()` / `chat_con_modelo_stream()`
y no sabe nada de Ollama. Eso además hace fácil sustituirlo por un doble en
las pruebas.

Fase 7: se retiraron `chat()`, `modelo_actual()`, `MODELO_POR_DEFECTO` y la
variable `VENTIA_LLM_MODELO`, que solo usaba la ruta Northwind (archivada
en `legacy/northwind/`). El modelo se elige con `VENTIA_LLM_MODELO_INSTACART`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import ollama

#: Modelo por defecto. Elegido originalmente en la Fase 5 (qwen2.5-coder:1.5b;
#: ver docs/FASE5_REPORTE.md) por tasa de SQL válido, rechazo de trampas y RSS
#: bajo -- esos criterios no medían si la respuesta era SEMÁNTICAMENTE
#: correcta. La Fase 11-18 agregó un oráculo de referencia
#: (scripts/benchmark_modelos_llm.py, docs/CLASIFICACION_FALLOS.md) y con esa
#: métrica qwen2.5-coder:1.5b solo acertaba 10/12 (83%) de las preguntas
#: evaluables, contra 12/12 (100%) de llama3.1:8b y de qwen2.5-coder:latest
#: (7B) -- ambos con un costo medido de solo +0.4s por pregunta y +4GB de
#: RAM/VRAM en esta máquina (16GB RAM libre, 5.7GB VRAM libre), ver "Fase 18"
#: en docs/CLASIFICACION_FALLOS.md. Se cambió a llama3.1:8b (Fase 19) por ser
#: el más rápido de los dos con 100%. Se cambia con la variable de entorno
#: VENTIA_LLM_MODELO_INSTACART (útil para volver a qwen2.5-coder:1.5b en una
#: máquina sin GPU o con poca RAM).
MODELO_POR_DEFECTO_INSTACART = "llama3.1:8b"


class LlmNoDisponible(RuntimeError):
    """Ollama no respondió (no está corriendo, o falta el modelo)."""


class LlmTimeout(LlmNoDisponible):
    """Ollama no terminó de responder dentro de `timeout_s`. Subclase de
    `LlmNoDisponible` para que quien ya atrapa esa siga funcionando."""


def modelo_actual_instacart() -> str:
    """Modelo a usar. Se lee en cada llamada (`VENTIA_LLM_MODELO_INSTACART`,
    o `MODELO_POR_DEFECTO_INSTACART` si no está definida), así que se puede
    cambiar sin reiniciar el proceso."""
    return os.environ.get("VENTIA_LLM_MODELO_INSTACART", MODELO_POR_DEFECTO_INSTACART)


def modelos_disponibles() -> list[str]:
    """Lista los modelos que Ollama tiene descargados.

    Lanza `LlmNoDisponible` si Ollama no responde (mismo trato que `chat_con_modelo`).
    """
    try:
        data = ollama.list()
    except Exception as exc:  # noqa: BLE001
        raise LlmNoDisponible(
            "No se pudo listar los modelos: ¿está corriendo 'ollama serve'? "
            f"Detalle: {exc}"
        ) from exc

    nombres: list[str] = []
    for m in data.get("models", []):
        nombre = m.get("model") or m.get("name")
        if nombre:
            nombres.append(nombre)
    return nombres


#: Timeout de las llamadas de administración (`/api/ps` y la descarga). No
#: generan texto: con Ollama sano responden en milisegundos.
TIMEOUT_ADMIN_S = 10.0


def modelos_cargados() -> list[dict]:
    """Modelos que Ollama tiene cargados en memoria AHORA (`GET /api/ps`),
    como `[{"nombre", "size_bytes", "expires_at"}]`. `size_bytes` es el
    `size` que reporta la API; en esta máquina coincide con el RSS real del
    proceso `llama-server` con menos del 1% de diferencia
    (docs/FASE9_INVESTIGACION.md). Lanza `LlmNoDisponible` si Ollama no responde."""
    try:
        data = ollama.Client(timeout=TIMEOUT_ADMIN_S).ps()
    except Exception as exc:  # noqa: BLE001
        raise LlmNoDisponible(f"No se pudo consultar /api/ps: ¿está corriendo Ollama? Detalle: {exc}") from exc
    return [
        {"nombre": m.get("model") or m.get("name"), "size_bytes": m.get("size"), "expires_at": m.get("expires_at")}
        for m in data.get("models", [])
    ]


def liberar_modelo(modelo: str) -> None:
    """Descarga `modelo` de la memoria sin tocar el servicio: `POST
    /api/generate` con `keep_alive: 0` y sin prompt. Ollama responde
    `done_reason: "unload"` y termina el proceso `llama-server` del modelo
    (verificado en docs/FASE9_INVESTIGACION.md). Si el modelo no estaba
    cargado, no tiene efecto. Lanza `LlmNoDisponible` si Ollama no responde
    o el modelo no existe (HTTP 404)."""
    try:
        ollama.Client(timeout=TIMEOUT_ADMIN_S).generate(model=modelo, prompt="", keep_alive=0)
    except Exception as exc:  # noqa: BLE001
        raise LlmNoDisponible(f"No se pudo liberar el modelo '{modelo}': {exc}") from exc


def chat_con_modelo(
    modelo: str,
    sistema: str,
    usuario: str,
    temperatura: float = 0.0,
    timeout_s: float | None = None,
) -> str:
    """Envía un mensaje de sistema + uno de usuario al `modelo` indicado y
    devuelve el texto. El modelo es parámetro explícito: la ruta Instacart
    pasa `modelo_actual_instacart()` y el benchmark de modelos (Fase 5,
    `scripts/benchmark_modelos_llm.py`) prueba varios en la misma corrida.
    temperatura=0.0 -> respuestas lo más deterministas posible.

    `timeout_s`, si se pasa, limita cuánto se espera la respuesta HTTP de
    Ollama (usa un `ollama.Client` propio en vez del cliente global de
    módulo, que no expone timeout).
    """
    try:
        if timeout_s is not None:
            cliente = ollama.Client(timeout=timeout_s)
            respuesta = cliente.chat(
                model=modelo,
                messages=[
                    {"role": "system", "content": sistema},
                    {"role": "user", "content": usuario},
                ],
                options={"temperature": temperatura},
            )
        else:
            respuesta = ollama.chat(
                model=modelo,
                messages=[
                    {"role": "system", "content": sistema},
                    {"role": "user", "content": usuario},
                ],
                options={"temperature": temperatura},
            )
    except httpx.TimeoutException as exc:
        raise LlmTimeout(
            f"El modelo '{modelo}' no respondió en {timeout_s:.0f}s y se canceló la espera."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - queremos un único mensaje claro
        raise LlmNoDisponible(
            f"No se pudo contactar a Ollama con el modelo '{modelo}'. "
            f"Verifica que 'ollama serve' esté corriendo y que hayas hecho "
            f"'ollama pull {modelo}'. Detalle: {exc}"
        ) from exc

    return respuesta["message"]["content"]


def chat_con_modelo_stream(
    modelo: str,
    sistema: str,
    usuario: str,
    temperatura: float = 0.0,
    timeout_s: float | None = None,
) -> Iterator[str]:
    """Igual que `chat_con_modelo`, pero con `stream=True`: devuelve los
    fragmentos de texto a medida que Ollama los genera (Fase 6, chat con
    streaming). `ollama-python` 0.3.3 lo soporta: con `stream=True`,
    `Client.chat` devuelve un generador de dicts `{"message": {"content":
    ...}, "done": bool}`.

    `timeout_s` es el timeout de httpx, que en streaming se aplica POR
    LECTURA (tiempo máximo sin recibir un fragmento), no a la respuesta
    completa. Los errores se traducen igual que en `chat_con_modelo`, en el
    momento en que ocurren (también a mitad del stream).
    """
    try:
        cliente = ollama.Client(timeout=timeout_s)
        fragmentos = cliente.chat(
            model=modelo,
            messages=[
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            options={"temperature": temperatura},
            stream=True,
        )
        for fragmento in fragmentos:
            texto = fragmento["message"]["content"]
            if texto:
                yield texto
    except httpx.TimeoutException as exc:
        raise LlmTimeout(
            f"El modelo '{modelo}' dejó de enviar texto por más de {timeout_s:.0f}s y se canceló la espera."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - queremos un único mensaje claro
        raise LlmNoDisponible(
            f"No se pudo contactar a Ollama con el modelo '{modelo}'. "
            f"Verifica que 'ollama serve' esté corriendo y que hayas hecho "
            f"'ollama pull {modelo}'. Detalle: {exc}"
        ) from exc
