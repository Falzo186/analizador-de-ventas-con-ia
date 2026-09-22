"""`construir_ficha()` (Fase 4) es la única fuente de las cifras de
`docs/DATASET_INSTACART.md` y de la portada del PDF (`src/pdf/generador.py`).
Este test confirma que las dos coinciden: regenera el markdown a partir de
`construir_ficha()` y lo compara contra el archivo actual en disco, byte a
byte salvo la línea de fecha (que cambia por diseño en cada corrida).

Se salta si `data/instacart.duckdb` no existe (mismo criterio que el resto
de los tests marcados `integration`).
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from scripts.generar_ficha_dataset import _ficha_a_markdown, construir_ficha

RAIZ = Path(__file__).resolve().parents[1]
# Rutas RELATIVAS a propósito: `docs/DATASET_INSTACART.md` en disco se generó
# con `python -m scripts.generar_ficha_dataset` corrido desde la raíz del
# repo con sus defaults relativos ("data/instacart.duckdb"); una ruta
# absoluta aquí haría que la línea "Archivo:" nunca coincidiera aunque el
# resto de la ficha sí.
DB_PATH = Path("data/instacart.duckdb")
RAW_DIR = Path("data/instacart/raw")
FICHA_MD = RAIZ / "docs" / "DATASET_INSTACART.md"

pytestmark = pytest.mark.integration

_FALTAN = not (DB_PATH.exists() and RAW_DIR.exists() and FICHA_MD.exists())
_RAZON = f"Requiere {DB_PATH.name}, {RAW_DIR} y {FICHA_MD.name}"

_PATRON_FECHA = re.compile(r"_Generado automáticamente.*el \d{4}-\d{2}-\d{2}\. .*_")


def _sin_fecha(texto: str) -> str:
    return _PATRON_FECHA.sub("_[fecha omitida para la comparación]_", texto)


@pytest.mark.skipif(_FALTAN, reason=_RAZON)
def test_construir_ficha_coincide_con_dataset_instacart_md():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ficha = construir_ficha(con, DB_PATH, RAW_DIR)
    finally:
        con.close()

    md_regenerado = _sin_fecha(_ficha_a_markdown(ficha, DB_PATH))
    md_en_disco = _sin_fecha(FICHA_MD.read_text(encoding="utf-8"))

    assert md_regenerado == md_en_disco, (
        "docs/DATASET_INSTACART.md está desactualizado respecto a construir_ficha(); "
        "corre: python -m scripts.generar_ficha_dataset"
    )


@pytest.mark.skipif(_FALTAN, reason=_RAZON)
def test_construir_ficha_cifras_clave():
    """Belt-and-suspenders: cifras puntuales, para que un fallo del test de
    arriba (si el markdown y la ficha divergen) diga también QUÉ número
    concreto está mal, no solo "el texto no coincide"."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ficha = construir_ficha(con, DB_PATH, RAW_DIR)
    finally:
        con.close()

    por_tabla = {d.nombre: d.filas for d in ficha.dimensiones}
    assert por_tabla["orders"] == 3_421_083
    assert por_tabla["order_products__prior"] == 32_434_489
    assert por_tabla["order_products__train"] == 1_384_617
    assert por_tabla["products"] == 49_688
    assert por_tabla["aisles"] == 134
    assert por_tabla["departments"] == 21
    assert ficha.orders_test == 75_000
    assert ficha.n_fail == 0
    assert ficha.n_pass == 22
    assert ficha.n_info == 13
