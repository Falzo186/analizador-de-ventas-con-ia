"""Las 3 guardas de seguridad que la auditoría marcó como faltantes en la
ruta de Northwind (`docs/AUDITORIA.md`, sección 5), agregadas DE VERDAD en
`src/llm/safety_instacart.py`: LIMIT forzado, timeout real de consulta, y
conexión de solo lectura a nivel de motor. Cada test demuestra que la
guarda PROTEGE (con evidencia de que sin ella el escenario fallaría), no
solo que el código existe.
"""

from __future__ import annotations

import time

import duckdb
import pytest

from src.llm.safety_instacart import (
    JOIN_TRAS_INTERRUPT_S,
    TimeoutConsulta,
    abrir_conexion_solo_lectura,
    aplicar_limit,
    ejecutar_con_guardas,
)


# ---------------------------------------------------------------------------
# a. LIMIT forzado
# ---------------------------------------------------------------------------

def test_limit_se_agrega_si_falta():
    sql = aplicar_limit("SELECT * FROM orders", limite=1000)
    assert sql == "SELECT * FROM orders LIMIT 1000"


def test_limit_propio_mas_chico_se_respeta():
    sql = aplicar_limit("SELECT * FROM orders LIMIT 5", limite=1000)
    assert sql == "SELECT * FROM orders LIMIT 5"


def test_limit_propio_igual_al_maximo_se_respeta():
    sql = aplicar_limit("SELECT * FROM orders LIMIT 1000", limite=1000)
    assert sql == "SELECT * FROM orders LIMIT 1000"


def test_limit_propio_mas_grande_se_recorta():
    sql = aplicar_limit("SELECT * FROM orders LIMIT 50000", limite=1000)
    assert sql == "SELECT * FROM orders LIMIT 1000"


def test_limit_con_offset_se_respeta_si_es_chico():
    sql = aplicar_limit("SELECT * FROM orders LIMIT 5 OFFSET 10", limite=1000)
    assert sql == "SELECT * FROM orders LIMIT 5 OFFSET 10"


def test_limit_con_offset_se_recorta_si_excede():
    sql = aplicar_limit("SELECT * FROM orders LIMIT 50000 OFFSET 10", limite=1000)
    assert sql == "SELECT * FROM orders LIMIT 1000 OFFSET 10"


def test_ejecutar_con_guardas_aplica_el_limit_de_verdad(tmp_path):
    """No basta con que `aplicar_limit` exista: `ejecutar_con_guardas` debe
    USARLO antes de ejecutar. Se arma una tabla de 50 filas y se pide
    `SELECT *` SIN LIMIT -- sin la guarda, vendrían las 50; con la guarda
    (límite=10), deben venir como máximo 10."""
    db_path = tmp_path / "t.duckdb"
    con_prep = duckdb.connect(str(db_path))
    con_prep.execute("CREATE TABLE t AS SELECT range AS x FROM range(50)")
    con_prep.close()

    con = abrir_conexion_solo_lectura(str(db_path))
    try:
        _cols, filas = ejecutar_con_guardas(con, "SELECT * FROM t", limite_filas=10)
    finally:
        con.close()
    assert len(filas) == 10, f"se esperaban <=10 filas por el LIMIT forzado, vinieron {len(filas)}"


# ---------------------------------------------------------------------------
# b. Timeout real de consulta
# ---------------------------------------------------------------------------

