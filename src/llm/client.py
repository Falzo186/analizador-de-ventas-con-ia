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

#: Modelo por defecto (Fase 5), elegido con datos en
#: scripts/benchmark_modelos_llm.py (único de los 3 que rechazó las 2
#: preguntas trampa, 0 timeouts, 1.3GB de RSS contra 9.7GB de
#: qwen2.5-coder:14b); ver docs/FASE5_REPORTE.md. Se cambia con la variable
#: de entorno VENTIA_LLM_MODELO_INSTACART.
MODELO_POR_DEFECTO_INSTACART = "qwen2.5-coder:1.5b"


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
