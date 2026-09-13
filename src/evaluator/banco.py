"""Banco de preguntas de prueba con su SQL de referencia.

Cada `Caso` tiene:
  - pregunta            : el texto en español que recibiría el modelo
  - sql_referencia      : el SQL "correcto", escrito a mano por nosotros
  - categoria_pregunta  : para desglosar el reporte ("productos", "empleados", ...)
  - modo                : cómo se compara la respuesta ("numero" o "texto")
  - nota                : contexto, sobre todo para las preguntas con matices

El SQL de referencia se ejecuta EN VIVO contra la base, así que la "verdad"
siempre corresponde a los datos reales de `data/northwind.db` (Northwind
Traders). A diferencia del banco anterior (sobre el simulador sintético),
estas 10 preguntas fueron elegidas para cubrir necesidades de negocio típicas
sobre productos, empleados, proveedores, categorías, clientes y envíos, y
CADA SQL de referencia se verificó ejecutándolo contra northwind.db antes de
incluirlo aquí (ver docs/arquitectura.md, sección "Migración a datos reales").

Todas las consultas usan JOIN reales (Northwind no tiene columnas planas
duplicadas como el simulador anterior) y todas fueron confirmadas contra el
esquema exacto de la base: Categories, Products, Suppliers, Customers,
Employees, Shippers, Orders y "Order Details" (con espacio, requiere
comillas dobles en SQL).
"""

from __future__ import annotations

from dataclasses import dataclass

MODO_NUMERO = "numero"
MODO_TEXTO = "texto"

# Ingreso de una línea de pedido, con el descuento ya aplicado.
# Asume que "Order Details" siempre se alía como `od` en las consultas de abajo.
_INGRESO_LINEA = "od.UnitPrice * od.Quantity * (1 - od.Discount)"


@dataclass(frozen=True)
class Caso:
    pregunta: str
    sql_referencia: str
    categoria_pregunta: str
    modo: str = MODO_NUMERO
    nota: str = ""


