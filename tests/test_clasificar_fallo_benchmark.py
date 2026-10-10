"""Clasificación de fallos del benchmark de modelos LLM (mejora pedida por
el profesor Eder Jahir González Bravo, ver docs/CLASIFICACION_FALLOS.md):
no basta con "acertó/falló", hay que distinguir error de EJECUCIÓN (el
motor rechazó el SQL: sintaxis, tabla/columna inexistente, agregación mal
formada) de error SEMÁNTICO (el SQL corrió pero no responde la pregunta).

Los mensajes de error usados aquí NO son inventados: son los que ya
salieron de verdad al correr este proyecto contra DuckDB 1.5.5 (ver
docs/CLASIFICACION_FALLOS.md para el origen de cada uno).
"""

from __future__ import annotations

from scripts.benchmark_modelos_llm import (
    CATEGORIAS_FALLO,
    ResultadoModelo,
    ResultadoPregunta,
    clasificar_fallo,
)


def _pregunta(**kwargs) -> ResultadoPregunta:
    base = dict(pregunta="¿pregunta de prueba?", categoria="simple")
    base.update(kwargs)
    return ResultadoPregunta(**base)


# ---------------------------------------------------------------------------
# Sin fallo
# ---------------------------------------------------------------------------

def test_sin_fallo_cuando_el_sql_corrio_bien_y_no_es_sospechoso():
    r = _pregunta(sql="SELECT COUNT(*) FROM orders", sql_valido=True, n_filas=1)
    assert clasificar_fallo(r) is None


def test_sin_fallo_cuando_no_hay_sql_ni_error():
    # Caso borde: no debería pasar en la práctica, pero clasificar_fallo no
    # debe inventar una categoría si no hay ni SQL ni error.
    r = _pregunta(sql=None, error_sql=None)
    assert clasificar_fallo(r) is None


# ---------------------------------------------------------------------------
# tabla_o_columna_inexistente
# ---------------------------------------------------------------------------

def test_tabla_o_columna_inexistente_por_lista_de_tablas_desconocidas():
    r = _pregunta(
        sql="SELECT * FROM pedidos_fantasma",
        error_sql="Catalog Error: Table with name pedidos_fantasma does not exist!",
        tablas_desconocidas=["pedidos_fantasma"],
    )
    assert clasificar_fallo(r) == "tabla_o_columna_inexistente"


def test_tabla_o_columna_inexistente_con_mensaje_real_del_proyecto():
    # Mensaje real visto en este proyecto al pedir N2 (canasta de productos):
    # el modelo usó op1.eval_set, pero eval_set es de `orders`, no de
    # `order_products` (alias op1/op2).
    r = _pregunta(
        sql="SELECT ... WHERE op1.eval_set = 'prior' AND op2.eval_set = 'prior'",
        error_sql='Binder Error: Values list "op1" does not have a column named "eval_set"',
    )
    assert clasificar_fallo(r) == "tabla_o_columna_inexistente"


def test_tabla_o_columna_inexistente_con_mensaje_real_de_tabla_referenciada():
    # Mensaje real visto en la corrida de la Fase 11c (pregunta: "qué
    # departamento tiene más líneas reordenadas"): el modelo usó
    # `departments` sin hacer el JOIN que la trae al alcance del SELECT.
    # Esta forma del mensaje ("Referenced table", no "column") caía antes
    # en "otro" -- el patrón no la cubría.
    r = _pregunta(
        sql="SELECT departments.department ... FROM order_products op ...",
        error_sql='Binder Error: Referenced table "departments" not found! Candidate tables: "p", "op_train"',
    )
    assert clasificar_fallo(r) == "tabla_o_columna_inexistente"


# ---------------------------------------------------------------------------
# error_sintaxis
# ---------------------------------------------------------------------------

def test_error_sintaxis_con_parser_error():
    r = _pregunta(sql="SELECT * FORM orders", error_sql='Parser Error: syntax error at or near "FORM"')
    assert clasificar_fallo(r) == "error_sintaxis"


# ---------------------------------------------------------------------------
# agregacion_incorrecta (error de EJECUCIÓN: el motor lo rechazó)
# ---------------------------------------------------------------------------

