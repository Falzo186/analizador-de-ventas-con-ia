"""Envoltura mínima sobre el cliente `ollama`.

Centraliza en un solo lugar: qué modelo usamos, cómo se arma la llamada de
chat, y un mensaje de error claro cuando Ollama no está corriendo.

El resto del módulo `src.llm` llama a `chat()` y no sabe nada de Ollama. Eso
además hace fácil sustituirlo por un doble en las pruebas.
"""

from __future__ import annotations

import os

import ollama

#: Modelo por defecto si nadie indica otra cosa.
MODELO_POR_DEFECTO = "llama3.1:8b"


class LlmNoDisponible(RuntimeError):
    """Ollama no respondió (no está corriendo, o falta el modelo)."""


def modelo_actual() -> str:
    """Modelo a usar. Se lee en cada llamada para que el evaluador pueda
    cambiarlo en caliente con `set VENTIA_LLM_MODELO=...` o `--modelo`.
    """
    return os.environ.get("VENTIA_LLM_MODELO", MODELO_POR_DEFECTO)


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
