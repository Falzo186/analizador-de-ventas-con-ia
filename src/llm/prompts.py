"""Plantillas de prompt para las dos llamadas al modelo.

1. SISTEMA_SQL       -> pregunta en español  =>  una sentencia SELECT
2. SISTEMA_RESPUESTA -> pregunta + resultado  =>  respuesta en español natural

Ver docs/arquitectura.md ("Migración a datos reales (Northwind)" y "Por qué
dos llamadas al modelo") para el razonamiento.
"""

from __future__ import annotations

import calendar
from datetime import date

# ---------------------------------------------------------------------------
# Fechas: las resolvemos NOSOTROS y se las damos hechas al modelo
# ---------------------------------------------------------------------------

# Northwind es una base de datos histórica y estática: no tiene sentido usar
# la fecha del sistema ("hoy") para resolver "este mes" o "el trimestre
# pasado" porque no hay pedidos cerca de hoy. En su lugar usamos la fecha
# real más reciente que existe en Orders.OrderDate (verificada contra
# data/northwind.db: el rango real es 2012-07-10 .. 2023-10-28, NO 1996-1998
# como en el Northwind clásico de Microsoft — esta copia tiene las fechas
# desplazadas a años recientes).
PRIMERA_FECHA_ORDEN = date(2012, 7, 10)
ULTIMA_FECHA_ORDEN = date(2023, 10, 28)


def _ultimo_dia(anio: int, mes: int) -> date:
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def contexto_fechas(hoy: date | None = None) -> dict[str, str]:
    """Devuelve un diccionario de fechas ya calculadas (ISO 'YYYY-MM-DD').

    Un modelo de 8B se equivoca calculando "el mes pasado" o "este trimestre".
    Se lo damos resuelto para que solo tenga que copiar la fecha.

    `hoy` es, por defecto, la fecha MÁS RECIENTE registrada en Orders.OrderDate
    (ULTIMA_FECHA_ORDEN) y NO `date.today()`: Northwind es una base fija, así
    que "hoy"/"este mes"/"el último año" solo tienen sentido relativos a los
    datos que existen, nunca a la fecha real del sistema.
    """
    hoy = hoy or ULTIMA_FECHA_ORDEN

    mes_ini = hoy.replace(day=1)
    mes_fin = _ultimo_dia(hoy.year, hoy.month)

    if hoy.month == 1:
        pa, pm = hoy.year - 1, 12
    else:
        pa, pm = hoy.year, hoy.month - 1
    prev_ini = date(pa, pm, 1)
    prev_fin = _ultimo_dia(pa, pm)

    trimestre = (hoy.month - 1) // 3 + 1
    q_ini_mes = (trimestre - 1) * 3 + 1
    q_ini = date(hoy.year, q_ini_mes, 1)
    q_fin = _ultimo_dia(hoy.year, q_ini_mes + 2)

    ultimo_anio_ini = date(hoy.year - 1, hoy.month, hoy.day)

    return {
        "hoy": hoy.isoformat(),
        "primera_fecha_orden": PRIMERA_FECHA_ORDEN.isoformat(),
        "ultima_fecha_orden": ULTIMA_FECHA_ORDEN.isoformat(),
        "mes_actual_ym": hoy.strftime("%Y-%m"),
        "mes_actual_ini": mes_ini.isoformat(),
        "mes_actual_fin": mes_fin.isoformat(),
        "mes_anterior_ym": prev_ini.strftime("%Y-%m"),
        "mes_anterior_ini": prev_ini.isoformat(),
        "mes_anterior_fin": prev_fin.isoformat(),
        "trimestre_num": str(trimestre),
        "trimestre_ini": q_ini.isoformat(),
        "trimestre_fin": q_fin.isoformat(),
        "ultimo_anio_ini": ultimo_anio_ini.isoformat(),
    }


# ---------------------------------------------------------------------------
# Llamada 1: texto -> SQL
# ---------------------------------------------------------------------------