def test_agregacion_incorrecta_con_mensaje_real_del_proyecto():
    # Mensaje real visto en este proyecto al pedir N11 (predicción del
    # siguiente pedido): seleccionó product_id junto a COUNT/SUM sin
    # agruparlo.
    r = _pregunta(
        sql="SELECT op.product_id, COUNT(*), SUM(...) FROM order_products op",
        error_sql=(
            'Binder Error: column "product_id" must appear in the GROUP BY '
            "clause or be used in an aggregate function. Either add it to "
            'the GROUP BY list, or use "ANY_VALUE(product_id)" if the exact '
            'value of "product_id" is not important.'
        ),
    )
    assert clasificar_fallo(r) == "agregacion_incorrecta"


# ---------------------------------------------------------------------------
# agregacion_incorrecta_heuristica (error SEMÁNTICO: corrió bien, pero mal)
# ---------------------------------------------------------------------------

def test_agregacion_incorrecta_heuristica_agrupa_por_order_id():
    # Mismo patrón real documentado en N9_ia.json de este proyecto: el
    # modelo agrupó por order_id (una fila casi por pedido) en vez de
    # calcular la distribución agregada que pide la pregunta de N9.
    r = _pregunta(
        sql=(
            "SELECT COUNT(*), AVG(op.add_to_cart_order), AVG(op.reordered) "
            "FROM orders INNER JOIN order_products AS op "
            "ON orders.order_id = op.order_id GROUP BY orders.order_id"
        ),
        sql_valido=True,
        n_filas=3346083,
    )
    assert clasificar_fallo(r) == "agregacion_incorrecta_heuristica"


def test_sin_fallo_heuristico_si_agrupa_por_id_pero_sin_agregado():
    # GROUP BY sobre un id sin ninguna función de agregado no es el patrón
    # sospechoso (p. ej. un SELECT DISTINCT disfrazado de GROUP BY).
    r = _pregunta(sql="SELECT order_id FROM orders GROUP BY order_id", sql_valido=True, n_filas=3421083)
    assert clasificar_fallo(r) is None


def test_sin_fallo_heuristico_si_agrupa_por_id_para_un_top_n_legitimo():
    # Falso positivo real, encontrado en la corrida de la Fase 11c:
    # "¿Cuál es el producto con más líneas de pedido?" agrupa por
    # product_id a propósito -- es la forma correcta de responder un "el
    # más X" o un "top N". Antes de este fix, cualquier GROUP BY sobre un
    # id con un agregado se marcaba como fallo, sin importar cuántas filas
    # trajera de vuelta.
    r = _pregunta(
        sql=(
            "SELECT product_id, COUNT(*) AS n FROM order_products "
            "GROUP BY product_id ORDER BY n DESC LIMIT 1"
        ),
        sql_valido=True,
        n_filas=1,
    )
    assert clasificar_fallo(r) is None


def test_sin_fallo_heuristico_si_agrupa_por_id_para_top_5():
    # Mismo falso positivo real, segunda pregunta: "top 5 de productos con
    # más veces reordenados".
    r = _pregunta(
        sql=(
            "SELECT product_id, SUM(reordered) AS n FROM order_products "
            "GROUP BY product_id ORDER BY n DESC LIMIT 5"
        ),
        sql_valido=True,
        n_filas=5,
    )
    assert clasificar_fallo(r) is None


def test_heuristica_respeta_el_umbral_de_filas():
    # Justo en el umbral: no debe marcarse (el umbral es un mínimo
    # estricto, UMBRAL_FILAS_AGREGACION_SOSPECHOSA=50).
    from scripts.benchmark_modelos_llm import UMBRAL_FILAS_AGREGACION_SOSPECHOSA

    sql = "SELECT user_id, COUNT(*) FROM orders GROUP BY user_id"
    en_el_umbral = _pregunta(sql=sql, sql_valido=True, n_filas=UMBRAL_FILAS_AGREGACION_SOSPECHOSA)
    assert clasificar_fallo(en_el_umbral) is None

    justo_arriba = _pregunta(sql=sql, sql_valido=True, n_filas=UMBRAL_FILAS_AGREGACION_SOSPECHOSA + 1)
    assert clasificar_fallo(justo_arriba) == "agregacion_incorrecta_heuristica"


def test_sin_fallo_heuristico_si_agrupa_por_columna_de_negocio():
    # Agrupar por una columna que NO es un identificador (aisle_id es
    # borderline, pero department SÍ es una columna de negocio legítima
    # para agregar) no debe marcarse como sospechoso.
    r = _pregunta(
        sql="SELECT department, AVG(price) FROM products GROUP BY department",
        sql_valido=True,
        n_filas=21,
    )
    assert clasificar_fallo(r) is None


# ---------------------------------------------------------------------------
# otro
# ---------------------------------------------------------------------------

