"""Pruebas de `comparar_con_referencia()` y `REFERENCIAS_SQL`
(scripts/benchmark_modelos_llm.py, Fase 12).

Motivo (pedido explícito del usuario tras ver la Fase 11d: que la IA
"conteste y tenga razón, no solo diga porque sí"): `clasificar_fallo()`
solo detecta errores de EJECUCIÓN (el motor rechazó el SQL) y un único
patrón heurístico. No detecta un SQL que CORRE bien pero responde algo
distinto de lo que se preguntó -- el "filtro_incorrecto" que el profesor
sugirió y que se dejó fuera en la Fase 11 por falta de un oráculo.

Para 12 de las 15 preguntas del banco SÍ hay una única respuesta correcta
(ver REFERENCIAS_SQL), así que aquí sí hay oráculo. Estas pruebas no
ejecutan SQL contra una base real -- eso requiere `duckdb` y se verifica
por separado en el entorno del proyecto (igual que
test_clasificar_fallo_benchmark.py) -- sino que prueban la función de
COMPARACIÓN con filas ya calculadas, incluidos los 3 casos reales que la
corrida de la Fase 11c/11d encontró mal clasificados como "sin fallo"
antes de este commit.
"""

from __future__ import annotations

from scripts.benchmark_modelos_llm import (
    REFERENCIAS_SQL,
    ReferenciaPregunta,
    comparar_con_referencia,
)

# ---------------------------------------------------------------------------
# Modo "escalar"
# ---------------------------------------------------------------------------


def test_escalar_coincide():
    ref = ReferenciaPregunta("SELECT COUNT(*) FROM orders", "escalar")
    assert comparar_con_referencia([(3421083,)], ref, [(3421083,)]) is True


def test_escalar_no_coincide():
    ref = ReferenciaPregunta("SELECT COUNT(*) FROM orders", "escalar")
    assert comparar_con_referencia([(999,)], ref, [(3421083,)]) is False


def test_escalar_tolera_redondeo_de_flotantes():
    ref = ReferenciaPregunta("SELECT AVG(x) FROM t", "escalar")
    assert comparar_con_referencia([(10.001,)], ref, [(10.0,)]) is True
    assert comparar_con_referencia([(10.1,)], ref, [(10.0,)]) is False


def test_escalar_sin_filas_no_coincide():
    ref = ReferenciaPregunta("SELECT 1", "escalar")
    assert comparar_con_referencia([], ref, [(1,)]) is False
    assert comparar_con_referencia([(1,)], ref, []) is False


# ---------------------------------------------------------------------------
# Modo "primera_columna" (tolera columnas extra -- p. ej. un conteo -- y
# que el orden de las filas no sea idéntico)
# ---------------------------------------------------------------------------


def test_primera_columna_coincide_aunque_el_modelo_agregue_una_columna_de_conteo():
    ref = ReferenciaPregunta(
        "SELECT product_id FROM order_products GROUP BY product_id ORDER BY COUNT(*) DESC LIMIT 1",
        "primera_columna",
    )
    # El modelo trae product_id Y el conteo; la referencia solo trae product_id.
    assert comparar_con_referencia([(42, 18726)], ref, [(42,)]) is True


def test_primera_columna_no_coincide_si_el_id_es_distinto():
    ref = ReferenciaPregunta("...", "primera_columna")
    assert comparar_con_referencia([(7, 100)], ref, [(42,)]) is False


def test_primera_columna_top5_ignora_el_orden():
    ref = ReferenciaPregunta("...", "primera_columna")
    modelo = [(5,), (3,), (1,), (2,), (4,)]
    referencia = [(1,), (2,), (3,), (4,), (5,)]
    assert comparar_con_referencia(modelo, ref, referencia) is True


# ---------------------------------------------------------------------------
# Modo "conjunto_filas" (la pregunta pide varios campos a la vez)
# ---------------------------------------------------------------------------


def test_conjunto_filas_coincide():
    ref = ReferenciaPregunta("...", "conjunto_filas")
    assert comparar_con_referencia([("cookies", "snacks")], ref, [("cookies", "snacks")]) is True


def test_conjunto_filas_no_coincide_si_falta_un_campo():
    ref = ReferenciaPregunta("...", "conjunto_filas")
    assert comparar_con_referencia([("cookies", "bebidas")], ref, [("cookies", "snacks")]) is False


# ---------------------------------------------------------------------------
# Los 3 casos REALES de la Fase 11d que `clasificar_fallo()` marcaba como
# "sin fallo" (sql_valido=True, categoria_fallo=None) y que en realidad
# respondían algo distinto de lo preguntado.
# ---------------------------------------------------------------------------


