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


# ---------------------------------------------------------------------------
# HOTFIX 1: la regla de eval_set ya no invita a filtrar order_products por él
# ---------------------------------------------------------------------------

def test_regla_eval_set_dice_que_solo_existe_en_orders():
    """La regla anterior decía "filtra explícitamente eval_set IN
    ('prior','train') o usa la vista order_products", y qwen2.5-coder:1.5b
    combinaba las dos cosas: `order_products op ... WHERE op.eval_set IN (...)`
    -> Binder Error (docs/HOTFIX1_REPORTE.md)."""
    from src.llm.esquema_instacart import REGLAS_DE_NEGOCIO
    assert "eval_set existe SOLO en la tabla orders" in REGLAS_DE_NEGOCIO
    assert "order_products SIN filtrar por eval_set" in REGLAS_DE_NEGOCIO
    assert "filtra explícitamente eval_set IN" not in REGLAS_DE_NEGOCIO


def test_cache_con_reglas_viejas_se_reconstruye(con, tmp_path):
    """Misma huella de BD pero reglas distintas: la caché no debe servir la
    descripción vieja (antes la clave era solo la huella)."""
    from src.llm.esquema_instacart import REGLAS_DE_NEGOCIO
    ruta_cache = tmp_path / "esquema.json"
    construir_descripcion_esquema(con, cache_path=ruta_cache)
    datos = json.loads(ruta_cache.read_text(encoding="utf-8"))
    datos["reglas"] = "REGLAS VIEJAS"
    datos["descripcion"] = "DESCRIPCION VIEJA CON op.eval_set"
    ruta_cache.write_text(json.dumps(datos), encoding="utf-8")

    desc = construir_descripcion_esquema(con, cache_path=ruta_cache)

    assert "DESCRIPCION VIEJA" not in desc
    assert REGLAS_DE_NEGOCIO in desc
    assert json.loads(ruta_cache.read_text(encoding="utf-8"))["reglas"] == REGLAS_DE_NEGOCIO


def test_cache_sin_campo_reglas_se_reconstruye(con, tmp_path):
    """Una caché escrita antes del HOTFIX (sin campo "reglas") se reconstruye."""
    ruta_cache = tmp_path / "esquema.json"
    construir_descripcion_esquema(con, cache_path=ruta_cache)
    datos = json.loads(ruta_cache.read_text(encoding="utf-8"))
    del datos["reglas"]
    datos["descripcion"] = "DESCRIPCION PRE-HOTFIX"
    ruta_cache.write_text(json.dumps(datos), encoding="utf-8")

    assert "PRE-HOTFIX" not in construir_descripcion_esquema(con, cache_path=ruta_cache)


# ---------------------------------------------------------------------------
# Fase 12: reglas agregadas tras ver, en una corrida real contra Ollama
# (docs/evidencia/benchmark_fase11d_prueba.json), que el modelo confundía
# el JOIN de pasillo/departamento y el filtro de "reordenado".
# ---------------------------------------------------------------------------

def test_regla_aisle_department_exige_join_explicito():
    """Un modelo real escribió `GROUP BY departments.department` sin haber
    unido esa tabla -> Binder Error 'Referenced table "departments" not
    found!' (ver docs/evidencia/benchmark_fase11d_prueba.json)."""
    from src.llm.esquema_instacart import REGLAS_DE_NEGOCIO
    aplanado = " ".join(REGLAS_DE_NEGOCIO.split())
    assert "LLAVES FORÁNEAS" in aplanado
    assert "JOIN explícito" in aplanado


def test_regla_reordered_exige_filtrar_por_1():
    """Un modelo real contó TODAS las líneas de order_products para "top 5
    de productos con más veces reordenados" sin filtrar reordered = 1
    (mismo JSON de evidencia) -- SQL válido, respuesta incorrecta.

    Compara contra el texto con espacios/saltos de línea colapsados: la
    regla se reenvuelve de vez en cuando (p. ej. al agregar una frase) y un
    `in` literal se rompe cada vez que la frase buscada cae justo en un
    salto de línea del párrafo -- ya pasó una vez (Fase 13). La redacción
    exacta de la regla no es lo que importa aquí, solo que el contenido
    esté."""
    from src.llm.esquema_instacart import REGLAS_DE_NEGOCIO
    aplanado = " ".join(REGLAS_DE_NEGOCIO.split())
    assert "reordered = 1" in aplanado
    assert "NO cuentes todas las líneas" in aplanado


def test_regla_promedio_de_dos_pasos_trae_ejemplo_literal_de_subconsulta():
    """Fase 16: en las Fases 14-15, tres modelos (1.5B, 7B y 8B) fallaron
    el mismo patrón -- `AVG(product_id)`, `AVG(order_id)`,
    `AVG(add_to_cart_order)` -- para preguntas de "promedio de líneas por
    pedido/pasillo". Se confirmó que las reglas no tenían NINGÚN ejemplo
    de subconsulta (grep por "FROM (", "sub", "AVG" en todo src/llm/ sin
    resultados relevantes), y el ejemplo literal de JOIN de la Fase 14 sí
    funcionó para ese otro problema -- de ahí la hipótesis de agregar un
    ejemplo literal también para el promedio de dos pasos."""
    from src.llm.esquema_instacart import REGLAS_DE_NEGOCIO
    aplanado = " ".join(REGLAS_DE_NEGOCIO.split())
    assert "DOS PASOS" in aplanado
    assert "GROUP BY <columna_del_grupo>" in aplanado
    assert "NUNCA escribas AVG(product_id)" in aplanado
