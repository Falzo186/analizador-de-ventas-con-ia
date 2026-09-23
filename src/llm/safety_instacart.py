"""Guardas de seguridad de la ruta Instacart.

`docs/AUDITORIA.md` (sección 5) marcó que la ruta de Northwind solo tenía
"empieza con SELECT + blacklist de palabras + una sola sentencia + sin
commit" -- sin LIMIT forzado, sin timeout de verdad, y con una conexión de
solo lectura solo a nivel de SQLAlchemy (texto), no del motor. Este módulo
agrega las 3 que faltaban, DE VERDAD (con test que las prueba fallando si
se quitan, no solo que el código existe):

  a. LIMIT forzado (`aplicar_limit`)
  b. Timeout real de consulta (`ejecutar_con_guardas`, hilo + con.interrupt())
  c. Conexión de solo lectura a nivel de MOTOR (`abrir_conexion_solo_lectura`,
     duckdb.connect(..., read_only=True) -- DuckDB rechaza la escritura por
     sí mismo, no depende de que el validador de texto no tenga bugs)

La validación de texto ("una sola sentencia / empieza con SELECT /
blacklist": `validar_sql`, `es_seguro`, `PALABRAS_PROHIBIDAS`) venía de
`src.llm.safety` (ruta Northwind). En la Fase 7, al retirar Northwind a
`legacy/`, se trasladó aquí sin cambios para que la ruta Instacart no
importe nada archivado.
"""

from __future__ import annotations

import re
import threading

import duckdb


__all__ = [
    "PALABRAS_PROHIBIDAS", "SqlNoSeguro", "validar_sql", "es_seguro",
    "LIMITE_FILAS_DEFAULT", "TIMEOUT_S_DEFAULT", "JOIN_TRAS_INTERRUPT_S",
    "TimeoutConsulta", "aplicar_limit", "abrir_conexion_solo_lectura", "ejecutar_con_guardas",
    "es_intento_de_escritura",
]

# ---------------------------------------------------------------------------
# Validación de texto (trasladada sin cambios desde src/llm/safety.py, Fase 7)
# ---------------------------------------------------------------------------

#: Palabras que jamás deben aparecer en una consulta de solo lectura.
PALABRAS_PROHIBIDAS: tuple[str, ...] = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "REPLACE", "TRUNCATE", "MERGE", "UPSERT",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "GRANT",
)


class SqlNoSeguro(Exception):
    """El SQL propuesto por el modelo no pasó la validación."""