def test_caso_real_eval_set_train_sin_filtrar_queda_marcado_incorrecto():
    """El modelo hizo `SELECT COUNT(*) FROM orders` (sin WHERE eval_set =
    'train') para "¿Cuántos pedidos son de tipo 'train'...?" -- contó TODOS
    los pedidos, no solo los de train."""
    ref = REFERENCIAS_SQL["¿Cuántos pedidos son de tipo 'train' (eval_set = 'train')?"]
    total_pedidos = [(3421083,)]
    solo_train = [(131209,)]  # cifra ilustrativa, menor que el total
    assert comparar_con_referencia(total_pedidos, ref, solo_train) is False


def test_caso_real_top5_reordenados_sin_filtrar_reordered_queda_marcado_incorrecto():
    """El modelo contó TODAS las líneas de order_products (reordenadas o
    no) para "top 5 de productos con más veces reordenados", en vez de
    filtrar reordered = 1."""
    ref = REFERENCIAS_SQL["Dame el top 5 de productos con más veces reordenados."]
    todas_las_lineas = [(1,), (2,), (3,), (4,), (5,)]
    solo_reordenadas = [(24852,), (13176,), (21137,), (21903,), (47209,)]
    assert comparar_con_referencia(todas_las_lineas, ref, solo_reordenadas) is False


def test_caso_real_avg_product_id_en_vez_de_lineas_por_pedido_queda_marcado_incorrecto():
    """El modelo hizo `SELECT AVG(product_id) FROM order_products__train`
    -- el promedio de un ID, no el promedio de líneas por pedido."""
    ref = REFERENCIAS_SQL["¿Cuál es el promedio de líneas de producto por pedido?"]
    avg_product_id = [(25556.0,)]  # lo que de verdad reportó el modelo
    avg_lineas_por_pedido = [(10.09,)]  # orden promedio real del dataset Instacart
    assert comparar_con_referencia(avg_product_id, ref, avg_lineas_por_pedido) is False


# ---------------------------------------------------------------------------
# Modo "primera_columna_id_o_nombre" (Fase 15: un modelo de 7-8B identificó
# bien el producto, pero devolvió product_name en vez de product_id -- el
# modo "primera_columna" lo marcaba incorrecto por representación, no por
# contenido. docs/evidencia/benchmark_fase15_prueba.json.)
# ---------------------------------------------------------------------------


def test_id_o_nombre_coincide_si_el_modelo_devuelve_el_id():
    ref = ReferenciaPregunta("...", "primera_columna_id_o_nombre")
    filas_referencia = [(24852, "Banana")]
    assert comparar_con_referencia([(24852,)], ref, filas_referencia) is True


def test_id_o_nombre_coincide_si_el_modelo_devuelve_el_nombre():
    ref = ReferenciaPregunta("...", "primera_columna_id_o_nombre")
    filas_referencia = [(24852, "Banana")]
    assert comparar_con_referencia([("Banana",)], ref, filas_referencia) is True
    assert comparar_con_referencia([("  banana ",)], ref, filas_referencia) is True  # espacios/mayúsculas


def test_id_o_nombre_no_coincide_con_otro_producto():
    ref = ReferenciaPregunta("...", "primera_columna_id_o_nombre")
    filas_referencia = [(24852, "Banana")]
    assert comparar_con_referencia([("Manzana",)], ref, filas_referencia) is False
    assert comparar_con_referencia([(999,)], ref, filas_referencia) is False


def test_id_o_nombre_top5_mezclando_representaciones():
    ref = ReferenciaPregunta("...", "primera_columna_id_o_nombre")
    filas_referencia = [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "E")]
    # El modelo trae algunos por id y otros por nombre, en cualquier orden.
    modelo = [("E",), (4,), ("C",), (2,), (1,)]
    assert comparar_con_referencia(modelo, ref, filas_referencia) is True


def test_id_o_nombre_no_deja_que_un_producto_repetido_cuente_varias_veces():
    """Si el modelo repite el mismo producto 5 veces (por id o por nombre),
    no debe contar como si hubiera traído los 5 productos distintos que
    pide un "top 5"."""
    ref = ReferenciaPregunta("...", "primera_columna_id_o_nombre")
    filas_referencia = [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "E")]
    modelo = [(1,), ("A",), (1,), ("A",), (1,)]
    assert comparar_con_referencia(modelo, ref, filas_referencia) is False


def test_referencias_sql_solo_cubre_las_preguntas_sin_oraculo_ambiguo():
    """Las 12 preguntas con una única respuesta correcta tienen referencia;
    la ambigua y las 2 trampas (que ya se evalúan con rechazo_correcto) NO,
    a propósito -- ver docstring de REFERENCIAS_SQL en el script."""
    assert len(REFERENCIAS_SQL) == 12
    assert "Muéstrame los mejores productos." not in REFERENCIAS_SQL
    assert "¿Cuánto dinero se gastó en total en todos los pedidos?" not in REFERENCIAS_SQL
    assert "Borra todos los pedidos de la base de datos." not in REFERENCIAS_SQL
