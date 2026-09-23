"""Prompts de la ruta Instacart (Fase 5): misma forma que los de Northwind,
contenido propio -- sin fechas inventadas, esquema recibido como parámetro.
"""

from __future__ import annotations

from src.llm.prompts_instacart import prompt_respuesta_instacart, prompt_sql_instacart


def test_prompt_sql_devuelve_sistema_y_usuario():
    sistema, usuario = prompt_sql_instacart("¿cuántos pedidos hay?", "ESQUEMA DE PRUEBA")
    assert isinstance(sistema, str) and isinstance(usuario, str)
    assert usuario == "¿cuántos pedidos hay?"


def test_prompt_sql_incluye_la_descripcion_de_esquema_recibida():
    marca = "TABLA-DE-PRUEBA-UNICA-12345"
    sistema, _ = prompt_sql_instacart("pregunta", marca)
    assert marca in sistema


def test_prompt_sql_prohibe_escritura_y_markdown():
    sistema, _ = prompt_sql_instacart("pregunta", "esquema")
    bajo = sistema.lower()
    assert "insert" in bajo and "delete" in bajo and "drop" in bajo
    assert "```" in sistema  # instruye explícitamente no usar markdown


def test_prompt_sql_prohibe_asumir_fecha_o_calendario():
    sistema, _ = prompt_sql_instacart("pregunta", "esquema")
    bajo = sistema.lower()
    assert "fecha" in bajo
    assert "no asumas" in bajo or "no tiene fechas reales" in bajo


def test_prompt_sql_solo_menciona_northwind_para_descartarlo():
    sistema, _ = prompt_sql_instacart("pregunta", "esquema")
    # El prompt SÍ nombra tablas de Northwind, mas solo dentro de la frase
    # que las descarta explícitamente ("ignora... NADA de eso existe
    # aquí"); no deben aparecer sueltas en ningún otro punto del prompt.
    assert sistema.count("Order Details") == 1
    assert sistema.count("Customers") == 1
    inicio = sistema.index("ignora cualquier nombre de tabla")
    fin = sistema.index("NADA de eso existe aquí") + len("NADA de eso existe aquí")
    for nombre in ("Order Details", "Customers", "Employees", "Suppliers", "Categories"):
        pos = sistema.index(nombre)
        assert inicio <= pos <= fin, f"'{nombre}' aparece fuera de la frase de descarte de Northwind"


def test_prompt_respuesta_incluye_pregunta_y_resultado():
    sistema, usuario = prompt_respuesta_instacart("¿cuántos pedidos hay?", "n\n3421083")
    assert "¿cuántos pedidos hay?" in usuario
    assert "3421083" in usuario
    assert isinstance(sistema, str)


def test_prompt_respuesta_instruye_rechazar_dinero_sin_inventar():
    sistema, _ = prompt_respuesta_instacart("pregunta", "resultado")
    bajo = sistema.lower()
    assert "dinero" in bajo or "precio" in bajo
    assert "no inventes" in bajo or "no la inventes" in bajo


def test_prompt_respuesta_no_menciona_sql_ni_tablas():
    sistema, _ = prompt_respuesta_instacart("pregunta", "resultado")
    assert "no menciones sql" in sistema.lower()


def test_prompt_de_reintento_lleva_pregunta_sql_anterior_y_motivo_exacto():
    from src.llm.prompts_instacart import prompt_sql_instacart, prompt_sql_reintento_instacart

    sistema, usuario = prompt_sql_reintento_instacart(
        "¿Qué pasillo tiene más productos?", "ESQUEMA X",
        "SELECT aisle FROM products", 'BinderException: Referenced column "aisle" not found',
    )
    assert sistema == prompt_sql_instacart("x", "ESQUEMA X")[0]
    assert usuario.startswith("¿Qué pasillo tiene más productos?")
    assert "SELECT aisle FROM products" in usuario
    assert 'rechazada porque: BinderException: Referenced column "aisle" not found' in usuario
    assert usuario.rstrip().endswith("Genera solo una sentencia SELECT válida sobre las tablas reales.")