BANCO: list[Caso] = [
    # --------------------------------------------------------------- productos
    Caso(
        "¿Qué productos están descontinuados actualmente?",
        'SELECT ProductName FROM Products WHERE Discontinued = 1',
        "productos",
        modo=MODO_TEXTO,
        nota="8 productos descontinuados en la base. Se espera que la "
        "respuesta los nombre (al menos varios de ellos), no solo un conteo.",
    ),
    Caso(
        "¿Qué productos tienen el stock (UnitsInStock) por debajo de su punto de reorden (ReorderLevel)?",
        "SELECT ProductName FROM Products WHERE UnitsInStock < ReorderLevel",
        "productos",
        modo=MODO_TEXTO,
        nota="18 productos están por debajo de su ReorderLevel. Pregunta de "
        "negocio típica de reabastecimiento: requiere comparar dos columnas "
        "de la misma tabla, sin agregación.",
    ),
    Caso(
        "¿Qué producto genera más ingresos considerando los descuentos aplicados en sus pedidos?",
        f'SELECT p.ProductName FROM Products p '
        f'JOIN "Order Details" od ON od.ProductID = p.ProductID '
        f'GROUP BY p.ProductID ORDER BY SUM({_INGRESO_LINEA}) DESC LIMIT 1',
        "productos",
        modo=MODO_TEXTO,
        nota="Verdad: 'Côte de Blaye'. Obliga a usar la fórmula de ingreso "
        "con descuento (UnitPrice*Quantity*(1-Discount)), no solo SUM(Quantity).",
    ),

    # -------------------------------------------------------------- empleados
    Caso(
        "¿Qué empleado generó menos pedidos en total?",
        "SELECT e.FirstName || ' ' || e.LastName AS empleado "
        "FROM Employees e JOIN Orders o ON o.EmployeeID = e.EmployeeID "
        "GROUP BY e.EmployeeID ORDER BY COUNT(*) ASC LIMIT 1",
        "empleados",
        modo=MODO_TEXTO,
        nota="Verdad: Michael Suyama (1754 pedidos, el más bajo de los 9).",
    ),
    Caso(
        "¿Cómo se comparan las ventas totales entre los 9 empleados? "
        "(para decidir si el equipo está balanceado)",
        "SELECT MAX(total) AS mayor, MIN(total) AS menor FROM ("
        "  SELECT e.EmployeeID, SUM(" + _INGRESO_LINEA + ") AS total"
        "  FROM Employees e JOIN Orders o ON o.EmployeeID = e.EmployeeID"
        '  JOIN "Order Details" od ON od.OrderID = o.OrderID'
        "  GROUP BY e.EmployeeID"
        ")",
        "empleados",
        nota="Pregunta comparativa/abierta: la 'verdad' que se evalúa es el "
        "monto del empleado con MÁS ventas y el de MENOS ventas (~51.49M vs "
        "~48.31M, una diferencia de apenas ~6%: el equipo SÍ está balanceado). "
        "Se espera que la respuesta mencione ambos montos, no una lista de 9.",
    ),

    # ------------------------------------------------------------- proveedores
    Caso(
        "¿Qué proveedor tiene más productos descontinuados?",
        "SELECT s.CompanyName FROM Suppliers s "
        "JOIN Products p ON p.SupplierID = s.SupplierID "
        "WHERE p.Discontinued = 1 "
        "GROUP BY s.SupplierID ORDER BY COUNT(*) DESC LIMIT 1",
        "proveedores",
        modo=MODO_TEXTO,
        nota="Verdad: 'Plutzer Lebensmittelgroßmärkte AG' (2 productos "
        "descontinuados). Requiere JOIN Suppliers-Products y filtrar por "
        "Discontinued antes de agrupar.",
    ),

    # -------------------------------------------------------------- categorías
    Caso(
        "¿Qué categoría de producto genera más ingresos en total?",
        f'SELECT c.CategoryName FROM Categories c '
        f'JOIN Products p ON p.CategoryID = c.CategoryID '
        f'JOIN "Order Details" od ON od.ProductID = p.ProductID '
        f'GROUP BY c.CategoryID ORDER BY SUM({_INGRESO_LINEA}) DESC LIMIT 1',
        "categorias",
        modo=MODO_TEXTO,
        nota="Verdad: 'Beverages'. Cadena de 3 tablas (Categories-Products-"
        '"Order Details").',
    ),

    # --------------------------------------------------------------- clientes
    Caso(
        "¿Qué clientes no han hecho ningún pedido en el último año registrado en la base?",
        "SELECT c.CompanyName FROM Customers c "
        "WHERE c.CustomerID NOT IN ("
        "  SELECT DISTINCT o.CustomerID FROM Orders o"
        "  WHERE o.OrderDate >= date((SELECT MAX(OrderDate) FROM Orders), '-1 year')"
        "    AND o.CustomerID IS NOT NULL"
        ")",
        "clientes",
        modo=MODO_TEXTO,
        nota="Verdad: NINGUNO — el resultado está vacío. Los 93 clientes "
        "hicieron al menos un pedido en los últimos 12 meses de datos "
        "(2022-10-28 a 2023-10-28). Sirve para ver si el modelo dice "
        "correctamente 'ningún cliente' en vez de inventar nombres.",
    ),
    Caso(
        "¿Qué país concentra más clientes?",
        "SELECT Country FROM Customers "
        "GROUP BY Country ORDER BY COUNT(*) DESC LIMIT 1",
        "clientes",
        modo=MODO_TEXTO,
        nota="Verdad: USA (13 de 93 clientes).",
    ),

    # ---------------------------------------------------------------- envíos
    Caso(
        "¿Qué transportista (shipper) tiene el costo de flete promedio más alto?",
        "SELECT s.CompanyName FROM Shippers s "
        "JOIN Orders o ON o.ShipVia = s.ShipperID "
        "GROUP BY s.ShipperID ORDER BY AVG(o.Freight) DESC LIMIT 1",
        "envios",
        modo=MODO_TEXTO,
        nota="Verdad: 'Speedy Express' (flete promedio ~$249.46). Usa "
        "Orders.Freight, no requiere tocar \"Order Details\".",
    ),
]
