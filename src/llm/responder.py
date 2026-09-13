"""Orquestador: pregunta en español -> SQL -> resultado -> respuesta natural.

Flujo completo:

  1. generar_sql()      (llamada 1 al modelo)      -> texto SQL
  2. validar_sql()      (sin modelo, safety.py)    -> SQL seguro o error
  3. ejecutar_select()  (SQLAlchemy, solo lectura) -> filas
  4. redactar_respuesta() (llamada 2 al modelo)    -> frase en español

Cada paso está envuelto en try/except: si algo falla (Ollama apagado, SQL
inválido, SQL peligroso...), se devuelve un `Resultado` con `error` y el
programa sigue vivo.

CLI:
  python -m src.llm.responder "¿cuántos artículos de tecnología se vendieron en agosto?"
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from sqlalchemy import text

from src.db.database import crear_base_de_datos, engine
from src.llm import client
from src.llm.nl2sql import generar_sql
from src.llm.prompts import prompt_respuesta
from src.llm.safety import SqlNoSeguro, validar_sql

MAX_FILAS_AL_MODELO = 25


@dataclass
class Resultado:
    pregunta: str
    sql: str | None = None
    columnas: list[str] | None = None
    filas: list[tuple] | None = None
    respuesta: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def ejecutar_select(sql: str) -> tuple[list[str], list[tuple]]:
    """Ejecuta un SELECT ya validado y devuelve (columnas, filas).

    Usamos engine.connect() (sin transacción de escritura): aunque por algún
    hueco pasara un comando de escritura, no se haría commit. Es una segunda
    barrera además de safety.py.
    """
    with engine.connect() as conn:
        resultado = conn.execute(text(sql))
        columnas = list(resultado.keys())
        filas = [tuple(fila) for fila in resultado.fetchall()]
    return columnas, filas


def _tabla_a_texto(columnas: list[str], filas: list[tuple]) -> str:
    if not filas:
        return "(sin resultados)"
    lineas = [" | ".join(map(str, columnas))]
    for fila in filas[:MAX_FILAS_AL_MODELO]:
        lineas.append(" | ".join("" if v is None else str(v) for v in fila))
    if len(filas) > MAX_FILAS_AL_MODELO:
        lineas.append(f"... ({len(filas) - MAX_FILAS_AL_MODELO} filas más)")
    return "\n".join(lineas)


def redactar_respuesta(pregunta: str, columnas: list[str], filas: list[tuple]) -> str:
    """Llamada 2: convierte el resultado tabular en una frase en español."""
    sistema, usuario = prompt_respuesta(pregunta, _tabla_a_texto(columnas, filas))
    return client.chat(sistema, usuario).strip()


def responder_pregunta(pregunta: str, redactar: bool = True) -> Resultado:
    """Ejecuta el flujo completo y SIEMPRE devuelve un `Resultado`."""
    res = Resultado(pregunta=pregunta)

    # 1. texto -> SQL
    try:
        res.sql = generar_sql(pregunta)
    except Exception as exc:  # noqa: BLE001
        res.error = f"No se pudo generar el SQL: {exc}"
        return res

    # 2. validación de seguridad (sin modelo)
    try:
        res.sql = validar_sql(res.sql)
    except SqlNoSeguro as exc:
        res.error = f"SQL rechazado por seguridad: {exc}"
        return res

    # 3. ejecutar
    try:
        res.columnas, res.filas = ejecutar_select(res.sql)
    except Exception as exc:  # noqa: BLE001 - SQL mal formado, columna inexistente...
        res.error = f"Error al ejecutar el SQL: {exc}"
        return res

    # 4. resultado -> español (opcional; si falla, no invalida el resto)
    if redactar:
        try:
            res.respuesta = redactar_respuesta(pregunta, res.columnas, res.filas)
        except Exception as exc:  # noqa: BLE001
            res.respuesta = None
            res.error = f"Se obtuvo el resultado pero no se pudo redactar: {exc}"

    return res


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _imprimir(res: Resultado) -> None:
    print(f"Pregunta:  {res.pregunta}")
    print(f"SQL:       {res.sql or '(no generado)'}")

    if res.filas is not None:
        tabla = _tabla_a_texto(res.columnas or [], res.filas)
        sangrado = tabla.replace("\n", "\n           ")
        print(f"Resultado: {sangrado}")

    if res.error:
        print(f"ERROR:     {res.error}")
    if res.respuesta:
        print(f"Respuesta: {res.respuesta}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.llm.responder",
        description="Pregunta en español sobre las ventas y obtén SQL + respuesta.",
    )
    parser.add_argument("pregunta", help="La pregunta en lenguaje natural")
    parser.add_argument("--solo-sql", action="store_true",
                        help="No hacer la segunda llamada al modelo")
    args = parser.parse_args(argv)

    crear_base_de_datos()
    res = responder_pregunta(args.pregunta, redactar=not args.solo_sql)
    _imprimir(res)
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
