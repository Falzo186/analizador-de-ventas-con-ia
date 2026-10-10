"""Introspección DINÁMICA del esquema de `data/instacart.duckdb` para el
prompt del sistema de la ruta Instacart.

Corrige el defecto que marcó la auditoría en la ruta de Northwind (ver
`docs/AUDITORIA.md`, sección 5): ahí el esquema está escrito a mano dentro
de `src/llm/prompts.py`. Aquí `construir_descripcion_esquema(con)` arma el
bloque de esquema consultando la base de datos real en el momento de
arrancar: `information_schema.tables` para descubrir qué tablas/vistas
existen (nada de una lista de nombres fija), `PRAGMA table_info` para
columnas y tipos, `COUNT(*)` para el tamaño, y `SELECT ... LIMIT 3` para
filas de ejemplo reales.

Caché: mismo patrón de `huella_bd` que `src.dashboard.servicio` (Fase 3) /
`agregados.abrir_agregado` (Fase 2A) para detectar un agregado "rancio" --
aquí, una descripción de esquema desactualizada. Se guarda en
`data/cache/esquema_instacart.json` junto con la `huella_bd` que tenía la
BD al construirse y el texto de `REGLAS_DE_NEGOCIO`; si ambos coinciden, se
sirve del archivo sin tocar la BD más que las 3 consultas baratas de
`huella_bd()`. (HOTFIX 1: antes la clave era solo la huella, y un cambio de
reglas seguía sirviendo la descripción vieja.)
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from src.analytics.conexion import huella_bd

CACHE_PATH_DEFAULT = Path("data/cache/esquema_instacart.json")
FILAS_EJEMPLO = 3

# Reglas de negocio del dataset (verificadas en Fase 1/2A/2B, ver
# docs/FASE1_REPORTE.md y docs/AUDITORIA.md): se listan como reglas
# EXPLÍCITAS que el modelo debe seguir, no como ejemplos sueltos que podría
# pasar por alto. Ninguna es una cifra que cambie con los datos -- son
# propiedades estructurales del dataset (una vista tiene 3 columnas fijas,
# un valor censurado es 30 fijo por diseño de Instacart), no medidas que
# deban recalcularse; por eso se listan como texto fijo, igual que las
# limitaciones fijas de `scripts/generar_ficha_dataset.py`.
REGLAS_DE_NEGOCIO = """\
REGLAS DE NEGOCIO (obligatorias, no las repitas como si fueran una pregunta del usuario)
- La columna eval_set existe SOLO en la tabla orders. La vista
  order_products y las tablas order_products__prior/__train NO tienen
  eval_set: nunca escribas order_products.eval_set ni op.eval_set. Los
  pedidos con eval_set = 'test' no tienen productos, y order_products ya
  cubre solo 'prior' y 'train': si la pregunta es sobre PRODUCTOS de los
  pedidos, usa order_products SIN filtrar por eval_set. Si la pregunta es
  sobre los PEDIDOS en sí (eval_set, order_dow, order_hour_of_day,
  order_number, days_since_prior_order, user_id), consulta la tabla orders,
  que es la única que tiene esas columnas.
- days_since_prior_order está CENSURADO en 30: cualquier intervalo real de
  30 días o más aparece como exactamente 30 en esta columna (no se puede
  distinguir "exactamente 30" de "45" o "90"). Es NULL en el primer pedido
  de cada usuario (no existe un pedido previo, no es un intervalo de 0).
- order_dow (0-6) y order_hour_of_day (0-23) son códigos ANÓNIMOS: no se
  sabe qué día calendario real es el 0, ni en qué huso horario está la
  hora. No traduzcas order_dow a nombres de día ("lunes", "martes") ni
  asumas que la hora es un huso horario específico.
- Este dataset NO TIENE PRECIOS, montos ni ningún dato monetario. Si la
  pregunta pide un ingreso, ganancia, costo o cualquier cifra en dinero,
  NO la inventes: no existe ninguna columna ni forma de calcularla con
  estos datos. Dilo explícitamente en vez de generar SQL.
- Para preguntas sobre "productos de un pedido" o "qué compró alguien",
  usa la VISTA order_products (que ya une order_products__prior UNION ALL
  order_products__train) en vez de referenciar las tablas __prior/__train
  directamente -- salvo que la pregunta pida explícitamente distinguir
  pedidos "prior" (históricos) de pedidos "train" (el más reciente de cada
  usuario con datos de entrenamiento).
- No existe ninguna fecha real (ni de calendario ni de "hoy"): no asumas
  ni inventes un calendario. Todo lo temporal en este dataset es relativo
  (order_number, days_since_prior_order), nunca una fecha absoluta.
