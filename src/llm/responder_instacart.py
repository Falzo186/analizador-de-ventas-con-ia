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
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb

from src.llm import client, tecnicas
from src.llm.bitacora_chat import Bitacora, leccion_de_error, leccion_de_revision, mapa_columnas
from src.llm.esquema_instacart import construir_descripcion_esquema, describir_columnas
from src.llm.revision_sql import FILAS_AL_REVISOR, revisar_sql
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
    #: Fase 23 (trazabilidad): primer SQL, si se rechazó y hubo reintento.
    sql_rechazado: str | None = None
    #: Fase 23: primera redacción del modelo y, si hubo, la segunda
    #: (estricta), tal cual las devolvió, aunque se hayan descartado.
    primera_redaccion: str | None = None
    segunda_redaccion: str | None = None
    #: Fase 27: True si `motivo_reintento` vino de DuckDB (o del timeout de la
    #: guarda), es decir, de ejecutar la consulta y no de la guarda de seguridad.
    motivo_reintento_es_ejecucion: bool = False
    #: Fase 27: revisión del SQL por el revisor (`revision_sql.RevisionSQL` como
    #: dict) y si se reintentó por ella.
    revision_sql: dict | None = None
    reintento_por_revision: bool = False
    #: Fase 27: lecciones agregadas a la bitácora del chat por esta pregunta.
    lecciones_nuevas: list[str] = field(default_factory=list)
    #: Fase 27: cuántas lecciones de la bitácora llevaba el prompt de sistema.
    lecciones_en_prompt: int = 0
    #: Fase 30: técnica de contexto usada, ejemplos few-shot inyectados
    #: [(id, similitud)] y el texto exacto del bloque (None si no hubo).
    tecnica: str = "base"
    ejemplos_usados: list[tuple[str, float]] = field(default_factory=list)
    bloque_ejemplos: str | None = None

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
    temperatura: float = 0.0,
    retroalimentacion: str | None = None,
) -> str:
    """Llamada 2: convierte el resultado tabular en una frase en español."""
    sistema, usuario = prompt_respuesta_instacart(pregunta, _tabla_a_texto(columnas, filas), retroalimentacion)
    return client.chat_con_modelo(
        modelo or client.modelo_actual_instacart(), sistema, usuario, timeout_s=timeout_s,
        temperatura=temperatura,
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
    temperatura: float = 0.0,
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
    res.primera_redaccion = texto
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
            temperatura=temperatura,
        ).strip()
    except Exception:  # noqa: BLE001 - si el reintento falla, la plantilla sigue siendo correcta
        segundo = ""
    res.segunda_redaccion = segundo or None
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
    temperatura: float = 0.0,
    retroalimentacion: str | None = None,
    lecciones: str | None = None,
    ejemplos: str | None = None,
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
        temperatura=temperatura, retroalimentacion=retroalimentacion, lecciones=lecciones, ejemplos=ejemplos,
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
        res.motivo_reintento_es_ejecucion = True

    res.motivo_reintento = motivo
    res.sql_rechazado = res.sql
    res.sql = generar_sql_reintento_instacart(
        pregunta, descripcion_esquema, res.sql, motivo, modelo=modelo, timeout_s=timeout_llm_s, contexto=contexto,
        temperatura=temperatura, retroalimentacion=retroalimentacion, lecciones=lecciones, ejemplos=ejemplos,
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
    temperatura: float = 0.0,
    retroalimentacion: str | None = None,
    bitacora: Bitacora | None = None,
    revisar_sql: bool = False,
    timeout_consulta_s: float = TIMEOUT_S_DEFAULT,
    tecnica: str | None = None,
) -> ResultadoInstacart:
    """Ejecuta el flujo completo y SIEMPRE devuelve un `ResultadoInstacart`
    (nunca lanza una excepción hacia quien llama). `modelo` (Fase 9): el
    modelo de Ollama a usar; por defecto, `client.modelo_actual_instacart()`.

    `temperatura` (Fase 11) se aplica a TODAS las llamadas al modelo del
    flujo (SQL, reintento de SQL, redacción, reintento estricto). El chat y
    la entrega no la pasan: siguen en 0.0. Solo el modo de iteraciones
    (`src.llm.iteraciones_juez`) usa un valor mayor, para que N corridas
    de la misma pregunta no salgan idénticas.

    `retroalimentacion` (Fase 23): texto con el intento anterior de la misma
    pregunta (SQL, resultado o error, veredicto del juez). Se agrega al
    mensaje de usuario de la llamada de SQL (y su reintento) y de la
    primera redacción. Solo lo pasa el modo «autocorrección» de
    `src.llm.iteraciones_juez`; con None los prompts no cambian.

    Fase 27 (solo el chat libre los pasa; las necesidades y la entrega no):
    - `bitacora`: sus lecciones van al prompt de sistema de SQL y, al terminar
      la fase SQL (bien o mal), se le agregan las lecciones de esta pregunta:
      del error real de ejecución (primer intento y/o final) y de la revisión.
    - `revisar_sql`: si el SQL ejecutó, el revisor (`revision_sql`) evalúa la
      SENTENCIA (no si la respuesta es correcta: no hay referencia). Si la marca
      sospechosa y todavía no se usó el único reintento, se reintenta con su
      motivo; si el reintento falla, se conserva el resultado original.

    `timeout_consulta_s` (Fase 29): límite de la guarda de DuckDB. El chat usa
    el default (10 s); el panel de iteraciones y la entrega pasan el de la
    necesidad (`DefNecesidad.timeout_consulta_s`, p. ej. 25 s en N9).

    `tecnica` (Fase 30): «base» o «few_shot» (`src.llm.tecnicas`); None =
    `tecnicas.tecnica_por_defecto()` (VENTIA_TECNICA o «base»). Con few_shot,
    los ejemplos más parecidos van al prompt de sistema de TODAS las llamadas
    de SQL de esta pregunta (y su reintento). Una técnica pendiente o
    desconocida devuelve el error en `res.error`."""
    res = ResultadoInstacart(pregunta=pregunta)
    try:
        res.tecnica = tecnicas.validar_tecnica(tecnica) if tecnica else tecnicas.tecnica_por_defecto()
        ctx = tecnicas.contexto_de_tecnica(res.tecnica, pregunta)
    except ValueError as exc:
        res.error = str(exc)
        return res
    res.ejemplos_usados, res.bloque_ejemplos = ctx.usados, ctx.bloque
    lecciones = bitacora.bloque_prompt() if bitacora is not None else None
    res.lecciones_en_prompt = len(bitacora.para_prompt()) if lecciones else 0

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
        error_ejecucion: str | None = None  # Fase 27: error final de DuckDB o timeout de la guarda
        try:
            generar_y_ejecutar_con_reintento(
                con, pregunta, descripcion_esquema, res,
                timeout_llm_s=timeout_sql_llm_s, limite_filas=limite_filas, contexto=contexto,
                modelo=modelo, temperatura=temperatura, retroalimentacion=retroalimentacion, lecciones=lecciones,
                timeout_sql_s=timeout_consulta_s, ejemplos=res.bloque_ejemplos,
            )
        except client.LlmTimeout as exc:
            res.error = f"El modelo tardó demasiado en generar la consulta y se canceló: {exc}"
        except client.LlmNoDisponible as exc:
            res.error = f"No se pudo generar la consulta SQL: {exc}"
        except SqlNoSeguro as exc:
            res.error = f"No puedo ejecutar esa consulta: {exc}"
        except TimeoutConsulta as exc:
            res.error = f"La consulta tardó demasiado y se canceló: {exc}"
            error_ejecucion = str(exc)
        except Exception as exc:  # noqa: BLE001 - SQL mal formado, columna/tabla inexistente...
            res.error = f"No se pudo ejecutar la consulta contra los datos: {exc}"
            if isinstance(exc, duckdb.Error):
                error_ejecucion = str(exc)

        # Fase 27: revisión de la sentencia y bitácora del chat
        if res.ok and revisar_sql:
            _revisar_y_quizas_reintentar(
                con, res, descripcion_esquema, modelo=modelo, timeout_llm_s=timeout_sql_llm_s,
                limite_filas=limite_filas, contexto=contexto, lecciones=lecciones, timeout_sql_s=timeout_consulta_s,
                ejemplos=res.bloque_ejemplos,
            )
        if bitacora is not None:
            _aprender(con, res, bitacora, error_ejecucion)
        if not res.ok:
            return res
    finally:
        con.close()

    # 4. resultado -> español (opcional; si falla, no invalida el resto)
    if redactar:
        try:
            texto = redactar_respuesta_instacart(
                pregunta, res.columnas, res.filas, modelo=modelo, timeout_s=timeout_respuesta_llm_s,
                temperatura=temperatura, retroalimentacion=retroalimentacion,
            )
            finalizar_redaccion(
                res, texto, modelo=modelo, timeout_s=timeout_respuesta_llm_s, temperatura=temperatura,
            )
        except Exception as exc:  # noqa: BLE001
            res.respuesta = None
            res.error = f"Se obtuvo el resultado pero no se pudo redactar la respuesta: {exc}"

    return res