def test_otro_cuando_el_error_no_encaja_en_ninguna_categoria_conocida():
    r = _pregunta(sql="SELECT * FROM orders", error_sql="Out of Memory Error: failed to allocate block")
    assert clasificar_fallo(r) == "otro"


def test_otro_cuando_no_hubo_sql_pero_si_hubo_error():
    # El modelo ni siquiera devolvió una consulta (p. ej. timeout del LLM
    # en la llamada 1): no hay SQL que inspeccionar, pero sí hubo fallo.
    r = _pregunta(sql=None, error_sql="LlmTimeout: se agotó el tiempo de espera")
    assert clasificar_fallo(r) == "otro"


# ---------------------------------------------------------------------------
# Las preguntas trampa no se clasifican aquí (usan rechazo_correcto)
# ---------------------------------------------------------------------------

def test_categoria_fallo_no_se_asigna_a_preguntas_trampa():
    # _correr_pregunta solo llama a clasificar_fallo() para categorías
    # distintas de trampa_dinero/trampa_escritura; este test documenta esa
    # regla fijando el contrato de datos: una trampa nunca debería traer
    # categoria_fallo si nadie se lo asignó explícitamente.
    r = _pregunta(categoria="trampa_dinero", rechazo_correcto=True)
    assert r.categoria_fallo is None


# ---------------------------------------------------------------------------
# ResultadoModelo.resumen_fallos
# ---------------------------------------------------------------------------

def test_resumen_fallos_cuenta_por_categoria_y_omite_las_vacias():
    preguntas = [
        _pregunta(categoria="simple", categoria_fallo="tabla_o_columna_inexistente"),
        _pregunta(categoria="simple", categoria_fallo="tabla_o_columna_inexistente"),
        _pregunta(categoria="join", categoria_fallo="agregacion_incorrecta"),
        _pregunta(categoria="join", categoria_fallo=None),  # sin fallo
        _pregunta(categoria="trampa_dinero", rechazo_correcto=True),  # no cuenta
    ]
    modelo = ResultadoModelo(modelo="qwen2.5-coder:1.5b", preguntas=preguntas, rss_ollama_max_kb=0, duracion_total_s=1.0)
    assert modelo.resumen_fallos == {
        "tabla_o_columna_inexistente": 2,
        "agregacion_incorrecta": 1,
    }


def test_resumen_fallos_vacio_cuando_nadie_fallo():
    preguntas = [_pregunta(categoria="simple", categoria_fallo=None)]
    modelo = ResultadoModelo(modelo="m", preguntas=preguntas, rss_ollama_max_kb=0, duracion_total_s=1.0)
    assert modelo.resumen_fallos == {}


def test_categorias_fallo_cubre_todas_las_que_puede_devolver_clasificar_fallo():
    # Defensa contra que alguien agregue una categoría nueva en
    # clasificar_fallo() y se le olvide registrarla en CATEGORIAS_FALLO
    # (entonces resumen_fallos la descartaría en silencio). IMPORTANTE:
    # cada caso trae `sql` -- sin él, clasificar_fallo() devuelve "otro" de
    # inmediato (rama `r.sql is None`) sin mirar siquiera `error_sql`, así
    # que un caso sin `sql` no prueba nada sobre su categoría pretendida
    # (bug de la primera versión de este test, que SÍ pasaba mientras los 5
    # casos colapsaban a "otro" sin que nadie lo notara).
    casos = {
        "tabla_o_columna_inexistente": _pregunta(
            sql="SELECT * FROM x", tablas_desconocidas=["x"], error_sql="Catalog Error: ...",
        ),
        "error_sintaxis": _pregunta(
            sql="SELECT * FORM orders", error_sql='Parser Error: syntax error at or near "X"',
        ),
        "agregacion_incorrecta": _pregunta(
            sql="SELECT product_id, COUNT(*) FROM order_products",
            error_sql='Binder Error: column "x" must appear in the GROUP BY clause',
        ),
        "agregacion_incorrecta_heuristica": _pregunta(
            sql="SELECT COUNT(*) FROM orders o GROUP BY o.order_id", sql_valido=True, n_filas=200,
        ),
        "otro": _pregunta(sql="SELECT 1", error_sql="Out of Memory Error: ..."),
    }
    for esperada, r in casos.items():
        assert clasificar_fallo(r) == esperada, f"caso '{esperada}' dio {clasificar_fallo(r)!r}"

    categorias_obtenidas = {clasificar_fallo(r) for r in casos.values()}
    assert categorias_obtenidas == set(CATEGORIAS_FALLO)  # las 5, ni de más ni de menos
