"""Pruebas del evaluador.

Las de comparación son puras. La de `evaluar_caso` reemplaza el modelo por un
doble, así que no necesita Ollama.
"""

from __future__ import annotations

from src.evaluator import comparar as C
from src.evaluator.banco import BANCO, Caso


# --------------------------------------------------------------- comparación

def test_numeros_de_texto_maneja_separadores():
    nums = C.numeros_de_texto("Se facturaron $12,345.67 en 342 ventas")
    assert any(abs(n - 12345.67) < 0.01 for n in nums)
    assert 342.0 in nums


def test_coincide_numerico_con_tolerancia():
    # conteo (entero): coincidencia exacta
    assert C.coincide_numerico([197], C.numeros_de_texto("Fueron 197 artículos."))
    assert not C.coincide_numerico([197], C.numeros_de_texto("Fueron 198 artículos."))
    # dinero (float): 1% de tolerancia absorbe el redondeo
    assert C.coincide_numerico([10000.0], [10050.0])
    assert not C.coincide_numerico([10000.0], [10200.0])


def test_coincide_numerico_exige_todos_los_numeros():
    assert not C.coincide_numerico([10.0, 20.0], C.numeros_de_texto("solo veo 10"))


def test_coincide_texto_ignora_acentos_y_mayusculas():
    assert C.coincide_texto(["tecnologia"], "La categoría top fue Tecnología.")
    assert not C.coincide_texto(["ropa"], "La categoría top fue Tecnología.")


def test_coincide_texto_reconoce_categoria_traducida():
    # 'Beverages' es el valor real en Categories.CategoryName; el modelo
    # responde en español y traduce a 'Bebidas' — debe seguir contando como
    # acierto (antes de la normalización, este caso daba falso negativo).
    assert C.coincide_texto(["beverages"], "La categoría con más ingresos es Bebidas.")
    assert C.coincide_texto(["dairy products"], "La categoría es Productos Lácteos.")
    assert not C.coincide_texto(["beverages"], "La categoría con más ingresos es Condimentos.")


def test_coincide_texto_reconoce_pais_traducido():
    assert C.coincide_texto(["usa"], "El país que concentra más clientes es Estados Unidos.")
    assert C.coincide_texto(["uk"], "El país es el Reino Unido.")
    assert C.coincide_texto(["germany"], "El país con más clientes es Alemania.")
    assert not C.coincide_texto(["usa"], "El país que concentra más clientes es Venezuela.")


def test_coincide_texto_sigue_aceptando_el_valor_original_en_ingles():
    # Si el modelo NO traduce (deja 'USA' o 'Beverages' tal cual), debe
    # seguir contando como acierto: la normalización solo AÑADE variantes.
    assert C.coincide_texto(["usa"], "El país con más clientes es USA.")
    assert C.coincide_texto(["beverages"], "La categoría es Beverages.")


def test_expresa_vacio_o_cero_reconoce_none_en_ingles():
    assert C.expresa_vacio_o_cero("There are none.")
    assert C.expresa_vacio_o_cero("No hay clientes en esa situación.")


def test_numeros_de_filas_ignora_texto_y_bool():
    assert C.numeros_de_filas([("alimentos", 342), ("ropa", 88)]) == [342, 88]


# ------------------------------------------------------------------- banco

def test_banco_tiene_suficientes_casos_y_campos():
    # 10 necesidades de negocio sobre Northwind (ver docs/arquitectura.md,
    # "Migración a datos reales").
    assert len(BANCO) == 10
    for c in BANCO:
        assert c.pregunta and c.sql_referencia and c.categoria_pregunta
        assert c.sql_referencia.lstrip().lower().startswith("select")


def test_banco_referencia_order_details_con_comillas():
    # "Order Details" tiene un espacio en el nombre: cualquier caso que la
    # use debe referenciarla entre comillas dobles.
    for c in BANCO:
        if "Order Details" in c.sql_referencia:
            assert '"Order Details"' in c.sql_referencia, c.pregunta


# --------------------------------------------------------------- evaluar_caso

def test_evaluar_caso_acierto_con_modelo_simulado(monkeypatch):
    from src.generator.seed import sembrar_productos
    from src.llm import client, responder

    sembrar_productos()
    caso = Caso("¿cuántos productos hay?", "SELECT COUNT(*) FROM productos", "conteo")

    respuestas = iter(["SELECT COUNT(*) FROM productos", "Hay 54 productos."])
    monkeypatch.setattr(client, "chat", lambda *a, **k: next(respuestas))

    from src.evaluator.evaluador import evaluar_caso

    r = evaluar_caso(caso)
    assert r.acierto is True
    assert r.motivo_fallo is None


