"""Configuración compartida de las pruebas.

Antes de importar cualquier módulo de `src.db`, apuntamos la base de datos a
un archivo temporal. Así las pruebas nunca tocan `data/ventia.db` (tus datos
reales).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Se ejecuta al recolectar las pruebas, ANTES de importar src.db.database.
_TMP_DB = Path(tempfile.gettempdir()) / "ventia_test.db"
os.environ["VENTIA_DB_PATH"] = str(_TMP_DB)


@pytest.fixture(autouse=True)
def base_limpia():
    """Recrea todas las tablas vacías antes de cada prueba."""
    from src.db.database import RUTA_DB, engine
    from src.db.schema import Base

    if RUTA_DB.exists():
        RUTA_DB.unlink()
    Base.metadata.create_all(engine)
    yield
    engine.dispose()
    if RUTA_DB.exists():
        RUTA_DB.unlink()
