"""Fixtures comunes a toda la suite."""

from __future__ import annotations

import pytest

from src.llm import bitacora_chat


@pytest.fixture(autouse=True)
def _bitacora_del_chat_aislada(monkeypatch, tmp_path):
    """Fase 27: ningún test lee ni escribe la bitácora real del chat
    (`data/cache/bitacora_chat.json`); cada uno tiene la suya, vacía."""
    monkeypatch.setattr(bitacora_chat, "RUTA_DEFAULT", tmp_path / "bitacora_chat.json")