def test_evaluar_caso_detecta_fallo_de_redaccion(monkeypatch):
    from src.generator.seed import sembrar_productos
    from src.llm import client

    sembrar_productos()
    caso = Caso("¿cuántos productos hay?", "SELECT COUNT(*) FROM productos", "conteo")

    # SQL correcto, pero la frase final dice un número equivocado.
    respuestas = iter(["SELECT COUNT(*) FROM productos", "Hay 999 productos."])
    monkeypatch.setattr(client, "chat", lambda *a, **k: next(respuestas))

    from src.evaluator.evaluador import evaluar_caso

    r = evaluar_caso(caso)
    assert r.acierto is False
    assert r.motivo_fallo == "redaccion"


def test_verdad_cero_acepta_respuesta_en_palabras(monkeypatch):
    from src.generator.seed import sembrar_productos
    from src.llm import client

    sembrar_productos()
    caso = Caso(
        "¿cuántos productos hay en la categoría 'electrónica'?",
        "SELECT COUNT(*) FROM productos WHERE categoria = 'electrónica'",
        "ambigua",
    )
    respuestas = iter([
        "SELECT COUNT(*) FROM productos WHERE categoria = 'electrónica'",
        "No se registraron productos en esa categoría.",
    ])
    monkeypatch.setattr(client, "chat", lambda *a, **k: next(respuestas))

    from src.evaluator.evaluador import evaluar_caso

    r = evaluar_caso(caso)
    assert r.acierto is True


def test_verdad_cero_penaliza_numero_inventado(monkeypatch):
    from src.generator.seed import sembrar_productos
    from src.llm import client

    sembrar_productos()
    caso = Caso(
        "¿cuántos productos hay en la categoría 'electrónica'?",
        "SELECT COUNT(*) FROM productos WHERE categoria = 'electrónica'",
        "ambigua",
    )
    # SQL correcto (0) pero la frase final inventa un número.
    respuestas = iter([
        "SELECT COUNT(*) FROM productos WHERE categoria = 'electrónica'",
        "Hay 14 productos en esa categoría.",
    ])
    monkeypatch.setattr(client, "chat", lambda *a, **k: next(respuestas))

    from src.evaluator.evaluador import evaluar_caso

    r = evaluar_caso(caso)
    assert r.acierto is False
    assert r.motivo_fallo == "redaccion"


def test_evaluar_caso_detecta_sql_incorrecto(monkeypatch):
    from src.generator.seed import sembrar_productos
    from src.llm import client

    sembrar_productos()
    caso = Caso("¿cuántos productos hay?", "SELECT COUNT(*) FROM productos", "conteo")

    # El modelo cuenta otra cosa -> datos distintos a la verdad.
    respuestas = iter([
        "SELECT COUNT(*) FROM productos WHERE categoria = 'ropa'",
        "Hay 13 productos.",
    ])
    monkeypatch.setattr(client, "chat", lambda *a, **k: next(respuestas))

    from src.evaluator.evaluador import evaluar_caso

    r = evaluar_caso(caso)
    assert r.acierto is False
    assert r.motivo_fallo == "sql"


def test_modo_texto_acierta_por_la_frase_aunque_el_sql_tenga_otra_forma(monkeypatch):
    """Pregunta de etiqueta: si la frase nombra lo correcto, es acierto aunque
    el SQL del modelo devuelva la etiqueta con otro formato."""
    from src.generator.seed import sembrar_productos
    from src.llm import client

    sembrar_productos()
    caso = Caso(
        "¿qué categoría tiene el producto más caro?",
        "SELECT categoria FROM productos ORDER BY precio_base DESC LIMIT 1",
        "top-n",
        modo="texto",
    )
    # El SQL del modelo trae (categoria, precio) en vez de solo categoria,
    # pero la frase final nombra 'tecnología' que es lo correcto.
    respuestas = iter([
        "SELECT categoria, MAX(precio_base) FROM productos",
        "La categoría con el producto más caro es tecnología.",
    ])
    monkeypatch.setattr(client, "chat", lambda *a, **k: next(respuestas))

    from src.evaluator.evaluador import evaluar_caso

    r = evaluar_caso(caso)
    assert r.acierto is True


def test_modo_texto_falla_si_la_frase_nombra_lo_incorrecto(monkeypatch):
    from src.generator.seed import sembrar_productos
    from src.llm import client

    sembrar_productos()
    caso = Caso(
        "¿qué categoría tiene el producto más caro?",
        "SELECT categoria FROM productos ORDER BY precio_base DESC LIMIT 1",
        "top-n",
        modo="texto",
    )
    respuestas = iter([
        "SELECT categoria FROM productos ORDER BY precio_base ASC LIMIT 1",
        "La categoría con el producto más caro es alimentos.",
    ])
    monkeypatch.setattr(client, "chat", lambda *a, **k: next(respuestas))

    from src.evaluator.evaluador import evaluar_caso

    r = evaluar_caso(caso)
    assert r.acierto is False
    assert r.motivo_fallo == "sql"
