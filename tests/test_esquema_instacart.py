"""Introspección dinámica del esquema Instacart (Fase 5).

Corre contra la fixture chica (`tests/fixtures/instacart_needs/`), no
contra `data/instacart.duckdb`: el esquema (nombres de tabla/columna) es
el mismo sin importar el volumen de datos -- se verificó a mano que la
descripción generada contra el dataset completo tiene la misma forma (ver
`docs/FASE5_REPORTE.md`). No depende de tener el dataset completo en CI.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from src.db.instacart import construir_duckdb
from src.llm.esquema_instacart import construir_descripcion_esquema
from tests.helpers_analytics import FIXTURE_DIR

TABLAS_REALES = (
    "aisles", "departments", "products", "orders",
    "order_products__prior", "order_products__train", "order_products",
)
NOMBRES_NORTHWIND_PROHIBIDOS = (
    "Products", "Orders", "Order Details", "Customers", "Employees",
    "Suppliers", "Categories", "Shippers",
)


@pytest.fixture()
def con(tmp_path):
    db_path = tmp_path / "needs.duckdb"
    construir_duckdb(FIXTURE_DIR, db_path)
    c = duckdb.connect(str(db_path), read_only=True)
    yield c
    c.close()


def test_descripcion_contiene_las_7_tablas_reales(con, tmp_path):
    desc = construir_descripcion_esquema(con, cache_path=tmp_path / "esquema.json")
    for tabla in TABLAS_REALES:
        assert tabla in desc, f"falta la tabla/vista '{tabla}' en la descripción del esquema"


def test_descripcion_no_menciona_tablas_de_northwind(con, tmp_path):
    desc = construir_descripcion_esquema(con, cache_path=tmp_path / "esquema.json")
    for nombre in NOMBRES_NORTHWIND_PROHIBIDOS:
        assert nombre not in desc, f"la descripción menciona '{nombre}' (nombre de tabla de Northwind)"


def test_descripcion_incluye_reglas_de_negocio(con, tmp_path):
    desc = construir_descripcion_esquema(con, cache_path=tmp_path / "esquema.json")
    for fragmento in ("eval_set = 'test'", "CENSURADO en 30", "ANÓNIMOS", "NO TIENE PRECIOS"):
        assert fragmento in desc, f"falta la regla de negocio que menciona {fragmento!r}"


def test_descripcion_incluye_filas_de_ejemplo_reales(con, tmp_path):
    desc = construir_descripcion_esquema(con, cache_path=tmp_path / "esquema.json")
    assert desc.count("ej:") >= len(TABLAS_REALES), "faltan filas de ejemplo por tabla"


def test_cache_se_reusa_si_la_huella_no_cambio(con, tmp_path):
    ruta_cache = tmp_path / "esquema.json"
    assert not ruta_cache.exists()

    desc1 = construir_descripcion_esquema(con, cache_path=ruta_cache)
    assert ruta_cache.exists()
    datos = json.loads(ruta_cache.read_text(encoding="utf-8"))
    assert "huella_bd" in datos and "descripcion" in datos

    desc2 = construir_descripcion_esquema(con, cache_path=ruta_cache)
    assert desc1 == desc2


def test_cache_rancia_se_reconstruye(con, tmp_path):
    ruta_cache = tmp_path / "esquema.json"
    construir_descripcion_esquema(con, cache_path=ruta_cache)

    datos = json.loads(ruta_cache.read_text(encoding="utf-8"))
    datos["huella_bd"]["orders_total"] += 1  # "cambia un byte" de la huella guardada
    datos["descripcion"] = "DESCRIPCIÓN VIEJA Y RANCIA QUE NO DEBERÍA SERVIRSE"
    ruta_cache.write_text(json.dumps(datos), encoding="utf-8")

    desc = construir_descripcion_esquema(con, cache_path=ruta_cache)
    assert desc != "DESCRIPCIÓN VIEJA Y RANCIA QUE NO DEBERÍA SERVIRSE"
    assert "orders" in desc


def test_forzar_ignora_la_cache_vigente(con, tmp_path):
    ruta_cache = tmp_path / "esquema.json"
    desc1 = construir_descripcion_esquema(con, cache_path=ruta_cache)
    desc2 = construir_descripcion_esquema(con, cache_path=ruta_cache, forzar=True)
    assert desc1 == desc2  # mismos datos -> mismo contenido, pero sí se reconstruyó
