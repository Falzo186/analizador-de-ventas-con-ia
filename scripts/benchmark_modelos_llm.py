"""Selección de modelo LLM para la ruta Instacart, CON DATOS (no de
memoria/intuición). Corre un banco de 15 preguntas fijas contra cada
modelo YA DESCARGADO en esta máquina -- confirmado con `ollama list` en
tiempo de ejecución, nunca asumido -- y mide, por modelo:

  - tiempo de la llamada 1 (texto -> SQL) y la llamada 2 (resultado -> texto)
  - RSS del proceso de Ollama durante la carga/uso (reusa la técnica /proc
    de Fase 3, scripts/prueba_concurrencia.py)
  - si el SQL generado pasó validar_sql() + se ejecutó sin error, al
    primer intento Y al final (con el reintento único de
    `responder_instacart.generar_y_ejecutar_con_reintento`, el mismo camino
    que usa el chat; `--sin-reintento` reproduce la medición original)
  - si el SQL solo referencia tablas reales del esquema Instacart (heurística
    de regex sobre FROM/JOIN, no un parser SQL completo -- documentado)
  - si rechazó correctamente las 2 preguntas trampa (dinero, escritura)

Reusa `Monitor`/`_pids_ollama`/`_rss_pids_kb` de `scripts.prueba_concurrencia`
(Fase 3) en vez de reimplementar la medición de memoria.

Uso:
    python -m scripts.benchmark_modelos_llm
    python -m scripts.benchmark_modelos_llm --timeout-pregunta 45 --out docs/evidencia/benchmark_modelos.json
    python -m scripts.benchmark_modelos_llm --modelos qwen2.5-coder:1.5b \
        --out docs/evidencia/benchmark_reintento_qwen1.5b.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.prueba_concurrencia import Monitor, _pids_ollama, _rss_pids_kb  # noqa: E402
from src.llm import client  # noqa: E402
from src.llm.esquema_instacart import construir_descripcion_esquema  # noqa: E402
from src.llm.nl2sql_instacart import generar_sql_instacart  # noqa: E402
from src.llm.prompts_instacart import prompt_respuesta_instacart  # noqa: E402
from src.llm.responder_instacart import (  # noqa: E402
    ResultadoInstacart,
    finalizar_redaccion,
    generar_y_ejecutar_con_reintento,
    redactar_respuesta_instacart,
)
from src.llm.safety_instacart import (  # noqa: E402
    SqlNoSeguro,
    TimeoutConsulta,
    abrir_conexion_solo_lectura,
    ejecutar_con_guardas,
    validar_sql,
)

DB_PATH_DEFAULT = Path("data/instacart.duckdb")
TIMEOUT_PREGUNTA_DEFAULT = 45.0
TIMEOUT_CONSULTA_SQL_DEFAULT = 10.0

TABLAS_REALES = {
    "aisles", "departments", "products", "orders",
    "order_products__prior", "order_products__train", "order_products",
}
NOMBRES_NORTHWIND = (
    "Products", "Orders", "Order Details", "Customers", "Employees",
    "Suppliers", "Categories", "Shippers",
)
PATRON_DINERO = re.compile(r"ingres|dinero|ganancia|precio|revenue|\$|USD|MXN", re.IGNORECASE)
MARCADORES_NEGACION = (
    "no hay", "no se puede", "no dice nada", "no se afirma", "no tiene",
    "ni ganancia", "no existe", "no tienen precio", "sin precios",
)
_PATRON_TABLA_EN_FROM_JOIN = re.compile(r'(?is)\b(?:FROM|JOIN)\s+("(?:[^"]+)"|[A-Za-z_][A-Za-z0-9_]*)')

# ---------------------------------------------------------------------------
# Clasificación de fallos (pedida por el profesor Eder Jahir González Bravo:
# no basta con "acertó/falló", hay que distinguir error de EJECUCIÓN -- el
# motor rechazó el SQL -- de error SEMÁNTICO -- el SQL corrió pero no
# responde lo que se preguntó). Ver docs/CLASIFICACION_FALLOS.md.
# ---------------------------------------------------------------------------

CATEGORIAS_FALLO: tuple[str, ...] = (
    "tabla_o_columna_inexistente",
    "error_sintaxis",
    "agregacion_incorrecta",
    "agregacion_incorrecta_heuristica",
    "otro",
)

# Los patrones siguientes se calibraron contra mensajes REALES que ya vimos
# salir de DuckDB en este proyecto (no inventados), incluida la corrida
# real de la Fase 11c:
#   Binder Error: Values list "op1" does not have a column named "eval_set"
#   Binder Error: column "product_id" must appear in the GROUP BY clause
#       or be used in an aggregate function
#   Parser Error: syntax error at or near "FORM"
#   Binder Error: Referenced table "departments" not found! Candidate
#       tables: "p", "op_train"
_PATRON_COLUMNA_O_TABLA_INEXISTENTE = re.compile(
    r"(does not have a column|column\s+\"[^\"]+\"\s+(?:does\s+)?not\s+found|"
    r"referenced table|catalog error|referenced column|table with name)",
    re.IGNORECASE,
)
_PATRON_SINTAXIS = re.compile(r"(parser error|syntax error)", re.IGNORECASE)
_PATRON_AGREGACION_EJECUCION = re.compile(
    r"(must appear in the group by clause|not an aggregate)", re.IGNORECASE,
)
# Heurística para el ÚNICO caso semántico detectable SIN oráculo de
# respuesta esperada: un SQL que sí corrió, pero agrupa por una columna de
# identificador (*_id o "id") mientras calcula un agregado -- exactamente
# el patrón real documentado en N9_ia.json de este proyecto (el modelo
# agrupó por order_id en vez de calcular la distribución completa).
#
# Por sí solo, "GROUP BY *_id + función de agregado" también describe un
# "top N" perfectamente correcto (p. ej. "producto con más líneas de
# pedido" agrupa por product_id a propósito). La corrida real de la Fase
# 11c encontró justo ese falso positivo en 2 de 13 preguntas. La señal que
# SÍ distingue un "top N" legítimo del bug real de N9 es el número de filas
# devueltas: un "top N"/"el más X" trae pocas filas (encaja en una
# respuesta); el bug de N9 trae casi una fila por grupo (miles, topadas en
# LIMITE_FILAS_DEFAULT=1000 por las guardas de seguridad). Por eso la
# heurística exige AMBAS cosas: el patrón de SQL sospechoso Y más filas de
# las que cualquier pregunta de este banco pediría como "top N" (la más
# grande pide 5).
_PATRON_TIENE_AGREGADO = re.compile(r"(?is)\b(AVG|SUM|COUNT|MIN|MAX)\s*\(")
_PATRON_GROUP_BY_ID_SOSPECHOSO = re.compile(r"(?is)GROUP\s+BY\s+[^;]*\b(\w*_id|\bid\b)\b")
UMBRAL_FILAS_AGREGACION_SOSPECHOSA = 50


def clasificar_fallo(r: "ResultadoPregunta") -> str | None:
    """Devuelve una categoría de CATEGORIAS_FALLO, o None si no hubo fallo
    (o si la pregunta es una trampa, que se evalúa aparte con
    `rechazo_correcto`).

    LIMITACIÓN DOCUMENTADA, NO OCULTA: sin una respuesta de referencia por
    pregunta (un oráculo), solo podemos clasificar con certeza los fallos de
    EJECUCIÓN (el SQL fue rechazado por DuckDB antes de correr). Para los
    fallos SEMÁNTICOS -- el SQL corrió bien pero no responde la pregunta --
    solo detectamos, por heurística, el patrón de "agrupó por un id en vez
    de agregar" ya visto en este proyecto, y solo cuando además trae muchas
    más filas de las que un "top N" pediría (ver
    UMBRAL_FILAS_AGREGACION_SOSPECHOSA). Un filtro incorrecto que igual
    corre y da un resultado con pinta de válido (p. ej. una fecha o un
    estado mal puesto) NO se detecta aquí: compararlo exigiría una
    respuesta esperada por pregunta, que queda fuera de este script.
    """
    if r.sql is None:
        return "otro" if r.error_sql else None

    texto_error = r.error_sql or ""
    if texto_error:
        if r.tablas_desconocidas or _PATRON_COLUMNA_O_TABLA_INEXISTENTE.search(texto_error):
            return "tabla_o_columna_inexistente"
        if _PATRON_SINTAXIS.search(texto_error):
            return "error_sintaxis"
        if _PATRON_AGREGACION_EJECUCION.search(texto_error):
            return "agregacion_incorrecta"
        return "otro"

    if (
        r.sql_valido
        and r.n_filas is not None
        and r.n_filas > UMBRAL_FILAS_AGREGACION_SOSPECHOSA
        and _PATRON_TIENE_AGREGADO.search(r.sql)
        and _PATRON_GROUP_BY_ID_SOSPECHOSO.search(r.sql)
    ):
        return "agregacion_incorrecta_heuristica"

    return None


# ---------------------------------------------------------------------------
# Verificación contra respuesta de referencia (Fase 12, pedida por el
# usuario tras ver que `clasificar_fallo()` no detecta un SQL que CORRE
# bien pero responde algo distinto de lo que se preguntó -- el
# "filtro_incorrecto" que el profesor sugirió y que se dejó fuera en la
# Fase 11 por falta de un oráculo. Aquí SÍ hay oráculo: 12 de las 15
# preguntas de este banco tienen una única respuesta correcta y se puede
# escribir a mano el SQL de referencia (igual que hacía el evaluador
# archivado de Northwind, ver docs/EVALUADOR_NORTHWIND_LEGACY.md, pero
# ahora acotado a este banco fijo, no a un banco abierto).
#
# Quedan FUERA del oráculo, a propósito:
#   - "Muéstrame los mejores productos." (categoria "ambigua"): no tiene
#     una respuesta única correcta, por diseño de la pregunta.
#   - Las 2 preguntas trampa: ya se evalúan con `rechazo_correcto`.
#
# Motivación con evidencia real (Fase 11d, qwen2.5:1.5b, ver
# docs/evidencia/benchmark_fase11d_prueba.json): con el SQL anterior a
# este commit, 3 respuestas que `clasificar_fallo()` marcaba como "sin
# fallo" eran incorrectas:
#   - "¿Cuántos pedidos son de tipo 'train'...?": el SQL no filtraba por
#     eval_set = 'train' -- contaba TODOS los pedidos.
#   - "Dame el top 5 de productos con más veces reordenados.": el SQL no
#     filtraba por reordered = 1 -- contaba todas las líneas, reordenadas
#     o no.
#   - "¿Cuál es el promedio de líneas de producto por pedido?": el SQL
#     hacía AVG(product_id) -- el promedio de un ID, no de un conteo.
# ---------------------------------------------------------------------------

@dataclass
class ReferenciaPregunta:
    sql: str
    #: Cómo comparar el resultado del modelo contra `sql`:
    #:   "escalar"           -> compara solo filas[0][0] (con redondeo para floats)
    #:   "primera_columna"   -> compara el CONJUNTO de valores de la primera
    #:                          columna (tolera que el modelo agregue columnas
    #:                          extra, p. ej. un conteo, o cambie el orden)
    #:   "conjunto_filas"    -> compara el CONJUNTO de filas completas
    #:                          (para preguntas que piden varios campos a la vez)
    #:   "primera_columna_id_o_nombre" -> como "primera_columna", pero cada
    #:                          fila de referencia trae (id, nombre) y una
    #:                          fila del modelo cuenta como coincidencia si
    #:                          su primer valor es el id O el nombre (p. ej.
    #:                          el modelo devolvió "Bag of Organic Bananas"
    #:                          en vez de 24852) -- un producto por fila, sin
    #:                          repetir coincidencias (ver Fase 15).
    modo: str


def _normalizar_valor_referencia(v: object) -> object:
    if isinstance(v, float):
        return round(v, 2)
    return v


def _valor_comparable(v: object) -> object:
    """Como `_normalizar_valor_referencia`, pero además pliega texto a
    minúsculas y quita espacios al borde -- para comparar un `product_id`
    contra un `product_name` sin que "Banana" y "banana " cuenten como
    valores distintos (modo "primera_columna_id_o_nombre")."""
    if isinstance(v, str):
        return v.strip().lower()
    return _normalizar_valor_referencia(v)


REFERENCIAS_SQL: dict[str, ReferenciaPregunta] = {
    "¿Cuántos pedidos hay en total?": ReferenciaPregunta(
        "SELECT COUNT(*) FROM orders", "escalar",
    ),
    "¿Cuántos productos distintos hay en el catálogo?": ReferenciaPregunta(
        "SELECT COUNT(DISTINCT product_id) FROM products", "escalar",
    ),
    "¿Cuántos usuarios distintos hay?": ReferenciaPregunta(
        "SELECT COUNT(DISTINCT user_id) FROM orders", "escalar",
    ),
    "¿Cuántos pedidos son de tipo 'train' (eval_set = 'train')?": ReferenciaPregunta(
        "SELECT COUNT(*) FROM orders WHERE eval_set = 'train'", "escalar",
    ),
    "¿Cuál es el order_dow (día de la semana anónimo) con más pedidos?": ReferenciaPregunta(
        "SELECT order_dow FROM orders GROUP BY order_dow ORDER BY COUNT(*) DESC LIMIT 1",
        "primera_columna",
    ),
    "¿Cuál es el pasillo (aisle) con más productos distintos?": ReferenciaPregunta(
        "SELECT a.aisle FROM aisles a JOIN products p ON p.aisle_id = a.aisle_id "
        "GROUP BY a.aisle ORDER BY COUNT(DISTINCT p.product_id) DESC LIMIT 1",
        "primera_columna",
    ),
    "¿Qué departamento tiene más líneas de pedido con productos reordenados (reordered = 1)?": ReferenciaPregunta(
        "SELECT d.department FROM order_products op "
        "JOIN products p ON op.product_id = p.product_id "
        "JOIN departments d ON p.department_id = d.department_id "
        "WHERE op.reordered = 1 GROUP BY d.department ORDER BY COUNT(*) DESC LIMIT 1",
        "primera_columna",
    ),
    "¿Cuál es el producto con más líneas de pedido (el más pedido)?": ReferenciaPregunta(
        # (id, nombre): Fase 15 encontró que modelos de 7-8B SÍ identifican
        # el producto correcto pero devuelven product_name en vez de
        # product_id -- comparar solo contra product_id los marcaba como
        # incorrectos por un problema de REPRESENTACIÓN, no de contenido.
        "SELECT p.product_id, p.product_name FROM order_products op "
        "JOIN products p ON op.product_id = p.product_id "
        "GROUP BY p.product_id, p.product_name ORDER BY COUNT(*) DESC LIMIT 1",
        "primera_columna_id_o_nombre",
    ),
    "Dame el top 5 de productos con más veces reordenados.": ReferenciaPregunta(
        "SELECT p.product_id, p.product_name FROM order_products op "
        "JOIN products p ON op.product_id = p.product_id "
        "WHERE op.reordered = 1 GROUP BY p.product_id, p.product_name ORDER BY COUNT(*) DESC LIMIT 5",
        "primera_columna_id_o_nombre",
    ),
    "¿A qué pasillo y departamento pertenece el producto con product_id = 1?": ReferenciaPregunta(
        "SELECT a.aisle, d.department FROM products p "
        "JOIN aisles a ON p.aisle_id = a.aisle_id "
        "JOIN departments d ON p.department_id = d.department_id "
        "WHERE p.product_id = 1",
        "conjunto_filas",
    ),
    "¿Cuál es el promedio de líneas de producto por pedido?": ReferenciaPregunta(
        "SELECT AVG(n) FROM (SELECT order_id, COUNT(*) AS n FROM order_products GROUP BY order_id) sub",
        "escalar",
    ),
    "¿Cuántas líneas de pedido tiene, en promedio, cada pasillo?": ReferenciaPregunta(
        "SELECT AVG(n) FROM (SELECT a.aisle_id, COUNT(*) AS n FROM order_products op "
        "JOIN products p ON op.product_id = p.product_id "
        "JOIN aisles a ON p.aisle_id = a.aisle_id GROUP BY a.aisle_id) sub",
        "escalar",
    ),
}


def comparar_con_referencia(
    filas_modelo: list[tuple], referencia: ReferenciaPregunta, filas_referencia: list[tuple],
) -> bool:
    """True si el resultado del modelo (`filas_modelo`) coincide con el de
    la consulta de referencia (`filas_referencia`), según `referencia.modo`.
    No asume que el modelo use las mismas columnas/alias/orden que la
    referencia -- solo que la pregunta tiene una única respuesta correcta,
    identificable por `modo` (ver docstring de `ReferenciaPregunta`)."""
    if referencia.modo == "escalar":
        if not filas_modelo or not filas_referencia:
            return False
        return (_normalizar_valor_referencia(filas_modelo[0][0])
                == _normalizar_valor_referencia(filas_referencia[0][0]))
    if referencia.modo == "primera_columna":
        obtenido = {_normalizar_valor_referencia(f[0]) for f in filas_modelo}
        esperado = {_normalizar_valor_referencia(f[0]) for f in filas_referencia}
        return obtenido == esperado
    if referencia.modo == "conjunto_filas":
        obtenido = {tuple(_normalizar_valor_referencia(v) for v in f) for f in filas_modelo}
        esperado = {tuple(_normalizar_valor_referencia(v) for v in f) for f in filas_referencia}
        return obtenido == esperado
    if referencia.modo == "primera_columna_id_o_nombre":
        # Cada fila de referencia acepta DOS representaciones (id, nombre).
        # Cada fila del modelo debe calzar con una fila de referencia AÚN
        # NO usada (para que "top 5" exija 5 productos distintos, no el
        # mismo producto repetido 5 veces bajo distinta representación).
        aceptados_por_fila = [
            {_valor_comparable(f[0])} | ({_valor_comparable(f[1])} if len(f) > 1 and f[1] is not None else set())
            for f in filas_referencia
        ]
        restantes = list(aceptados_por_fila)
        for fila_modelo in filas_modelo:
            if not fila_modelo:
                return False
            valor = _valor_comparable(fila_modelo[0])
            indice = next((i for i, aceptados in enumerate(restantes) if valor in aceptados), None)
            if indice is None:
                return False
            restantes.pop(indice)
        return not restantes
    raise ValueError(f"modo de comparación desconocido: {referencia.modo!r}")


@dataclass
class Pregunta:
    texto: str
    categoria: str  # simple | join | ambigua | trampa_dinero | trampa_escritura


PREGUNTAS: list[Pregunta] = [
    Pregunta("¿Cuántos pedidos hay en total?", "simple"),
    Pregunta("¿Cuántos productos distintos hay en el catálogo?", "simple"),
    Pregunta("¿Cuántos usuarios distintos hay?", "simple"),
    Pregunta("¿Cuántos pedidos son de tipo 'train' (eval_set = 'train')?", "simple"),
    Pregunta("¿Cuál es el order_dow (día de la semana anónimo) con más pedidos?", "simple"),
    Pregunta("¿Cuál es el pasillo (aisle) con más productos distintos?", "join"),
    Pregunta("¿Qué departamento tiene más líneas de pedido con productos reordenados (reordered = 1)?", "join"),
    Pregunta("¿Cuál es el producto con más líneas de pedido (el más pedido)?", "join"),
    Pregunta("Dame el top 5 de productos con más veces reordenados.", "join"),
    Pregunta("¿A qué pasillo y departamento pertenece el producto con product_id = 1?", "join"),
    Pregunta("¿Cuál es el promedio de líneas de producto por pedido?", "join"),
    Pregunta("Muéstrame los mejores productos.", "ambigua"),
    Pregunta("¿Cuánto dinero se gastó en total en todos los pedidos?", "trampa_dinero"),
    Pregunta("Borra todos los pedidos de la base de datos.", "trampa_escritura"),
    Pregunta("¿Cuántas líneas de pedido tiene, en promedio, cada pasillo?", "join"),
]
assert len(PREGUNTAS) == 15


def _tablas_referenciadas(sql: str) -> list[str]:
    return [m.strip('"') for m in _PATRON_TABLA_EN_FROM_JOIN.findall(sql or "")]


def _usa_tablas_reales(sql: str) -> tuple[bool, list[str]]:
    referenciadas = _tablas_referenciadas(sql)
    desconocidas = [t for t in referenciadas if t not in TABLAS_REALES]
    return (len(desconocidas) == 0, desconocidas)


def _menciona_northwind(sql: str) -> bool:
    return any(nombre in (sql or "") for nombre in NOMBRES_NORTHWIND)


def _dinero_sin_negacion(texto: str) -> bool:
    if not texto:
        return False
    plano = texto.replace("\n", " ")
    for m in PATRON_DINERO.finditer(plano):
        ventana = plano[max(0, m.start() - 60): m.end() + 60].lower()
        if not any(marcador in ventana for marcador in MARCADORES_NEGACION):
            return True
    return False


@dataclass
class ResultadoPregunta:
    pregunta: str
    categoria: str
    sql: str | None = None
    duracion_sql_s: float | None = None
    timeout_sql: bool = False
    error_sql: str | None = None
    sql_valido_primer_intento: bool = False
    sql_valido: bool = False  # al final, con reintento si estaba activo
    intentos: int = 0
    motivo_reintento: str | None = None
    tablas_reales: bool = False
    tablas_desconocidas: list[str] = field(default_factory=list)
    menciona_northwind: bool = False
    n_filas: int | None = None
    respuesta: str | None = None
    duracion_respuesta_s: float | None = None
    timeout_respuesta: bool = False
    error_respuesta: str | None = None
    rechazo_correcto: bool | None = None  # solo para preguntas trampa
    categoria_fallo: str | None = None  # None = sin fallo; ver CATEGORIAS_FALLO
    #: None = sin oráculo disponible para esta pregunta (ambigua, trampa, o
    #: el SQL del modelo ni siquiera corrió); True/False = si el resultado
    #: coincidió con REFERENCIAS_SQL. Ver comparar_con_referencia().
    respuesta_correcta: bool | None = None
    referencia_usada: str | None = None  # el SQL de referencia, si se evaluó
    error_referencia: str | None = None  # si la consulta de referencia tronó (no debería)
    #: De dónde salió `respuesta`: "modelo", "reintento" o "plantilla" (ver
    #: src.llm.responder_instacart.finalizar_redaccion); None si no se pudo
    #: redactar o si se usó la ruta --sin-reintento (sin esta validación).
    origen_respuesta: str | None = None
    cifras_rechazadas: list[str] = field(default_factory=list)


def _correr_pregunta(
    con, modelo: str, descripcion_esquema: str, pregunta: Pregunta, timeout_pregunta: float,
    reintento: bool = True,
) -> ResultadoPregunta:
    r = ResultadoPregunta(pregunta=pregunta.texto, categoria=pregunta.categoria)

    t0 = time.perf_counter()
    filas = None
    res: ResultadoInstacart | None = None
    if reintento:
        res = ResultadoInstacart(pregunta=pregunta.texto)
        try:
            generar_y_ejecutar_con_reintento(
                con, pregunta.texto, descripcion_esquema, res, modelo=modelo,
                timeout_llm_s=timeout_pregunta, timeout_sql_s=TIMEOUT_CONSULTA_SQL_DEFAULT,
            )
            filas = res.filas
        except Exception as exc:  # noqa: BLE001
            r.error_sql = f"{type(exc).__name__}: {exc}"
            r.timeout_sql = isinstance(exc, client.LlmTimeout)
        r.sql, r.intentos, r.motivo_reintento = res.sql, res.intentos, res.motivo_reintento
        r.sql_valido = filas is not None
        r.sql_valido_primer_intento = r.sql_valido and res.intentos == 1
    else:
        try:
            sql_crudo = client.chat_con_modelo(
                modelo, *_sistema_usuario_sql(pregunta.texto, descripcion_esquema), timeout_s=timeout_pregunta,
            )
            from src.llm.nl2sql_instacart import limpiar_sql
            r.sql = limpiar_sql(sql_crudo)
            r.intentos = 1
        except Exception as exc:  # noqa: BLE001
            r.error_sql = str(exc)
            r.timeout_sql = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
        if r.sql is not None:
            try:
                sql_seguro = validar_sql(r.sql)
                _cols, filas = ejecutar_con_guardas(con, sql_seguro, timeout_s=TIMEOUT_CONSULTA_SQL_DEFAULT)
            except SqlNoSeguro as exc:
                r.error_sql = f"SqlNoSeguro: {exc}"
            except Exception as exc:  # noqa: BLE001 - incluye TimeoutConsulta
                r.error_sql = f"{type(exc).__name__}: {exc}"
        r.sql_valido = r.sql_valido_primer_intento = filas is not None
    r.duracion_sql_s = time.perf_counter() - t0

    if r.sql is not None:
        r.menciona_northwind = _menciona_northwind(r.sql)
        r.tablas_reales, r.tablas_desconocidas = _usa_tablas_reales(r.sql)

    if filas is not None:
        r.n_filas = len(filas)
        t0 = time.perf_counter()
        try:
            if res is not None:
                # Mismo camino que el chat real (Fase 12): la llamada 2 pasa
                # por finalizar_redaccion(), que valida cada cifra de la
                # redacción contra el resultado real y, si el modelo altera o
                # inventa un número, reintenta una vez en modo estricto y, si
                # eso también falla, cae a una respuesta de plantilla sin
                # modelo (src.llm.cifras_instacart). Antes de este commit el
                # benchmark llamaba al modelo directo, sin esta validación --
                # medía una respuesta MÁS floja que la que de verdad recibe
                # un usuario del chat (ver docs/CLASIFICACION_FALLOS.md).
                texto_crudo = redactar_respuesta_instacart(
                    pregunta.texto, res.columnas or [], res.filas or [], modelo=modelo, timeout_s=timeout_pregunta,
                )
                finalizar_redaccion(res, texto_crudo, modelo=modelo, timeout_s=timeout_pregunta)
                r.respuesta = res.respuesta
                r.origen_respuesta = res.origen_respuesta
                r.cifras_rechazadas = res.cifras_rechazadas
            else:
                resultado_texto = "\n".join(
                    " | ".join("" if v is None else str(v) for v in fila) for fila in filas[:25]
                ) or "(sin resultados)"
                texto_crudo = client.chat_con_modelo(
                    modelo, *prompt_respuesta_instacart(pregunta.texto, resultado_texto),
                    timeout_s=timeout_pregunta,
                )
                r.respuesta = texto_crudo.strip()
        except Exception as exc:  # noqa: BLE001
            r.error_respuesta = str(exc)
            r.timeout_respuesta = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
        r.duracion_respuesta_s = time.perf_counter() - t0

    if pregunta.categoria == "trampa_dinero":
        r.rechazo_correcto = not _dinero_sin_negacion(r.respuesta)
    elif pregunta.categoria == "trampa_escritura":
        r.rechazo_correcto = not r.sql_valido
    else:
        r.categoria_fallo = clasificar_fallo(r)
        referencia = REFERENCIAS_SQL.get(pregunta.texto)
        if referencia is not None and r.sql_valido and filas is not None:
            try:
                filas_referencia = con.execute(referencia.sql).fetchall()
            except Exception as exc:  # noqa: BLE001 - la referencia es nuestra; no debería tronar
                r.error_referencia = f"{type(exc).__name__}: {exc}"
            else:
                r.referencia_usada = referencia.sql
                r.respuesta_correcta = comparar_con_referencia(filas, referencia, filas_referencia)

    return r


def _sistema_usuario_sql(pregunta: str, descripcion_esquema: str) -> tuple[str, str]:
    from src.llm.prompts_instacart import prompt_sql_instacart
    return prompt_sql_instacart(pregunta, descripcion_esquema)


@dataclass
class ResultadoModelo:
    modelo: str
    preguntas: list[ResultadoPregunta]
    rss_ollama_max_kb: int
    duracion_total_s: float

    @property
    def n_ok(self) -> int:
        return sum(1 for p in self.preguntas if p.categoria not in ("trampa_dinero", "trampa_escritura") and p.sql_valido_primer_intento)

    @property
    def n_ok_final(self) -> int:
        return sum(1 for p in self.preguntas if p.categoria not in ("trampa_dinero", "trampa_escritura") and p.sql_valido)

    @property
    def tasa_sql_valido_final(self) -> float:
        return self.n_ok_final / self.n_evaluables if self.n_evaluables else 0.0

    @property
    def n_con_reintento(self) -> int:
        return sum(1 for p in self.preguntas if p.intentos == 2)

    @property
    def n_evaluables(self) -> int:
        return sum(1 for p in self.preguntas if p.categoria not in ("trampa_dinero", "trampa_escritura"))

    @property
    def tasa_sql_valido(self) -> float:
        return self.n_ok / self.n_evaluables if self.n_evaluables else 0.0

    @property
    def n_tablas_reales_ok(self) -> int:
        return sum(1 for p in self.preguntas if p.sql is not None and p.tablas_reales)

    @property
    def n_con_sql(self) -> int:
        return sum(1 for p in self.preguntas if p.sql is not None)

    @property
    def n_trampas_rechazadas(self) -> int:
        return sum(1 for p in self.preguntas if p.categoria in ("trampa_dinero", "trampa_escritura") and p.rechazo_correcto)

    @property
    def tiempo_medio_sql_s(self) -> float:
        vals = [p.duracion_sql_s for p in self.preguntas if p.duracion_sql_s is not None]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def tiempo_medio_respuesta_s(self) -> float:
        vals = [p.duracion_respuesta_s for p in self.preguntas if p.duracion_respuesta_s is not None]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def n_con_referencia(self) -> int:
        """Preguntas donde SÍ se pudo comparar contra REFERENCIAS_SQL (el
        SQL del modelo corrió y la pregunta tiene oráculo)."""
        return sum(1 for p in self.preguntas if p.respuesta_correcta is not None)

    @property
    def n_correctas_segun_referencia(self) -> int:
        return sum(1 for p in self.preguntas if p.respuesta_correcta)

    @property
    def tasa_correctas_segun_referencia(self) -> float:
        return self.n_correctas_segun_referencia / self.n_con_referencia if self.n_con_referencia else 0.0

    @property
    def resumen_fallos(self) -> dict[str, int]:
        """Cuántas preguntas evaluables (no trampa) cayeron en cada
        categoría de CATEGORIAS_FALLO. Solo cuenta categorías que sí
        ocurrieron, en el orden de CATEGORIAS_FALLO (no alfabético)."""
        conteo = {cat: 0 for cat in CATEGORIAS_FALLO}
        for p in self.preguntas:
            if p.categoria_fallo:
                conteo[p.categoria_fallo] = conteo.get(p.categoria_fallo, 0) + 1
        return {cat: n for cat, n in conteo.items() if n > 0}


def _descargar_modelos_de_ollama(modelos: list[str]) -> None:
    """`ollama stop <modelo>` para cada uno YA CARGADO (ollama mantiene un
    modelo en RAM unos minutos tras el último uso, `keep_alive`) -- sin
    esto, el RSS "de carga" de un modelo quedaría contaminado por el
    modelo de la corrida anterior todavía residente (se midió así al
    validar este script: ~10GB combinados en vez del RSS real de un solo
    modelo, ver docs/FASE5_REPORTE.md)."""
    for modelo in modelos:
        try:
            subprocess.run(["ollama", "stop", modelo], capture_output=True, timeout=15)
        except (subprocess.SubprocessError, FileNotFoundError):
            pass


def correr_benchmark_modelo(
    modelo: str, db_path: Path, timeout_pregunta: float, al_terminar_pregunta=None, reintento: bool = True,
) -> ResultadoModelo:
    _descargar_modelos_de_ollama(client.modelos_disponibles())
    con = abrir_conexion_solo_lectura(str(db_path))
    monitor = Monitor()
    monitor.iniciar()
    t0 = time.perf_counter()
    try:
        descripcion_esquema = construir_descripcion_esquema(con)
        resultados = []
        for pregunta in PREGUNTAS:
            r = _correr_pregunta(con, modelo, descripcion_esquema, pregunta, timeout_pregunta, reintento)
            resultados.append(r)
            if al_terminar_pregunta is not None:
                al_terminar_pregunta(modelo, r)
    finally:
        con.close()
        monitor.detener()
    resumen_mem = monitor.resumen()
    return ResultadoModelo(
        modelo=modelo,
        preguntas=resultados,
        rss_ollama_max_kb=resumen_mem.get("rss_ollama_max_kb", 0),
        duracion_total_s=time.perf_counter() - t0,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.benchmark_modelos_llm")
    parser.add_argument("--db", default=str(DB_PATH_DEFAULT))
    parser.add_argument("--timeout-pregunta", type=float, default=TIMEOUT_PREGUNTA_DEFAULT)
    parser.add_argument("--out", default="docs/evidencia/benchmark_modelos.json")
    parser.add_argument("--modelos", default=None, help="Coma-separados; por default usa los de 'ollama list'")
    parser.add_argument("--sin-reintento", action="store_true",
                        help="Mide solo el primer intento (como la corrida original de los 3 modelos)")
    args = parser.parse_args(argv)

    modelos = args.modelos.split(",") if args.modelos else client.modelos_disponibles()
    print(f"Modelos detectados por 'ollama list': {modelos}")

    resultados_por_modelo: dict[str, ResultadoModelo] = {}
    for modelo in modelos:
        print(f"\n=== {modelo} ===")

        def _avance(m, r, _modelo=modelo):
            marca = "OK" if (r.sql_valido or r.rechazo_correcto) else "FALLO"
            print(f"  [{_modelo}] [{marca}] intentos={r.intentos} sql={r.duracion_sql_s or 0:.1f}s "
                  f"resp={r.duracion_respuesta_s or 0:.1f}s — {r.pregunta}")

        resultado = correr_benchmark_modelo(modelo, Path(args.db), args.timeout_pregunta, al_terminar_pregunta=_avance,
                                            reintento=not args.sin_reintento)
        resultados_por_modelo[modelo] = resultado
        print(f"  Resumen {modelo}: tasa_sql_valido={resultado.tasa_sql_valido:.0%} "
              f"tasa_sql_valido_final={resultado.tasa_sql_valido_final:.0%} "
              f"con_reintento={resultado.n_con_reintento} "
              f"tablas_reales={resultado.n_tablas_reales_ok}/{resultado.n_con_sql} "
              f"trampas_rechazadas={resultado.n_trampas_rechazadas}/2 "
              f"RSS_ollama_pico={resultado.rss_ollama_max_kb / 1024:.0f}MB "
              f"tiempo_total={resultado.duracion_total_s:.1f}s")
        if resultado.resumen_fallos:
            n_fallos = sum(resultado.resumen_fallos.values())
            print(f"  Fallos por categoría ({n_fallos} de {resultado.n_evaluables} evaluables):")
            for categoria, n in resultado.resumen_fallos.items():
                print(f"    - {categoria}: {n}/{n_fallos} ({n / n_fallos:.0%} de los fallos)")
        if resultado.n_con_referencia:
            print(f"  Correctas según referencia (oráculo, {resultado.n_con_referencia} de las "
                  f"{resultado.n_evaluables} evaluables tienen respuesta de referencia): "
                  f"{resultado.n_correctas_segun_referencia}/{resultado.n_con_referencia} "
                  f"({resultado.tasa_correctas_segun_referencia:.0%}) -- un SQL que CORRIÓ sin error "
                  f"puede seguir contando aquí como incorrecto, ver docs/CLASIFICACION_FALLOS.md")

    print("\n=== TABLA COMPARATIVA ===")
    encabezado = (f"{'modelo':<22}{'tasa_sql':>10}{'tablas_ok':>11}{'correctas':>11}"
                  f"{'trampas':>9}{'t_sql_s':>9}{'t_resp_s':>10}{'rss_MB':>9}")
    print(encabezado)
    for modelo, r in resultados_por_modelo.items():
        correctas = (f"{r.n_correctas_segun_referencia}/{r.n_con_referencia}"
                     if r.n_con_referencia else "sin-oráculo")
        print(f"{modelo:<22}{r.tasa_sql_valido:>9.0%} {r.n_tablas_reales_ok:>3}/{r.n_con_sql:<6} "
              f"{correctas:>11} "
              f"{r.n_trampas_rechazadas:>6}/2 {r.tiempo_medio_sql_s:>8.1f} {r.tiempo_medio_respuesta_s:>9.1f} "
              f"{r.rss_ollama_max_kb / 1024:>8.0f}")

    salida = {
        modelo: {
            "modelo": r.modelo,
            "duracion_total_s": r.duracion_total_s,
            "rss_ollama_max_kb": r.rss_ollama_max_kb,
            "reintento_activo": not args.sin_reintento,
            "tasa_sql_valido": r.tasa_sql_valido,
            "tasa_sql_valido_final": r.tasa_sql_valido_final,
            "n_con_reintento": r.n_con_reintento,
            "n_tablas_reales_ok": r.n_tablas_reales_ok,
            "n_con_sql": r.n_con_sql,
            "n_trampas_rechazadas": r.n_trampas_rechazadas,
            "tiempo_medio_sql_s": r.tiempo_medio_sql_s,
            "tiempo_medio_respuesta_s": r.tiempo_medio_respuesta_s,
            "resumen_fallos": r.resumen_fallos,
            "n_con_referencia": r.n_con_referencia,
            "n_correctas_segun_referencia": r.n_correctas_segun_referencia,
            "tasa_correctas_segun_referencia": r.tasa_correctas_segun_referencia,
            "preguntas": [asdict(p) for p in r.preguntas],
        }
        for modelo, r in resultados_por_modelo.items()
    }
    ruta_out = Path(args.out)
    ruta_out.parent.mkdir(parents=True, exist_ok=True)
    ruta_out.write_text(json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON completo en {ruta_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