- products.aisle_id y products.department_id son LLAVES FORÁNEAS, no el
  nombre del pasillo/departamento. products NO tiene una columna "aisle" ni
  "department" con el texto -- esos nombres están en aisles.aisle y
  departments.department. Para responder con el NOMBRE del pasillo o
  departamento, siempre debes hacer JOIN explícito: products.aisle_id =
  aisles.aisle_id y products.department_id = departments.department_id.
  NUNCA escribas aisles.* ni departments.* en una consulta que no las haya
  unido con un JOIN a esa tabla (es un error de "tabla no referenciada" que
  ya se vio con modelos reales en este proyecto). aisle_id/department_id
  viven en products, NUNCA en order_products/__prior/__train: si la
  pregunta mezcla pedidos con pasillo o departamento, la cadena de JOIN
  SIEMPRE pasa por products primero:
    FROM order_products op
    JOIN products p ON op.product_id = p.product_id
    JOIN aisles a ON p.aisle_id = a.aisle_id
    JOIN departments d ON p.department_id = d.department_id
  (quita las dos últimas líneas si la pregunta no pide pasillo/departamento).
  NUNCA pongas aisle_id/department_id directamente en el JOIN con
  order_products: esa columna no existe ahí.
- "reordenado" / "reordered = 1" se refiere SOLO a las líneas de pedido
  donde reordered = 1 (el usuario ya había comprado ese producto antes). Si
  la pregunta dice "productos reordenados", "veces reordenado" o "líneas
  reordenadas", filtra explícitamente por reordered = 1 -- NO cuentes todas
  las líneas de order_products sin ese filtro (eso cuenta compras nuevas y
  repetidas por igual, una respuesta distinta a lo que se preguntó). Y
  sigue aplicando la regla anterior sobre order_products: usa la VISTA
  order_products (prior+train), no solo order_products__train, salvo que la
  pregunta pida distinguir "prior" de "train" explícitamente.
- "promedio de líneas/productos por pedido" (o "por pasillo", "por
  departamento", "por usuario"...) es un promedio de DOS PASOS, NUNCA
  AVG() de una sola columna. PRIMERO cuenta las líneas de cada grupo con
  GROUP BY, DESPUÉS promedia esos conteos en una subconsulta. La forma es
  SIEMPRE esta (adapta solo la columna de agrupación y los JOIN si hace
  falta llegar a ella, p. ej. pasando por products para agrupar por
  aisle_id):
    SELECT AVG(n) FROM (
      SELECT <columna_del_grupo>, COUNT(*) AS n
      FROM order_products
      GROUP BY <columna_del_grupo>
    ) sub
  NUNCA escribas AVG(product_id), AVG(order_id), AVG(aisle_id) ni
  AVG(add_to_cart_order) -- promediar directamente una columna de ID (o
  cualquier columna que no sea el conteo "n" de la subconsulta) no es el
  promedio que se preguntó, aunque la consulta corra sin error."""


def _tablas_y_vistas(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    """Descubre qué tablas/vistas existen realmente, por introspección (no
    una lista escrita a mano). Devuelve [(nombre, tipo)], tipo en
    {'BASE TABLE', 'VIEW'}."""
    return con.execute(
        "SELECT table_name, table_type FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()


def _describir_tabla(con: duckdb.DuckDBPyConnection, nombre: str, tipo: str) -> str:
    columnas = con.execute(f"PRAGMA table_info('{nombre}')").fetchall()
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    n_filas = con.execute(f"SELECT COUNT(*) FROM {nombre}").fetchone()[0]
    ejemplo = con.execute(f"SELECT * FROM {nombre} LIMIT {FILAS_EJEMPLO}").fetchall()

    etiqueta = "vista" if tipo == "VIEW" else "tabla"
    lineas = [f"{etiqueta.capitalize()} {nombre} ({n_filas:,} filas):"]
    for _cid, col_nombre, col_tipo, _notnull, _dflt, es_pk in columnas:
        marca = "  clave primaria" if es_pk else ""
        lineas.append(f"  {col_nombre}  {col_tipo}{marca}")
    if ejemplo:
        for fila in ejemplo:
            lineas.append("  ej: " + " | ".join("NULL" if v is None else str(v) for v in fila))
    return "\n".join(lineas)


def _construir_sin_cache(con: duckdb.DuckDBPyConnection) -> str:
    bloques = [
        "ESQUEMA REAL de data/instacart.duckdb (obtenido por introspección en el "
        "momento de generar este prompt -- no está escrito a mano, no confíes en "
        "ningún nombre de tabla/columna que no aparezca aquí):",
        "",
    ]
    for nombre, tipo in _tablas_y_vistas(con):
        bloques.append(_describir_tabla(con, nombre, tipo))
        bloques.append("")
    bloques.append(REGLAS_DE_NEGOCIO)
    return "\n".join(bloques)


def construir_descripcion_esquema(
    con: duckdb.DuckDBPyConnection,
    *,
    cache_path: Path = CACHE_PATH_DEFAULT,
    forzar: bool = False,
) -> str:
    """Devuelve el bloque de esquema para el prompt de sistema. Sirve de
    caché en disco si la `huella_bd` guardada coincide con la actual
    (mismo mecanismo que `src.dashboard.servicio.cargar_necesidad`); si no,
    reconstruye por introspección real y reescribe la caché."""
    huella_actual = huella_bd(con)

    if not forzar and cache_path.exists():
        try:
            datos = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            datos = None
        if (datos is not None and datos.get("huella_bd") == huella_actual
                and datos.get("reglas") == REGLAS_DE_NEGOCIO):
            return datos["descripcion"]

    descripcion = _construir_sin_cache(con)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"huella_bd": huella_actual, "reglas": REGLAS_DE_NEGOCIO, "descripcion": descripcion},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return descripcion
