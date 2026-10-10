"""Fase 27: bitácora de aprendizaje del chat libre.

El chat libre no tiene respuesta de referencia: no se puede saber si una
respuesta final es "correcta". Lo que sí se puede aprender entre preguntas
distintas son los errores de SQL contra el esquema real (p. ej. unir
`departments` con `aisles` por un `department_id` que `aisles` no tiene). Esta
bitácora guarda, por cada intento fallido o sospechoso del chat, una LECCIÓN de
una frase, y las lecciones se inyectan en el prompt de sistema de las
preguntas siguientes (`prompts_instacart.prompt_sql_instacart(lecciones=...)`),
igual que `REGLAS_DE_NEGOCIO` pero acumulado de fallos reales.

- Persistente: `data/cache/bitacora_chat.json` (ignorado por git), así que
  sobrevive entre sesiones de Streamlit. Se lee en cada pregunta (es chico) y
  se escribe de forma atómica (archivo temporal + reemplazo).
- Lecciones deterministas cuando el error es reconocible (`leccion_de_error`):
  columna en la tabla equivocada (con las columnas reales de la tabla y dónde
  SÍ está la columna, leídas del esquema real), columna inexistente, alias sin
  declarar, tabla inexistente, agregación anidada, GROUP BY incompleto y
  timeout. Si no, una lección genérica con la primera línea del error.
- Lecciones del revisor de SQL (`revision_sql.revisar_sql`): cuando marca la
  sentencia como sospechosa, su frase de lección.
- Deduplicadas por `firma` (el mismo error de esquema suma `veces` en vez de
  repetirse).

Límites (criterio en docs/CLASIFICACION_FALLOS.md, «Fase 27»):
- `MAX_LECCIONES_GUARDADAS` = 20 en disco; al pasarse, se descarta la de menos
  `veces` y, a igualdad, la más antigua.
- `MAX_LECCIONES_PROMPT` = 8 en el prompt: las de más `veces`, y a igualdad
  las más recientes.
- `MAX_CARACTERES_LECCION` = 220 por lección y `MAX_CARACTERES_BLOQUE` = 1,900
  para todo el bloque (~500 tokens): el prompt de sistema ya mide ~8.4k
  caracteres (~2.5k tokens) y los modelos corren con contexto de 4,096 tokens.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from src.llm.retroalimentacion import PISTA_TIMEOUT

RUTA_DEFAULT = Path("data/cache/bitacora_chat.json")
MAX_LECCIONES_GUARDADAS = 20
MAX_LECCIONES_PROMPT = 8
MAX_CARACTERES_LECCION = 220
MAX_CARACTERES_BLOQUE = 1900
MAX_CARACTERES_DETALLE = 400

ORIGEN_ERROR = "error_ejecucion"
ORIGEN_REVISOR = "revisor_sql"

ENCABEZADO_PROMPT = (
    "LECCIONES APRENDIDAS de errores reales en preguntas anteriores de este chat "
    "(no repitas estos errores; verifica contra el ESQUEMA REAL):"
)


@dataclass
class Leccion:
    firma: str
    leccion: str
    origen: str
    pregunta: str
    sql: str
    #: El error real (o el motivo del revisor), recortado.
    detalle: str
    veces: int = 1
    primera_vez: str = ""
    ultima_vez: str = ""


def _ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _recortar(texto: str, maximo: int) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


class Bitacora:
    """Bitácora en disco. `ruta` None -> `RUTA_DEFAULT` del módulo, resuelta
    en cada llamada (así los tests pueden monkeypatchearla)."""

    def __init__(self, ruta: Path | None = None):
        self._ruta = ruta

    @property
    def ruta(self) -> Path:
        return self._ruta if self._ruta is not None else RUTA_DEFAULT

    def cargar(self) -> list[Leccion]:
        """Lecciones guardadas; un archivo ausente o corrupto es una bitácora vacía."""
        try:
            datos = json.loads(self.ruta.read_text(encoding="utf-8"))
            return [Leccion(**d) for d in datos.get("lecciones", [])]
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            return []

    def _guardar(self, lecciones: list[Leccion]) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        contenido = json.dumps({"lecciones": [asdict(x) for x in lecciones]}, indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=self.ruta.parent, prefix=".bitacora_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(contenido)
        os.replace(tmp, self.ruta)

    def agregar(self, nueva: Leccion) -> Leccion:
        """Agrega `nueva` o, si ya hay una con la misma firma, le suma una vez
        y actualiza su pregunta/SQL/detalle al caso más reciente. Aplica el
        límite de `MAX_LECCIONES_GUARDADAS`. Devuelve la lección guardada."""
        lecciones = self.cargar()
        ahora = _ahora()
        nueva.leccion = _recortar(nueva.leccion, MAX_CARACTERES_LECCION)
        nueva.detalle = _recortar(nueva.detalle, MAX_CARACTERES_DETALLE)
        existente = next((x for x in lecciones if x.firma == nueva.firma), None)
        if existente is not None:
            existente.veces += 1
            existente.ultima_vez = ahora
            existente.pregunta, existente.sql, existente.detalle = nueva.pregunta, nueva.sql, nueva.detalle
            existente.leccion = nueva.leccion
            guardada = existente
        else:
            nueva.primera_vez = nueva.ultima_vez = ahora
            lecciones.append(nueva)
            guardada = nueva
        if len(lecciones) > MAX_LECCIONES_GUARDADAS:
            lecciones = sorted(por_prioridad(lecciones)[:MAX_LECCIONES_GUARDADAS], key=lecciones.index)
        self._guardar(lecciones)
        return guardada

    def para_prompt(self) -> list[Leccion]:
        """Las que entran al prompt: más `veces` primero, a igualdad las más
        recientes; como máximo `MAX_LECCIONES_PROMPT` y `MAX_CARACTERES_BLOQUE`."""
        orden = por_prioridad(self.cargar())
        elegidas, usados = [], len(ENCABEZADO_PROMPT)
        for x in orden[:MAX_LECCIONES_PROMPT]:
            if usados + len(x.leccion) + 3 > MAX_CARACTERES_BLOQUE:
                break
            elegidas.append(x)
            usados += len(x.leccion) + 3
        return elegidas

    def bloque_prompt(self) -> str | None:
        """Texto para el prompt de sistema, o None si no hay lecciones."""
        elegidas = self.para_prompt()
        if not elegidas:
            return None
        return "\n".join([ENCABEZADO_PROMPT, *(f"- {x.leccion}" for x in elegidas)])

    def borrar(self) -> None:
        self._guardar([])


def por_prioridad(lecciones: list[Leccion]) -> list[Leccion]:
    """Más `veces` primero; a igualdad, la más reciente (`ultima_vez` tiene
    resolución de segundos: el empate se rompe por orden en el archivo, donde
    las nuevas van al final)."""
    con_orden = list(enumerate(lecciones))
    con_orden.sort(key=lambda par: (par[1].veces, par[1].ultima_vez, par[0]), reverse=True)
    return [x for _i, x in con_orden]


# ---------------------------------------------------------------------------
# Lecciones deterministas a partir del error real de DuckDB
# ---------------------------------------------------------------------------

_PALABRAS_NO_ALIAS = {
    "on", "where", "join", "left", "right", "inner", "outer", "full", "cross", "group", "order", "limit",
    "using", "natural", "having", "union", "as", "select",
}


def alias_de_tablas(sql: str) -> dict[str, str]:
    """{alias o nombre: tabla} de las tablas de FROM/JOIN del SQL."""
    out: dict[str, str] = {}
    for tabla, alias in re.findall(r"(?i)\b(?:from|join)\s+([a-z_][\w]*)(?:\s+(?:as\s+)?([a-z_][\w]*))?", sql or ""):
        out[tabla.lower()] = tabla.lower()
        if alias and alias.lower() not in _PALABRAS_NO_ALIAS:
            out[alias.lower()] = tabla.lower()
    return out


def mapa_columnas(con: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """{tabla o vista: [columnas]} del esquema real (DESCRIBE)."""
    tablas = [t for (t,) in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name").fetchall()]
    return {t: [c[0] for c in con.execute(f"DESCRIBE {t}").fetchall()] for t in tablas}


def _donde_esta(columna: str, mapa: dict[str, list[str]]) -> list[str]:
    return [t for t, cols in mapa.items() if columna.lower() in (c.lower() for c in cols)]


def _lista(xs: list[str]) -> str:
    return ", ".join(xs) if xs else "ninguna tabla"


def leccion_de_error(
    error: str, sql: str, mapa: dict[str, list[str]], pregunta: str = "",
) -> Leccion | None:
    """Lección determinista para el error REAL de ejecución de una consulta
    del chat (DuckDB o el timeout de la guarda). Quien llama decide qué
    errores son de ejecución: los del modelo (no respondió) o de la guarda
    (pidió escribir) no enseñan nada del esquema y no llegan aquí."""
    if not error or not sql:
        return None
    if "excedió el límite" in error or "Out of Memory" in error:
        return Leccion("timeout", PISTA_TIMEOUT, ORIGEN_ERROR, pregunta, sql, error)
    alias = alias_de_tablas(sql)
    m = re.search(r'Table "(\w+)" does not have a column named "(\w+)"', error)
    if m:
        tabla = alias.get(m.group(1).lower(), m.group(1).lower())
        col = m.group(2)
        reales = mapa.get(tabla, [])
        texto = (f"La tabla {tabla} NO tiene la columna {col} (sus columnas: {_lista(reales)}); "
                 f"{col} está en: {_lista(_donde_esta(col, mapa))}.")
        return Leccion(f"columna_en_tabla:{tabla}.{col.lower()}", texto, ORIGEN_ERROR, pregunta, sql, error)
    m = re.search(r'Referenced column "(\w+)" not found', error)
    if m:
        col = m.group(1)
        donde = _donde_esta(col, mapa)
        usadas = sorted(set(alias.values()))
        texto = (f"La columna {col} no está en las tablas de tu FROM/JOIN ({_lista(usadas)}); "
                 + (f"está en: {_lista(donde)}, agrega ese JOIN." if donde else "no existe en ninguna tabla."))
        return Leccion(f"columna_no_encontrada:{col.lower()}", texto, ORIGEN_ERROR, pregunta, sql, error)
    m = re.search(r'Referenced table "(\w+)" not found', error)
    if m:
        texto = (f"Usaste el alias {m.group(1)} sin declarar su tabla: cada alias que aparece en SELECT, "
                 "GROUP BY u ORDER BY necesita su tabla en FROM o en un JOIN.")
        return Leccion("alias_no_declarado", texto, ORIGEN_ERROR, pregunta, sql, error)
    m = re.search(r"Table with name (\w+) does not exist", error)
    if m:
        texto = f"La tabla {m.group(1)} no existe; las tablas y vistas reales son: {_lista(sorted(mapa))}."
        return Leccion(f"tabla_inexistente:{m.group(1).lower()}", texto, ORIGEN_ERROR, pregunta, sql, error)
    if "aggregate function calls cannot be nested" in error:
        texto = ("No anides agregaciones como AVG(COUNT(*)): calcula el conteo en una subconsulta y promedia "
                 "afuera: SELECT AVG(n) FROM (SELECT id, COUNT(*) AS n FROM t GROUP BY id) s.")
        return Leccion("agregacion_anidada", texto, ORIGEN_ERROR, pregunta, sql, error)
    if "must appear in the GROUP BY clause" in error:
        texto = "Toda columna del SELECT que no esté dentro de una agregación debe ir también en el GROUP BY."
        return Leccion("group_by_incompleto", texto, ORIGEN_ERROR, pregunta, sql, error)
    primera = _recortar(error.splitlines()[0], 160)
    firma = "otro:" + re.sub(r'"[^"]*"|\d+', "_", primera.lower())[:60]
    return Leccion(firma, f"Un SQL anterior falló con: {primera}", ORIGEN_ERROR, pregunta, sql, error)


def leccion_de_revision(motivo: str, leccion: str, sql: str, pregunta: str) -> Leccion:
    """Lección del revisor de SQL (sentencia sospechosa)."""
    texto = leccion.strip() or motivo.strip()
    firma = "revisor:" + re.sub(r"\W+", " ", texto.lower()).strip()[:60]
    return Leccion(firma, texto, ORIGEN_REVISOR, pregunta, sql, motivo)
