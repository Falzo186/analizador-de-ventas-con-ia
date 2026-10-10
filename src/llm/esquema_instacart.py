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
- NUNCA selecciones ni agrupes por product_name directamente desde
  order_products: esa tabla solo tiene product_id, siempre hay que unir con
  products. Para "productos más/menos vendidos/comprados/pedidos" (o
  cualquier ranking de productos), cuenta filas de order_products y une con
  products por product_id, exactamente así:
  SELECT p.product_name, COUNT(*) AS n
  FROM order_products op
  JOIN products p ON op.product_id = p.product_id
  GROUP BY p.product_name
  ORDER BY n DESC
  LIMIT 10
  (para "menos vendidos" usa ORDER BY n ASC). Igual para pasillos y
  departamentos: el nombre está en aisles.aisle y departments.department;
  se llega con JOIN aisles a ON p.aisle_id = a.aisle_id y
  JOIN departments d ON p.department_id = d.department_id.
- eval_set NO existe en order_products (ni en order_products__prior ni en
  order_products__train): está SOLO en orders. Revisa en el DESCRIBE en qué
  tabla está cada columna antes de usarla; no asumas columnas, verifica
  contra el esquema dado. Para filtrar productos por eval_set une con
  orders: JOIN orders o ON op.order_id = o.order_id ... WHERE o.eval_set = 'train'.
- reordered (en order_products) vale 1 si el usuario YA había comprado ese
  producto antes (recompra) y 0 si es la primera vez. Para "productos que se
  recompran de forma consistente vs. se prueban y se abandonan" calcula la
  tasa de recompra por producto (recompras / veces comprado), exactamente así:
  SELECT p.product_name,
         COUNT(*) AS veces_comprado,
         SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) AS recompras,
         SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS tasa_recompra
  FROM order_products op
  JOIN products p ON op.product_id = p.product_id
  GROUP BY p.product_name
  HAVING COUNT(*) >= 50
  ORDER BY tasa_recompra DESC
  LIMIT 10
  Tasa alta = se recompra consistentemente (ORDER BY tasa_recompra DESC);
  tasa baja = se prueba y se abandona (ORDER BY tasa_recompra ASC).
- Para "productos que NO se recompran" / "se compran una vez y no se
  vuelven a comprar" / "se prueban y se abandonan": agrupa por producto,
  cuenta el total de compras y el total de recompras, y quédate con los que
  tienen recompras = 0, con un mínimo de compras para que sea significativo,
  exactamente así:
  SELECT p.product_name,
         COUNT(*) AS veces_comprado,
         SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) AS recompras
  FROM order_products op
  JOIN products p ON op.product_id = p.product_id
  GROUP BY p.product_name
  HAVING COUNT(*) >= 50 AND SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) = 0
  ORDER BY veces_comprado DESC
  LIMIT 10
  NUNCA ordenes por COUNT(*) WHERE reordered=0 solo -- eso favorece a los
  productos más populares en general, no a los que realmente no se
  recompran. Siempre compara contra el total.
- aisles y departments SOLO se pueden unir a través de products
  (products.aisle_id/products.department_id); NUNCA unas aisles o
  departments directamente con order_products u orders -- siempre
  aisles/departments -> JOIN products -> JOIN order_products.
  MAL (aisle_id no existe en order_products):
  JOIN order_products op ON a.aisle_id = op.aisle_id
  BIEN (productos distintos vendidos por pasillo):
  SELECT a.aisle, COUNT(DISTINCT op.product_id) AS productos_distintos
  FROM aisles a
  JOIN products p ON p.aisle_id = a.aisle_id
  JOIN order_products op ON op.product_id = p.product_id
  GROUP BY a.aisle
  ORDER BY productos_distintos DESC
  LIMIT 10
  Si la pregunta es sobre el CATÁLOGO (cuántos productos tiene cada
  pasillo/departamento), no hace falta order_products: basta
  aisles JOIN products y COUNT(*)."""


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


def describir_columnas(con: duckdb.DuckDBPyConnection) -> str:
    """DESCRIBE compacto (tabla, columna, tipo) de TODAS las tablas/vistas
    reales, sin conteos ni filas de ejemplo: una línea por tabla, para que
    el modelo vea de un vistazo qué columnas tiene cada una (Fase 12)."""
    lineas = ["DESCRIBE (tabla: columna TIPO, ...) -- ninguna columna existe fuera de su tabla:"]
    for nombre, _tipo in _tablas_y_vistas(con):
        cols = con.execute(f"DESCRIBE {nombre}").fetchall()
        # DESCRIBE: (column_name, column_type, null, key, default, extra)
        lineas.append(f"- {nombre}: " + ", ".join(f"{c[0]} {c[1]}" for c in cols))
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
    bloques.append(describir_columnas(con))
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