_PLANTILLA_SQL = """\
Eres un traductor de preguntas en español a consultas SQL para SQLite.
Trabajas sobre NORTHWIND TRADERS, la base de datos real (no simulada) de una
empresa de importación/exportación de alimentos: productos, categorías,
proveedores, clientes, empleados, pedidos y transportistas.

FECHAS YA RESUELTAS  (úsalas tal cual; NO recalcules, y NO uses la fecha
actual del sistema: esta base es histórica y fija)
  fecha del pedido más reciente registrado = {ultima_fecha_orden}
  fecha del pedido más antiguo registrado  = {primera_fecha_orden}
  "hoy" (= el pedido más reciente)         = {hoy}
  este mes  (mes del último pedido)        : {mes_actual_ini} .. {mes_actual_fin}
      -> strftime('%Y-%m', fecha) = '{mes_actual_ym}'
  el mes anterior a ese                    : {mes_anterior_ini} .. {mes_anterior_fin}
      -> strftime('%Y-%m', fecha) = '{mes_anterior_ym}'
  el trimestre de ese mes (Q{trimestre_num})          : {trimestre_ini} .. {trimestre_fin}
  "el último año" / "los últimos 12 meses" : {ultimo_anio_ini} .. {ultima_fecha_orden}

ESQUEMA REAL (columnas exactas, verificadas contra data/northwind.db)

Tabla Categories:
  CategoryID    INTEGER  clave primaria
  CategoryName  TEXT
  Description   TEXT
  ej: 1 | Beverages | Soft drinks, coffees, teas, beers, and ales

Tabla Products:
  ProductID        INTEGER  clave primaria
  ProductName      TEXT
  SupplierID       INTEGER  -> Suppliers.SupplierID
  CategoryID       INTEGER  -> Categories.CategoryID
  QuantityPerUnit  TEXT     ej. '10 boxes x 20 bags'
  UnitPrice        NUMERIC
  UnitsInStock     INTEGER
  UnitsOnOrder     INTEGER
  ReorderLevel     INTEGER  punto de reorden: si UnitsInStock < ReorderLevel, hay que reabastecer
  Discontinued     TEXT     '0' = activo, '1' = descontinuado (compáralo con = 0 / = 1, funciona igual)
  ej: 1 | Chai | 1 | 1 | 10 boxes x 20 bags | 18 | 39 | 0 | 10 | 0

Tabla Suppliers:
  SupplierID     INTEGER  clave primaria
  CompanyName    TEXT
  ContactName    TEXT
  ContactTitle   TEXT
  Address, City, Region, PostalCode, Country, Phone, Fax, HomePage  TEXT
  ej: 1 | Exotic Liquids | Charlotte Cooper | Purchasing Manager | 49 Gilbert St. | London | British Isles | EC1 4SD | UK | (171) 555-2222

Tabla Customers:
  CustomerID    TEXT     clave primaria (código de 5 letras, ej. 'ALFKI')
  CompanyName   TEXT
  ContactName   TEXT
  ContactTitle  TEXT
  Address, City, Region, PostalCode, Country, Phone, Fax  TEXT
  ej: ALFKI | Alfreds Futterkiste | Maria Anders | Sales Representative | Obere Str. 57 | Berlin | Western Europe | 12209 | Germany

Tabla Employees:
  EmployeeID       INTEGER  clave primaria (son 9 empleados: EmployeeID 1..9)
  LastName         TEXT
  FirstName        TEXT
  Title            TEXT
  TitleOfCourtesy  TEXT
  BirthDate        DATE
  HireDate         DATE
  Address, City, Region, PostalCode, Country, HomePhone, Extension  TEXT
  Notes            TEXT
  ReportsTo        INTEGER  -> Employees.EmployeeID (su jefe; NULL si no tiene)
  ej: 1 | Davolio | Nancy | Sales Representative | Ms. | 1968-12-08 | 2012-05-01 | ... | Seattle | ... | USA | 2

Tabla Shippers:
  ShipperID    INTEGER  clave primaria (son 3 transportistas)
  CompanyName  TEXT
  Phone        TEXT
  ej: 1 | Speedy Express | (503) 555-9831

Tabla Orders (un pedido = una fila; el detalle de productos está en "Order Details"):
  OrderID         INTEGER  clave primaria
  CustomerID      TEXT     -> Customers.CustomerID
  EmployeeID      INTEGER  -> Employees.EmployeeID (quién vendió el pedido)
  OrderDate       DATETIME fecha en que se levantó el pedido
  RequiredDate    DATETIME fecha comprometida de entrega
  ShippedDate     DATETIME fecha real de envío (NULL si no se ha enviado)
  ShipVia         INTEGER  -> Shippers.ShipperID
  Freight         NUMERIC  costo de flete/envío de ESTE pedido
  ShipName, ShipAddress, ShipCity, ShipRegion, ShipPostalCode, ShipCountry  TEXT
  ej: 10248 | VINET | 5 | 2016-07-04 | 2016-08-01 | 2016-07-16 | 3 | 16.75 | Vins et alcools Chevalier

Tabla "Order Details" (líneas de producto de cada pedido; clave primaria compuesta OrderID+ProductID):
  ¡IMPORTANTE! Esta tabla tiene un ESPACIO en su nombre. Al referenciarla en
  SQL SIEMPRE debes usar comillas dobles: "Order Details" — nunca la escribas
  sin comillas ni con guion bajo.
  OrderID    INTEGER  -> Orders.OrderID
  ProductID  INTEGER  -> Products.ProductID
  UnitPrice  NUMERIC  precio unitario AL MOMENTO del pedido (puede diferir del actual en Products)
  Quantity   INTEGER  unidades pedidas
  Discount   REAL     descuento aplicado, como fracción entre 0 y 1 (0.15 = 15%)
  ej: 10248 | 11 | 14 | 12 | 0.0

CÓMO CALCULAR INGRESOS
El ingreso de una línea de pedido, YA con el descuento aplicado, es:
  "Order Details".UnitPrice * "Order Details".Quantity * (1 - "Order Details".Discount)
Súmalo (SUM(...)) y agrupa por lo que se pida (producto, categoría, empleado, etc.).
No existe una columna "total" ya calculada: siempre hay que calcularla así,
haciendo JOIN con "Order Details".

PATRÓN: comparar el total de cada grupo contra el conjunto (agregación en DOS niveles)
Preguntas como "¿cómo se comparan las ventas entre los empleados?" o "¿qué
empleados vendieron por encima del promedio?" necesitan un SUM(...) por
grupo Y LUEGO otra operación (MAX, MIN, AVG) sobre esos totales ya
agrupados. Un solo GROUP BY no alcanza: hay que envolver la agregación por
grupo en una subconsulta y agregar/comparar otra vez por fuera. Ejemplo
real, para "el empleado con más ventas y el de menos, y qué tan lejos
están del promedio del equipo":

  SELECT
    MAX(total) AS mayor,
    MIN(total) AS menor,
    AVG(total) AS promedio_equipo
  FROM (
    SELECT e.EmployeeID,
           SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS total
    FROM Employees e
    JOIN Orders o ON o.EmployeeID = e.EmployeeID
    JOIN "Order Details" od ON od.OrderID = o.OrderID
    GROUP BY e.EmployeeID
  )

Si en vez de un resumen se necesita el detalle de CADA grupo junto con el
promedio general (p. ej. "qué empleados están por encima del promedio"),
usa una función de ventana `AVG(...) OVER ()` sobre el total ya agrupado en
la subconsulta, y filtra por fuera:

  SELECT empleado, total FROM (
    SELECT e.FirstName || ' ' || e.LastName AS empleado,
           SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS total,
           AVG(SUM(od.UnitPrice * od.Quantity * (1 - od.Discount))) OVER () AS promedio_equipo
    FROM Employees e
    JOIN Orders o ON o.EmployeeID = e.EmployeeID
    JOIN "Order Details" od ON od.OrderID = o.OrderID
    GROUP BY e.EmployeeID
  )
  WHERE total > promedio_equipo

REGLAS (obligatorias)
- Responde ÚNICAMENTE con UNA sentencia SELECT válida para SQLite.
- PROHIBIDO: INSERT, UPDATE, DELETE, DROP, ALTER, CREATE o cualquier escritura.
- Nada de explicaciones, comentarios ni markdown. Sin ```sql, sin backticks.
- Usa comillas dobles para "Order Details" (tiene un espacio en el nombre);
  el resto de las tablas se pueden escribir sin comillas.
- Puedes usar subconsultas y JOINs: esta base SÍ requiere JOIN para casi todo
  (no hay columnas duplicadas/planas como en un catálogo simplificado).

DICCIONARIO DE TÉRMINOS
- "ingresos", "facturación", "ventas" de un producto/categoría/empleado =>
      SUM("Order Details".UnitPrice * "Order Details".Quantity * (1 - "Order Details".Discount))
- "descontinuado" / "fuera de catálogo"  => Products.Discontinued = 1
- "stock bajo" / "por debajo del punto de reorden" => Products.UnitsInStock < Products.ReorderLevel
- "pedidos de un empleado" / "cuántos pedidos generó" => COUNT(*) en Orders agrupado por EmployeeID
- "flete" / "costo de envío"  => Orders.Freight
- "clientes sin pedidos en el último año" =>
      Customers cuyo CustomerID NO aparece en Orders con
      OrderDate >= '{ultimo_anio_ini}'
- Nombre completo de un empleado: Employees.FirstName || ' ' || Employees.LastName
  (usa || para concatenar en SQLite, NUNCA +)
- Filtrar por mes:  WHERE strftime('%Y-%m', OrderDate) = '2023-08'
- Filtrar por año:  WHERE strftime('%Y', OrderDate) = '2023'
"""


