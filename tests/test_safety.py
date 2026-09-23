"""Pruebas de la validación de seguridad del SQL.

Usan SQL fijo escrito a mano: NO llaman al modelo real, así que pasan aunque
Ollama no esté corriendo.
"""

from __future__ import annotations

import pytest

from src.llm.nl2sql_instacart import limpiar_sql
from src.llm.safety_instacart import SqlNoSeguro, es_seguro, validar_sql

SQL_ESCRITURA = [
    "DELETE FROM ventas",
    "INSERT INTO productos (nombre, categoria, precio_base) VALUES ('x', 'ropa', 1)",
    "UPDATE ventas SET total = 0",
    "DROP TABLE ventas",
    "ALTER TABLE ventas ADD COLUMN hackeado INTEGER",
    "SELECT 1; DROP TABLE ventas",          # dos sentencias apiladas
    "SELECT * FROM ventas; DELETE FROM ventas;",
    "SELECT 1 /* comentario */ ; DROP TABLE ventas",
    "",                                     # vacío
    "   ",                                  # solo espacios
]


@pytest.mark.parametrize("sql", SQL_ESCRITURA)
def test_validar_sql_rechaza_escritura_y_apilado(sql):
    with pytest.raises(SqlNoSeguro):
        validar_sql(sql)
    assert es_seguro(sql) is False


@pytest.mark.parametrize("sql", [
    "SELECT SUM(cantidad) FROM ventas WHERE categoria = 'tecnología'",
    "select count(*) from ventas where strftime('%Y-%m', fecha) = '2026-08';",
    "SELECT categoria, SUM(total) FROM ventas GROUP BY categoria ORDER BY 2 DESC",
])
def test_validar_sql_acepta_selects_de_solo_lectura(sql):
    normalizado = validar_sql(sql)
    assert normalizado.lower().startswith("select")
    assert ";" not in normalizado          # se quita el punto y coma final


def test_limpiar_sql_quita_fences_y_backticks():
    crudo = "```sql\nSELECT SUM(cantidad) FROM ventas\n```"
    assert limpiar_sql(crudo) == "SELECT SUM(cantidad) FROM ventas"
    assert limpiar_sql("`SELECT 1`") == "SELECT 1"
    assert limpiar_sql("Aquí está:\nSELECT 1;") == "SELECT 1"