# ---------------------------------------------------------------------------
# Fase 27: revisión de la sentencia SQL y bitácora del chat libre
# ---------------------------------------------------------------------------

def _revisar_y_quizas_reintentar(
    con: duckdb.DuckDBPyConnection, res: ResultadoInstacart, descripcion_esquema: str, *,
    modelo: str | None, timeout_llm_s: float, limite_filas: int, contexto: list[TurnoPrevio] | None,
    lecciones: str | None, timeout_sql_s: float = TIMEOUT_S_DEFAULT, ejemplos: str | None = None,
) -> None:
    """Revisa la sentencia que ejecutó. Si es sospechosa y queda el único
    reintento, lo usa; si el reintento no ejecuta, se queda el resultado
    original y el error del reintento se guarda para la bitácora."""
    modelo = modelo or client.modelo_actual_instacart()
    rev = revisar_sql(
        res.pregunta, res.sql or "", describir_columnas(con),
        _tabla_a_texto(res.columnas or [], (res.filas or [])[:FILAS_AL_REVISOR]),
        modelo=modelo, timeout_s=timeout_llm_s,
    )
    res.revision_sql = asdict(rev)
    if not rev.sospechoso or res.intentos >= 2:
        return
    motivo = f"Un revisor marcó tu SQL como sospechoso: {rev.motivo}"
    sql_nuevo: str | None = None
    try:
        sql_nuevo = generar_sql_reintento_instacart(
            res.pregunta, descripcion_esquema, res.sql or "", motivo, modelo=modelo, timeout_s=timeout_llm_s,
            contexto=contexto, lecciones=lecciones, ejemplos=ejemplos,
        )
        sql_nuevo, columnas, filas = _validar_y_ejecutar(con, sql_nuevo, limite_filas, timeout_sql_s)
    except (duckdb.Error, TimeoutConsulta) as exc:
        res.revision_sql["reintento_error"] = str(exc)
        res.revision_sql["reintento_sql"] = sql_nuevo
        return
    except Exception as exc:  # noqa: BLE001 - el modelo no respondió, la guarda lo rechazó...
        res.revision_sql["reintento_error"] = str(exc)
        return
    res.sql_rechazado, res.motivo_reintento = res.sql, motivo
    res.sql, res.columnas, res.filas = sql_nuevo, columnas, filas
    res.intentos, res.reintento_por_revision = 2, True