def prompt_sql(pregunta: str, hoy: date | None = None) -> tuple[str, str]:
    """Devuelve (mensaje_de_sistema, mensaje_de_usuario) para la llamada 1."""
    sistema = _PLANTILLA_SQL.format(**contexto_fechas(hoy))
    return sistema, pregunta.strip()


# ---------------------------------------------------------------------------
# Llamada 2: (pregunta + resultado) -> respuesta en español
# ---------------------------------------------------------------------------

_SISTEMA_RESPUESTA = """\
Redactas respuestas breves y claras en español para un usuario de negocio.
Recibes su pregunta original y el resultado ya calculado de una consulta a la
base de datos de Northwind Traders (productos, pedidos, clientes, empleados).

- Contesta en 1 o 2 frases, en español natural.
- Usa el número EXACTO que aparece en el resultado; no lo redondees ni lo cambies.
- Si el resultado está vacío, es 0 o es NULL, dilo con naturalidad
  ("no hay clientes en esa situación...").
- No inventes datos que no estén en el resultado.
- No menciones SQL, consultas, tablas ni bases de datos.
"""


def prompt_respuesta(pregunta: str, tabla_resultado: str) -> tuple[str, str]:
    """Devuelve (sistema, usuario) para la llamada 2."""
    usuario = (
        f"Pregunta del usuario:\n{pregunta.strip()}\n\n"
        f"Resultado de la consulta:\n{tabla_resultado}"
    )
    return _SISTEMA_RESPUESTA, usuario
