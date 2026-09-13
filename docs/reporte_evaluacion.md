# Reporte de evaluación

- **Modelo:** `llama3.1:8b`
- **Fecha:** 2026-09-12 19:40
- **Duración:** 467 s
- **Precisión global:** 6/10 (60.0%)

## Precisión por tipo de pregunta

| Tipo | Aciertos | Total | Precisión |
|---|---|---|---|
| categorias | 0 | 1 | 0.0% |
| clientes | 1 | 2 | 50.0% |
| empleados | 0 | 2 | 0.0% |
| envios | 1 | 1 | 100.0% |
| productos | 3 | 3 | 100.0% |
| proveedores | 1 | 1 | 100.0% |

## Motivos de fallo

| Motivo | Casos |
|---|---|
| SQL mal generado | 3 |
| SQL no ejecutable | 1 |

## Detalle por caso

| # | Tipo | Pregunta | Acierto | Motivo | Verdad | Respuesta del modelo |
|---|---|---|---|---|---|---|
| 1 | productos | ¿Qué productos están descontinuados actualmente? | sí |  | (Chef Anton's Gumbo Mix); (Mishi Kobe Niku); (Alice Mutton); (Guaraná Fantástica); (Rössle Sauerkraut); (Thüringer Rostbratwurst); (Singaporean Hokkien Fried Mee); (Perth Pasties) | Los productos descontinuados actualmente son: Chef Anton's Gumbo Mix, Mishi Kobe Niku, ... |
| 2 | productos | ¿Qué productos tienen el stock (UnitsInStock) por debajo de su punto de reorden (ReorderLevel)? | sí |  | (Chang); (Aniseed Syrup); (Queso Cabrales); (Sir Rodney's Scones); (Nord-Ost Matjeshering); (Gorgonzola Telino); (Mascarpone Fabioli); (Gravad lax); (Ipoh Coffee); (Rogede sild); (Chocolade); (Maxilaku); (Gnocchi di nonna Alice); (Wimmers gute Semmelknödel); (Louisiana Hot Spiced Okra); (Scottish Longbreads); (Outback Lager); (Longlife Tofu) | Los productos con stock por debajo de su punto de reorden son:  Chang (UnitsInStock: 13... |
| 3 | productos | ¿Qué producto genera más ingresos considerando los descuentos aplicados en sus pedidos? | sí |  | Côte de Blaye | El producto que genera más ingresos considerando los descuentos aplicados en sus pedido... |
| 4 | empleados | ¿Qué empleado generó menos pedidos en total? | no | SQL mal generado | Michael Suyama | El empleado con el ID 6 generó 1754 pedidos en total. |
| 5 | empleados | ¿Cómo se comparan las ventas totales entre los 9 empleados? (para decidir si el equipo está balanceado) | no | SQL mal generado | (51488395.196, 48314100.765) | Los empleados con las ventas totales más altas son el 4 con $51,488,395.20 y el 3 con $... |
| 6 | proveedores | ¿Qué proveedor tiene más productos descontinuados? | sí |  | Plutzer Lebensmittelgroßmärkte AG | El proveedor con más productos descontinuados es Plutzer Lebensmittelgroßmärkte AG con ... |
| 7 | categorias | ¿Qué categoría de producto genera más ingresos en total? | no | SQL no ejecutable | Beverages | Error al ejecutar el SQL: (sqlite3.OperationalError) no such column: T2.Quantity [SQL: ... |
| 8 | clientes | ¿Qué clientes no han hecho ningún pedido en el último año registrado en la base? | sí |  | (vacío / NULL) | No hay clientes que no hayan hecho ningún pedido en el último año. |
| 9 | clientes | ¿Qué país concentra más clientes? | no | SQL mal generado | USA | El país que concentra más clientes es Venezuela. |
| 10 | envios | ¿Qué transportista (shipper) tiene el costo de flete promedio más alto? | sí |  | Speedy Express | El transportista con el costo de flete promedio más alto es Speedy Express con un prome... |

## SQL de referencia vs. SQL del modelo

### 1. ¿Qué productos están descontinuados actualmente?
> _8 productos descontinuados en la base. Se espera que la respuesta los nombre (al menos varios de ellos), no solo un conteo._

```sql
-- referencia
SELECT ProductName FROM Products WHERE Discontinued = 1

-- modelo
SELECT ProductName FROM Products WHERE Discontinued = 1
```

### 2. ¿Qué productos tienen el stock (UnitsInStock) por debajo de su punto de reorden (ReorderLevel)?
> _18 productos están por debajo de su ReorderLevel. Pregunta de negocio típica de reabastecimiento: requiere comparar dos columnas de la misma tabla, sin agregación._

```sql
-- referencia
SELECT ProductName FROM Products WHERE UnitsInStock < ReorderLevel

-- modelo
SELECT ProductName FROM Products WHERE UnitsInStock < ReorderLevel
```

### 3. ¿Qué producto genera más ingresos considerando los descuentos aplicados en sus pedidos?
> _Verdad: 'Côte de Blaye'. Obliga a usar la fórmula de ingreso con descuento (UnitPrice*Quantity*(1-Discount)), no solo SUM(Quantity)._

```sql
-- referencia
SELECT p.ProductName FROM Products p JOIN "Order Details" od ON od.ProductID = p.ProductID GROUP BY p.ProductID ORDER BY SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) DESC LIMIT 1

-- modelo
SELECT T1.ProductName, SUM(T2.UnitPrice * T2.Quantity * (1 - T2.Discount)) AS Ingresos FROM Products AS T1 INNER JOIN `Order Details` AS T2 ON T1.ProductID = T2.ProductID GROUP BY T1.ProductID ORDER BY Ingresos DESC LIMIT 1
```

### 4. ¿Qué empleado generó menos pedidos en total?
> _Verdad: Michael Suyama (1754 pedidos, el más bajo de los 9)._

```sql
-- referencia
SELECT e.FirstName || ' ' || e.LastName AS empleado FROM Employees e JOIN Orders o ON o.EmployeeID = e.EmployeeID GROUP BY e.EmployeeID ORDER BY COUNT(*) ASC LIMIT 1

-- modelo
SELECT EmployeeID, COUNT(*) FROM Orders GROUP BY EmployeeID ORDER BY COUNT(*) ASC LIMIT 1
```

### 5. ¿Cómo se comparan las ventas totales entre los 9 empleados? (para decidir si el equipo está balanceado)
> _Pregunta comparativa/abierta: la 'verdad' que se evalúa es el monto del empleado con MÁS ventas y el de MENOS ventas (~51.49M vs ~48.31M, una diferencia de apenas ~6%: el equipo SÍ está balanceado). Se espera que la respuesta mencione ambos montos, no una lista de 9._

```sql
-- referencia
SELECT MAX(total) AS mayor, MIN(total) AS menor FROM (  SELECT e.EmployeeID, SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS total  FROM Employees e JOIN Orders o ON o.EmployeeID = e.EmployeeID  JOIN "Order Details" od ON od.OrderID = o.OrderID  GROUP BY e.EmployeeID)

-- modelo
SELECT Employees.EmployeeID, SUM("Order Details".UnitPrice * "Order Details".Quantity * (1 - "Order Details".Discount)) AS TotalVentas FROM Employees INNER JOIN Orders ON Employees.EmployeeID = Orders.EmployeeID INNER JOIN "Order Details" ON Orders.OrderID = "Order Details".OrderID GROUP BY Employees.EmployeeID
```

### 6. ¿Qué proveedor tiene más productos descontinuados?
> _Verdad: 'Plutzer Lebensmittelgroßmärkte AG' (2 productos descontinuados). Requiere JOIN Suppliers-Products y filtrar por Discontinued antes de agrupar._

```sql
-- referencia
SELECT s.CompanyName FROM Suppliers s JOIN Products p ON p.SupplierID = s.SupplierID WHERE p.Discontinued = 1 GROUP BY s.SupplierID ORDER BY COUNT(*) DESC LIMIT 1

-- modelo
SELECT Suppliers.CompanyName, COUNT(Products.ProductID) FROM Suppliers INNER JOIN Products ON Suppliers.SupplierID = Products.SupplierID WHERE Products.Discontinued = 1 GROUP BY Suppliers.CompanyName ORDER BY COUNT(Products.ProductID) DESC LIMIT 1
```

### 7. ¿Qué categoría de producto genera más ingresos en total?
> _Verdad: 'Beverages'. Cadena de 3 tablas (Categories-Products-"Order Details")._

```sql
-- referencia
SELECT c.CategoryName FROM Categories c JOIN Products p ON p.CategoryID = c.CategoryID JOIN "Order Details" od ON od.ProductID = p.ProductID GROUP BY c.CategoryID ORDER BY SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) DESC LIMIT 1

-- modelo
SELECT T1.CategoryName, SUM(T2.UnitPrice * T2.Quantity * (1 - T2.Discount)) AS Ingresos FROM Categories AS T1 INNER JOIN Products AS T2 ON T1.CategoryID = T2.CategoryID INNER JOIN "Order Details" AS T3 ON T2.ProductID = T3.ProductID INNER JOIN Orders AS T4 ON T3.OrderID = T4.OrderID GROUP BY T1.CategoryName ORDER BY Ingresos DESC LIMIT 1
```

### 8. ¿Qué clientes no han hecho ningún pedido en el último año registrado en la base?
> _Verdad: NINGUNO — el resultado está vacío. Los 93 clientes hicieron al menos un pedido en los últimos 12 meses de datos (2022-10-28 a 2023-10-28). Sirve para ver si el modelo dice correctamente 'ningún cliente' en vez de inventar nombres._

```sql
-- referencia
SELECT c.CompanyName FROM Customers c WHERE c.CustomerID NOT IN (  SELECT DISTINCT o.CustomerID FROM Orders o  WHERE o.OrderDate >= date((SELECT MAX(OrderDate) FROM Orders), '-1 year')    AND o.CustomerID IS NOT NULL)

-- modelo
SELECT CustomerID FROM Customers WHERE CustomerID NOT IN ( SELECT CustomerID FROM Orders WHERE OrderDate >= '2022-10-28' )
```

### 9. ¿Qué país concentra más clientes?
> _Verdad: USA (13 de 93 clientes)._

```sql
-- referencia
SELECT Country FROM Customers GROUP BY Country ORDER BY COUNT(*) DESC LIMIT 1

-- modelo
SELECT T1.Country FROM Customers AS T1 INNER JOIN ( SELECT CustomerID, COUNT(*) AS num FROM Customers GROUP BY CustomerID ) AS T2 ON T1.CustomerID = T2.CustomerID GROUP BY T1.Country ORDER BY T2.num DESC LIMIT 1
```

### 10. ¿Qué transportista (shipper) tiene el costo de flete promedio más alto?
> _Verdad: 'Speedy Express' (flete promedio ~$249.46). Usa Orders.Freight, no requiere tocar "Order Details"._

```sql
-- referencia
SELECT s.CompanyName FROM Shippers s JOIN Orders o ON o.ShipVia = s.ShipperID GROUP BY s.ShipperID ORDER BY AVG(o.Freight) DESC LIMIT 1

-- modelo
SELECT T1.CompanyName, AVG(T2.Freight) FROM Shippers AS T1 INNER JOIN Orders AS T2 ON T1.ShipperID = T2.ShipVia GROUP BY T1.CompanyName ORDER BY AVG(T2.Freight) DESC LIMIT 1
```

---

**Cómo se decide un acierto:** no se compara el SQL carácter por carácter (hay muchas formas válidas de escribir la misma consulta). Se ejecutan ambos SQL y se comparan los NÚMEROS del resultado (exacto para conteos; 1% de tolerancia para dinero y promedios). Luego se verifica que esos números aparezcan en la frase final del modelo. Si el resultado coincide pero la frase no, el fallo se atribuye a la redacción; si el resultado ya difería, al SQL. Ver `docs/arquitectura.md`.