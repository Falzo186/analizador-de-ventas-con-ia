"""Orquestador de la ruta Instacart: pregunta en español -> SQL -> resultado
-> respuesta natural. Misma FORMA que `src/llm/responder.py` (Northwind):

  1. construir_descripcion_esquema() (introspección, con caché) -> texto
  2. generar_sql_instacart()         (llamada 1 al modelo)      -> texto SQL
  3. validar_sql()                   (sin modelo, safety.py)    -> SQL seguro o error
  4. ejecutar_con_guardas()          (LIMIT + timeout + solo lectura) -> filas
     (2-4 viven en `generar_y_ejecutar_con_reintento`: si 3 o 4 fallan, UN
     solo reintento de la llamada 1 con el motivo exacto del rechazo)
  5. redactar_respuesta_instacart()  (llamada 2 al modelo)      -> frase en español
  6. finalizar_redaccion()           (sin modelo, cifras_instacart) -> cada cifra
     de la frase debe estar en el resultado; si no, UN reintento estricto de
     la llamada 2 y, si vuelve a fallar, respuesta de plantilla sin modelo

Toda llamada al modelo lleva timeout (`TIMEOUT_LLM_SQL_S`,
`TIMEOUT_LLM_RESPUESTA_S`): sin él, un Ollama colgado dejaría el chat
esperando para siempre.

Ruta nueva y paralela (Fase 5): no importa nada de `src/llm/responder.py`
ni lo modifica. Cada paso está en su propio try/except: si algo falla
(Ollama apagado, SQL inseguro, timeout, tabla inexistente...), se devuelve
un `ResultadoInstacart` con `error` en texto claro para el usuario -- nunca
un stack trace crudo ni una respuesta inventada.

CLI:
  python -m src.llm.responder_instacart "¿cuántos pedidos hay en total?"
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from src.llm import client
from src.llm.esquema_instacart import construir_descripcion_esquema
from src.llm.nl2sql_instacart import generar_sql_instacart, generar_sql_reintento_instacart
from src.llm.cifras_instacart import (
    cifras_invalidas,
    fmt_valor,
    respuesta_plantilla,
    valores_permitidos,
)
from src.llm.prompts_instacart import (
    TurnoPrevio,
    prompt_respuesta_estricta_instacart,
    prompt_respuesta_instacart,
)
from src.llm.safety_instacart import (
    LIMITE_FILAS_DEFAULT,
    TIMEOUT_S_DEFAULT,
    SqlNoSeguro,
    TimeoutConsulta,
    abrir_conexion_solo_lectura,
    ejecutar_con_guardas,
    es_intento_de_escritura,
    validar_sql,
)

DB_PATH_DEFAULT = Path("data/instacart.duckdb")
MAX_FILAS_AL_MODELO = 25

#: Timeouts de cada llamada al modelo. qwen2.5-coder:1.5b (default) midió
#: 2.9s de media para la llamada de SQL y 2.5s para la de respuesta en el
#: benchmark (docs/FASE5_REPORTE.md): 30s deja un margen de ~10x.
TIMEOUT_LLM_SQL_S = 30.0
TIMEOUT_LLM_RESPUESTA_S = 30.0


@dataclass
class ResultadoInstacart:
    pregunta: str
    sql: str | None = None
    columnas: list[str] | None = None
    filas: list[tuple] | None = None
    respuesta: str | None = None
    error: str | None = None
    #: 1 si el primer SQL sirvió, 2 si hizo falta el reintento, 0 si ni
    #: siquiera se llegó a generar SQL.
    intentos: int = 0
    #: Motivo exacto por el que se rechazó el primer SQL (si hubo reintento).
    motivo_reintento: str | None = None
    #: De dónde salió `respuesta`: "modelo" (primera redacción, cifras
    #: válidas), "reintento" (segunda redacción estricta) o "plantilla"
    #: (compuesta sin modelo con los valores reales).
    origen_respuesta: str | None = None
    #: Cifras de la primera redacción que no estaban en el resultado.
    cifras_rechazadas: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


def _tabla_a_texto(columnas: list[str], filas: list[tuple]) -> str:
    if not filas:
        return "(sin resultados)"
    lineas = [" | ".join(map(str, columnas))]
    for fila in filas[:MAX_FILAS_AL_MODELO]:
        lineas.append(" | ".join("" if v is None else str(v) for v in fila))
    if len(filas) > MAX_FILAS_AL_MODELO:
        lineas.append(f"... ({len(filas) - MAX_FILAS_AL_MODELO} filas más)")
    return "\n".join(lineas)


def redactar_respuesta_instacart(
    pregunta: str,
    columnas: list[str],
    filas: list[tuple],
    *,
    modelo: str | None = None,
    timeout_s: float = TIMEOUT_LLM_RESPUESTA_S,
) -> str:
    """Llamada 2: convierte el resultado tabular en una frase en español."""
    sistema, usuario = prompt_respuesta_instacart(pregunta, _tabla_a_texto(columnas, filas))
    return client.chat_con_modelo(
        modelo or client.modelo_actual_instacart(), sistema, usuario, timeout_s=timeout_s,
    ).strip()


def redactar_respuesta_stream(
    pregunta: str,
    columnas: list[str],
    filas: list[tuple],
    *,
    modelo: str | None = None,
    timeout_s: float = TIMEOUT_LLM_RESPUESTA_S,
) -> Iterator[str]:
    """Llamada 2 en streaming (chat de la Fase 6): mismos prompts que
    `redactar_respuesta_instacart`, pero va entregando fragmentos. El texto
    completo debe pasar después por `finalizar_redaccion()`."""
    sistema, usuario = prompt_respuesta_instacart(pregunta, _tabla_a_texto(columnas, filas))
    yield from client.chat_con_modelo_stream(
        modelo or client.modelo_actual_instacart(), sistema, usuario, timeout_s=timeout_s,
    )


def finalizar_redaccion(
    res: ResultadoInstacart,
    texto: str,
    *,
    modelo: str | None = None,
    timeout_s: float = TIMEOUT_LLM_RESPUESTA_S,
) -> None:
    """Valida las cifras de `texto` (la primera redacción) contra
    `res.filas` y deja la respuesta final en `res.respuesta` y
    `res.origen_respuesta`:

      - todas las cifras aparecen en el resultado -> se usa tal cual ("modelo");
      - alguna no -> UN reintento de la llamada 2 con instrucción estricta y
        los valores exactos preformateados ("reintento");
      - el reintento también altera una cifra, o falla -> respuesta de
        plantilla sin modelo con los valores reales ("plantilla").
    """
    texto = (texto or "").strip()
    permitidos = valores_permitidos(res.pregunta, res.columnas or [], res.filas or [])
    res.cifras_rechazadas = cifras_invalidas(texto, permitidos)
    if texto and not res.cifras_rechazadas:
        res.respuesta, res.origen_respuesta = texto, "modelo"
        return

    exactos = sorted({fmt_valor(v) for fila in (res.filas or [])[:MAX_FILAS_AL_MODELO]
                      for v in fila if isinstance(v, (int, float)) and not isinstance(v, bool)})
    try:
        sistema, usuario = prompt_respuesta_estricta_instacart(
            res.pregunta, _tabla_a_texto(res.columnas or [], res.filas or []),
            res.cifras_rechazadas or ["(respuesta vacía)"], exactos or ["(sin cifras)"],
        )
        segundo = client.chat_con_modelo(
            modelo or client.modelo_actual_instacart(), sistema, usuario, timeout_s=timeout_s,
        ).strip()
    except Exception:  # noqa: BLE001 - si el reintento falla, la plantilla sigue siendo correcta
        segundo = ""
    if segundo and not cifras_invalidas(segundo, permitidos):
        res.respuesta, res.origen_respuesta = segundo, "reintento"
        return
    res.respuesta, res.origen_respuesta = respuesta_plantilla(res.columnas or [], res.filas or []), "plantilla"


def redaccion_en_stream(
    res: ResultadoInstacart,
    *,
    modelo: str | None = None,
    timeout_s: float = TIMEOUT_LLM_RESPUESTA_S,
) -> Iterator[str]:
    """Paso 5-6 para el chat con streaming: entrega los fragmentos de la
    primera redacción a medida que llegan y, al agotarse, deja `res`
    completo (`finalizar_redaccion`). Si el modelo falla a mitad, lo
    registra en `res.error` con el mismo mensaje controlado que
    `responder_pregunta_instacart` y termina sin lanzar.

    Uso: `st.write_stream(redaccion_en_stream(res))` sobre un `res` que
    viene de `responder_pregunta_instacart(..., redactar=False)` con `res.ok`.
    """
    partes: list[str] = []
    try:
        for fragmento in redactar_respuesta_stream(
            res.pregunta, res.columnas or [], res.filas or [], modelo=modelo, timeout_s=timeout_s,
        ):
            partes.append(fragmento)
            yield fragmento
        finalizar_redaccion(res, "".join(partes), modelo=modelo, timeout_s=timeout_s)
    except client.LlmNoDisponible as exc:
        res.respuesta = None
        res.error = f"Se obtuvo el resultado pero no se pudo redactar la respuesta: {exc}"


def _validar_y_ejecutar(con, sql: str, limite_filas: int, timeout_sql_s: float):
    sql_seguro = validar_sql(sql)
    columnas, filas = ejecutar_con_guardas(con, sql_seguro, limite_filas=limite_filas, timeout_s=timeout_sql_s)
    return sql_seguro, columnas, filas


def generar_y_ejecutar_con_reintento(
    con: duckdb.DuckDBPyConnection,
    pregunta: str,
    descripcion_esquema: str,
    res: ResultadoInstacart,
    *,
    modelo: str | None = None,
    timeout_llm_s: float = TIMEOUT_LLM_SQL_S,
    limite_filas: int = LIMITE_FILAS_DEFAULT,
    timeout_sql_s: float = TIMEOUT_S_DEFAULT,
    contexto: list[TurnoPrevio] | None = None,
) -> None:
    """Pasos 2-4 con UN reintento como máximo. Llena `res.sql`,
    `res.columnas`, `res.filas`, `res.intentos` y `res.motivo_reintento`;
    si el intento final falla, relanza su excepción (quien llama la
    traduce a un mensaje controlado).

    Hay reintento si el primer SQL lo rechaza `validar_sql()` o si DuckDB
    falla al ejecutarlo (columna/tabla inexistente, error de sintaxis...).
    NO hay reintento si:
      - el SQL contiene palabras de escritura (`es_intento_de_escritura`):
        la petición era escribir y se rechaza tal cual;
      - la consulta excedió su timeout (`TimeoutConsulta`) o el modelo no
        respondió: reintentar solo duplicaría la espera.
    """
    res.sql = generar_sql_instacart(
        pregunta, descripcion_esquema, modelo=modelo, timeout_s=timeout_llm_s, contexto=contexto,
    )
    res.intentos = 1
    try:
        res.sql, res.columnas, res.filas = _validar_y_ejecutar(con, res.sql, limite_filas, timeout_sql_s)
        return
    except SqlNoSeguro as exc:
        if es_intento_de_escritura(res.sql):
            raise
        motivo = str(exc)
    except duckdb.InterruptException:
        raise
    except duckdb.Error as exc:
        motivo = f"{type(exc).__name__}: {exc}"

    res.motivo_reintento = motivo
    res.sql = generar_sql_reintento_instacart(
        pregunta, descripcion_esquema, res.sql, motivo, modelo=modelo, timeout_s=timeout_llm_s, contexto=contexto,
    )
    res.intentos = 2
    res.sql, res.columnas, res.filas = _validar_y_ejecutar(con, res.sql, limite_filas, timeout_sql_s)


def responder_pregunta_instacart(
    pregunta: str,
    *,
    db_path: Path = DB_PATH_DEFAULT,
    redactar: bool = True,
    limite_filas: int = LIMITE_FILAS_DEFAULT,
    timeout_sql_llm_s: float = TIMEOUT_LLM_SQL_S,
    timeout_respuesta_llm_s: float = TIMEOUT_LLM_RESPUESTA_S,
    contexto: list[TurnoPrevio] | None = None,
    modelo: str | None = None,
) -> ResultadoInstacart:
    """Ejecuta el flujo completo y SIEMPRE devuelve un `ResultadoInstacart`
    (nunca lanza una excepción hacia quien llama). `modelo` (Fase 9): el
    modelo de Ollama a usar; por defecto, `client.modelo_actual_instacart()`."""
    res = ResultadoInstacart(pregunta=pregunta)

    try:
        con = abrir_conexion_solo_lectura(str(db_path))
    except Exception as exc:  # noqa: BLE001
        res.error = f"No se pudo abrir la base de datos de Instacart: {exc}"
        return res

    try:
        # 0. esquema por introspección (con caché de huella_bd)
        try:
            descripcion_esquema = construir_descripcion_esquema(con)
        except Exception as exc:  # noqa: BLE001
            res.error = f"No se pudo leer el esquema de la base de datos: {exc}"
            return res

        # 1-3. texto -> SQL -> validación -> ejecución con guardas
        #      (LIMIT forzado + timeout real + solo lectura), con un reintento
        try:
            generar_y_ejecutar_con_reintento(
                con, pregunta, descripcion_esquema, res,
                timeout_llm_s=timeout_sql_llm_s, limite_filas=limite_filas, contexto=contexto,
                modelo=modelo,
            )
        except client.LlmTimeout as exc:
            res.error = f"El modelo tardó demasiado en generar la consulta y se canceló: {exc}"
            return res
        except client.LlmNoDisponible as exc:
            res.error = f"No se pudo generar la consulta SQL: {exc}"
            return res
        except SqlNoSeguro as exc:
            res.error = f"No puedo ejecutar esa consulta: {exc}"
            return res
        except TimeoutConsulta as exc:
            res.error = f"La consulta tardó demasiado y se canceló: {exc}"
            return res
        except Exception as exc:  # noqa: BLE001 - SQL mal formado, columna/tabla inexistente...
            res.error = f"No se pudo ejecutar la consulta contra los datos: {exc}"
            return res
    finally:
        con.close()

    # 4. resultado -> español (opcional; si falla, no invalida el resto)
    if redactar:
        try:
            texto = redactar_respuesta_instacart(
                pregunta, res.columnas, res.filas, modelo=modelo, timeout_s=timeout_respuesta_llm_s,
            )
            finalizar_redaccion(res, texto, modelo=modelo, timeout_s=timeout_respuesta_llm_s)
        except Exception as exc:  # noqa: BLE001
            res.respuesta = None
            res.error = f"Se obtuvo el resultado pero no se pudo redactar la respuesta: {exc}"

    return res


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _imprimir(res: ResultadoInstacart) -> None:
    print(f"Pregunta:  {res.pregunta}")
    print(f"SQL:       {res.sql or '(no generado)'}")
    if res.motivo_reintento:
        print(f"Reintento: el primer SQL se rechazó por: {res.motivo_reintento}")

    if res.filas is not None:
        tabla = _tabla_a_texto(res.columnas or [], res.filas)
        sangrado = tabla.replace("\n", "\n           ")
        print(f"Resultado: {sangrado}")

    if res.error:
        print(f"ERROR:     {res.error}")
    if res.respuesta:
        print(f"Respuesta: {res.respuesta}")
    if res.origen_respuesta and res.origen_respuesta != "modelo":
        print(f"Cifras:    la primera redacción alteró {res.cifras_rechazadas}; "
              f"respuesta final de: {res.origen_respuesta}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.llm.responder_instacart",
        description="Pregunta en español sobre el dataset Instacart y obtén SQL + respuesta.",
    )
    parser.add_argument("pregunta", help="La pregunta en lenguaje natural")
    parser.add_argument("--db", default=str(DB_PATH_DEFAULT))
    parser.add_argument("--solo-sql", action="store_true",
                        help="No hacer la segunda llamada al modelo")
    args = parser.parse_args(argv)

    res = responder_pregunta_instacart(args.pregunta, db_path=Path(args.db), redactar=not args.solo_sql)
    _imprimir(res)
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