def _sin_comentarios(sql: str) -> str:
    """Quita comentarios -- ... y /* ... */ para que no escondan nada."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql


def validar_sql(sql: str) -> str:
    """Devuelve el SQL normalizado si es seguro; si no, lanza `SqlNoSeguro`."""
    if not sql or not sql.strip():
        raise SqlNoSeguro("La consulta está vacía.")

    limpio = _sin_comentarios(sql).strip()

    # Quitar como máximo un punto y coma final (una sentencia terminada).
    if limpio.endswith(";"):
        limpio = limpio[:-1].strip()

    # Cualquier punto y coma restante => hay más de una sentencia.
    if ";" in limpio:
        raise SqlNoSeguro("La consulta contiene más de una sentencia.")

    if not re.match(r"(?is)^select\b", limpio):
        raise SqlNoSeguro("La consulta debe empezar con SELECT.")

    for palabra in PALABRAS_PROHIBIDAS:
        if re.search(rf"(?i)\b{palabra}\b", limpio):
            raise SqlNoSeguro(f"Palabra prohibida en la consulta: {palabra}.")

    return limpio


def es_seguro(sql: str) -> bool:
    """Versión booleana de `validar_sql`, cómoda para condicionales/tests."""
    try:
        validar_sql(sql)
        return True
    except SqlNoSeguro:
        return False


# ---------------------------------------------------------------------------
# Guardas de ejecución
# ---------------------------------------------------------------------------

LIMITE_FILAS_DEFAULT = 1000
TIMEOUT_S_DEFAULT = 10.0
#: Cuánto esperar a que el hilo de ejecución realmente termine después de
#: pedirle a DuckDB que se interrumpa (con.interrupt() es una señal, no
#: instantánea) -- deliberadamente corto: si no termina en este margen
#: adicional, algo más grave está pasando y no vale la pena seguir esperando.
JOIN_TRAS_INTERRUPT_S = 5.0

# Un LIMIT al final de la sentencia (con OFFSET opcional después). Una sola
# sentencia SELECT ya lo garantiza `validar_sql()` antes de llegar aquí, así
# que "al final del texto" es "la cláusula LIMIT de la consulta completa".
_PATRON_LIMIT_FINAL = re.compile(r"(?is)\bLIMIT\s+(\d+)\s*(?:OFFSET\s+\d+\s*)?;?\s*$")


class TimeoutConsulta(Exception):
    """La consulta excedió TIMEOUT_S y se interrumpió antes de terminar."""


# Palabras de PALABRAS_PROHIBIDAS que delatan una INTENCIÓN de escribir o
# tocar el esquema. Se excluye REPLACE: en DuckDB también es una función de
# texto (`SELECT replace(product_name, ...)`), no solo `INSERT OR REPLACE`.
_PALABRAS_DE_ESCRITURA = tuple(p for p in PALABRAS_PROHIBIDAS if p != "REPLACE")
_PATRON_ESCRITURA = re.compile(r"(?i)\b(?:" + "|".join(_PALABRAS_DE_ESCRITURA) + r")\b")


def es_intento_de_escritura(sql: str) -> bool:
    """True si `sql` contiene una palabra de escritura/esquema (DELETE,
    DROP, INSERT...). La usa el reintento de `responder_instacart`: si el
    modelo quiso escribir, la petición se rechaza y NO se reintenta -- un
    reintento pidiendo "solo SELECT" convertiría "borra los pedidos" en
    una lectura que el usuario no pidió."""
    return bool(_PATRON_ESCRITURA.search(_sin_comentarios(sql or "")))


def aplicar_limit(sql: str, limite: int = LIMITE_FILAS_DEFAULT) -> str:
    """Fuerza que `sql` traiga un LIMIT <= `limite`.

    - Sin LIMIT propio: se agrega ` LIMIT {limite}` al final.
    - Con LIMIT propio <= `limite`: se respeta tal cual (el modelo puede
      pedir menos filas si eso responde mejor la pregunta).
    - Con LIMIT propio > `limite`: se recorta a `limite`.
    """
    sql_limpio = sql.rstrip().rstrip(";").rstrip()
    coincidencia = _PATRON_LIMIT_FINAL.search(sql_limpio)
    if not coincidencia:
        return f"{sql_limpio} LIMIT {limite}"

    limite_propio = int(coincidencia.group(1))
    if limite_propio <= limite:
        return sql_limpio

    inicio, fin = coincidencia.span(1)
    return sql_limpio[:inicio] + str(limite) + sql_limpio[fin:]


def abrir_conexion_solo_lectura(db_path: str) -> duckdb.DuckDBPyConnection:
    """Conexión de solo lectura REAL a nivel de motor: `read_only=True` hace
    que DuckDB rechace cualquier sentencia de escritura por sí mismo (ver
    `tests/test_safety_instacart.py`, defensa en profundidad), sin depender
    de que `validar_sql()` no tenga un bug que deje pasar algo."""
    return duckdb.connect(str(db_path), read_only=True)


def _ejecutar_en_hilo(con: duckdb.DuckDBPyConnection, sql: str, resultado: dict) -> None:
    try:
        cursor = con.execute(sql)
        columnas = [d[0] for d in cursor.description] if cursor.description else []
        resultado["columnas"] = columnas
        resultado["filas"] = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001 - se re-lanza tal cual en el hilo principal
        resultado["error"] = exc


def ejecutar_con_guardas(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    *,
    limite_filas: int = LIMITE_FILAS_DEFAULT,
    timeout_s: float = TIMEOUT_S_DEFAULT,
) -> tuple[list[str], list[tuple]]:
    """Ejecuta `sql` (ya validado por `validar_sql()`) con las 3 guardas:
    LIMIT forzado, timeout real (corre en un hilo aparte; si no termina a
    tiempo, `con.interrupt()` le pide a DuckDB que pare) y `con` debe venir
    de `abrir_conexion_solo_lectura()`. Lanza `TimeoutConsulta` si se
    excede `timeout_s`; cualquier otro error de ejecución (tabla/columna
    inexistente, tipo incompatible...) se re-lanza tal cual."""
    sql_con_limit = aplicar_limit(sql, limite_filas)

    resultado: dict = {}
    hilo = threading.Thread(target=_ejecutar_en_hilo, args=(con, sql_con_limit, resultado), daemon=True)
    hilo.start()
    hilo.join(timeout_s)

    if hilo.is_alive():
        con.interrupt()
        hilo.join(JOIN_TRAS_INTERRUPT_S)
        raise TimeoutConsulta(f"La consulta excedió el límite de {timeout_s:.0f}s y se interrumpió.")

    if "error" in resultado:
        raise resultado["error"]
    return resultado["columnas"], resultado["filas"]
