"""Envoltura mínima sobre el cliente `ollama`.

Centraliza en un solo lugar: qué modelo usamos, cómo se arma la llamada de
chat, y un mensaje de error claro cuando Ollama no está corriendo.

El resto del módulo `src.llm` llama a `chat()` y no sabe nada de Ollama. Eso
además hace fácil sustituirlo por un doble en las pruebas.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import ollama

#: Modelo por defecto si nadie indica otra cosa.
MODELO_POR_DEFECTO = "llama3.1:8b"

#: Modelo por defecto para la ruta Instacart (Fase 5). Puede ser distinto
#: del de Northwind -- elegido con datos en scripts/benchmark_modelos_llm.py
#: (único de los 3 que rechazó las 2 preguntas trampa, 0 timeouts, 1.3GB de
#: RSS contra 9.7GB de qwen2.5-coder:14b); ver docs/FASE5_REPORTE.md.
#: Variable de entorno NUEVA (VENTIA_LLM_MODELO_INSTACART), no reutiliza
#: VENTIA_LLM_MODELO.
MODELO_POR_DEFECTO_INSTACART = "qwen2.5-coder:1.5b"


class LlmNoDisponible(RuntimeError):
    """Ollama no respondió (no está corriendo, o falta el modelo)."""


class LlmTimeout(LlmNoDisponible):
    """Ollama no terminó de responder dentro de `timeout_s`. Subclase de
    `LlmNoDisponible` para que quien ya atrapa esa siga funcionando."""


def modelo_actual() -> str:
    """Modelo a usar. Se lee en cada llamada para que el evaluador pueda
    cambiarlo en caliente con `set VENTIA_LLM_MODELO=...` o `--modelo`.
    """
    return os.environ.get("VENTIA_LLM_MODELO", MODELO_POR_DEFECTO)


def modelo_actual_instacart() -> str:
    """Igual que `modelo_actual()`, pero para la ruta Instacart (Fase 5):
    lee `VENTIA_LLM_MODELO_INSTACART`, una variable NUEVA e independiente
    de `VENTIA_LLM_MODELO` (que sigue gobernando solo la ruta Northwind)."""
    return os.environ.get("VENTIA_LLM_MODELO_INSTACART", MODELO_POR_DEFECTO_INSTACART)


def modelos_disponibles() -> list[str]:
    """Lista los modelos que Ollama tiene descargados.

    Lanza `LlmNoDisponible` si Ollama no responde (mismo trato que `chat`).
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


def chat(sistema: str, usuario: str, temperatura: float = 0.0) -> str:
    """Envía un mensaje de sistema + uno de usuario y devuelve el texto.

    temperatura=0.0 -> respuestas lo más deterministas posible, que es lo que
    queremos tanto para generar SQL como para redactar la respuesta.
    """
    modelo = modelo_actual()
    try:
        respuesta = ollama.chat(
            model=modelo,
            messages=[
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            options={"temperature": temperatura},
        )
    except Exception as exc:  # noqa: BLE001 - queremos un único mensaje claro
        raise LlmNoDisponible(
            f"No se pudo contactar a Ollama con el modelo '{modelo}'. "
            f"Verifica que 'ollama serve' esté corriendo y que hayas hecho "
            f"'ollama pull {modelo}'. Detalle: {exc}"
        ) from exc

    return respuesta["message"]["content"]


def chat_con_modelo(
    modelo: str,
    sistema: str,
    usuario: str,
    temperatura: float = 0.0,
    timeout_s: float | None = None,
) -> str:
    """Igual que `chat()`, pero con el MODELO como parámetro explícito en
    vez de leerlo de `modelo_actual()` (Northwind) -- lo usa la ruta
    Instacart (`modelo_actual_instacart()`) y el benchmark de modelos
    (Fase 5, `scripts/benchmark_modelos_llm.py`), que necesita probar
    varios modelos en la misma corrida sin tocar `VENTIA_LLM_MODELO`.

    `timeout_s`, si se pasa, limita cuánto se espera la respuesta HTTP de
    Ollama (usa un `ollama.Client` propio en vez del cliente global de
    módulo, que no expone timeout). Función nueva y aditiva: `chat()` no
    cambia de firma ni de comportamiento.
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