def _aprender(
    con: duckdb.DuckDBPyConnection, res: ResultadoInstacart, bitacora: Bitacora, error_ejecucion: str | None,
) -> None:
    """Agrega a la bitácora las lecciones de esta pregunta: el error real del
    primer SQL (si fue de ejecución), el error final y la revisión
    sospechosa. Nunca tumba la respuesta: si algo falla, no se aprende."""
    try:
        mapa = mapa_columnas(con)
        candidatas = []
        if res.motivo_reintento_es_ejecucion:
            candidatas.append(leccion_de_error(res.motivo_reintento or "", res.sql_rechazado or "", mapa, res.pregunta))
        if error_ejecucion:
            candidatas.append(leccion_de_error(error_ejecucion, res.sql or "", mapa, res.pregunta))
        rev = res.revision_sql or {}
        if rev.get("sospechoso"):
            sql_revisado = res.sql_rechazado if res.reintento_por_revision else res.sql
            candidatas.append(leccion_de_revision(rev.get("motivo", ""), rev.get("leccion", ""),
                                                  sql_revisado or "", res.pregunta))
            if rev.get("reintento_error") and rev.get("reintento_sql"):
                candidatas.append(leccion_de_error(rev["reintento_error"], rev["reintento_sql"], mapa, res.pregunta))
        vistas = set()
        for leccion in candidatas:
            if leccion is None or leccion.firma in vistas:
                continue
            vistas.add(leccion.firma)
            res.lecciones_nuevas.append(bitacora.agregar(leccion).leccion)
    except Exception:  # noqa: BLE001 - la bitácora es una ayuda, nunca un punto de falla
        pass


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