def test_consulta_costosa_dispara_timeout_controlado(tmp_path):
    """Producto cartesiano grande (CROSS JOIN de una fixture chica repetida
    muchas veces) con una agregación que obliga a materializar TODO antes
    de poder aplicar el LIMIT (a diferencia de un SELECT * sin agregar, que
    DuckDB puede recortar con solo generar las primeras filas) -- debe
    dispararse TimeoutConsulta en un tiempo acotado, sin colgar el test ni
    tumbar el proceso."""
    db_path = tmp_path / "t.duckdb"
    con_prep = duckdb.connect(str(db_path))
    con_prep.execute("CREATE TABLE chica AS SELECT range AS x FROM range(4000)")
    con_prep.close()

    con = abrir_conexion_solo_lectura(str(db_path))
    sql_costoso = (
        "SELECT COUNT(*) FROM chica a CROSS JOIN chica b CROSS JOIN chica c "
        "WHERE a.x + b.x + c.x > 0"
    )
    t0 = time.perf_counter()
    try:
        with pytest.raises(TimeoutConsulta):
            ejecutar_con_guardas(con, sql_costoso, timeout_s=1.5)
    finally:
        duracion = time.perf_counter() - t0
        con.close()

    # El test entero (timeout + margen de interrupt) debe acotarse: prueba
    # de que la guarda de verdad corta la consulta, no que el test se
    # colgó y pytest lo mató por su cuenta.
    assert duracion < 1.5 + JOIN_TRAS_INTERRUPT_S + 5, (
        f"la consulta tardó {duracion:.1f}s en interrumpirse -- el timeout no está protegiendo de verdad"
    )


def test_consulta_rapida_no_dispara_timeout(tmp_path):
    db_path = tmp_path / "t.duckdb"
    con_prep = duckdb.connect(str(db_path))
    con_prep.execute("CREATE TABLE t AS SELECT range AS x FROM range(100)")
    con_prep.close()

    con = abrir_conexion_solo_lectura(str(db_path))
    try:
        _cols, filas = ejecutar_con_guardas(con, "SELECT COUNT(*) FROM t", timeout_s=10.0)
    finally:
        con.close()
    assert filas == [(100,)]


# ---------------------------------------------------------------------------
# c. Conexión de solo lectura REAL a nivel de motor (defensa en profundidad)
# ---------------------------------------------------------------------------

def test_conexion_solo_lectura_rechaza_escritura_aunque_el_validador_de_texto_se_salte(tmp_path):
    """Simula un validador de texto con un bug que deja pasar un INSERT
    (se lo pasa DIRECTO a la conexión, sin pasar por validar_sql): el
    MOTOR debe rechazarlo por sí mismo porque la conexión se abrió con
    read_only=True -- la segunda barrera no depende de la primera."""
    db_path = tmp_path / "t.duckdb"
    con_prep = duckdb.connect(str(db_path))
    con_prep.execute("CREATE TABLE orders (order_id INTEGER)")
    con_prep.execute("INSERT INTO orders VALUES (1)")
    con_prep.close()

    con = abrir_conexion_solo_lectura(str(db_path))
    try:
        with pytest.raises(duckdb.Error):
            con.execute("INSERT INTO orders VALUES (999999)")  # bypass deliberado del validador
        # Confirma que NO se escribió nada (no solo que lanzó una excepción).
        assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
    finally:
        con.close()


def test_conexion_solo_lectura_rechaza_drop_directo(tmp_path):
    db_path = tmp_path / "t.duckdb"
    con_prep = duckdb.connect(str(db_path))
    con_prep.execute("CREATE TABLE orders (order_id INTEGER)")
    con_prep.close()

    con = abrir_conexion_solo_lectura(str(db_path))
    try:
        with pytest.raises(duckdb.Error):
            con.execute("DROP TABLE orders")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# d. Intención de escritura (decide que NO haya reintento)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql,esperado", [
    ("DELETE FROM orders", True),
    ("DROP TABLE orders", True),
    ("SELECT 1; INSERT INTO orders VALUES (1)", True),
    ("SELECT replace(product_name, 'a', 'b') FROM products", False),  # función de texto
    ("SELECT deleted_flag FROM t", False),                             # no es palabra completa
    ("SELECT 1 -- drop table orders", False),                          # solo en comentario
    ("No puedo darte una consulta para eso", False),
])
def test_es_intento_de_escritura(sql, esperado):
    from src.llm.safety_instacart import es_intento_de_escritura
    assert es_intento_de_escritura(sql) is esperado
