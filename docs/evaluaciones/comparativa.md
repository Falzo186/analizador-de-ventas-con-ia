# Comparativa de modelos — banco de Northwind (10 casos)

Los tres modelos corrieron el mismo banco de 10 preguntas de negocio
(`src/evaluator/banco.py`) contra la misma base real, `data/northwind.db`,
con `python -m src.evaluator.evaluador --modelo <modelo>`. Reportes
completos (SQL generado caso por caso) en:

- [`qwen2.5-coder-1.5b.md`](qwen2.5-coder-1.5b.md)
- [`qwen2.5-coder-14b.md`](qwen2.5-coder-14b.md)
- [`llama3.1-8b.md`](llama3.1-8b.md)

Este documento tiene tres rondas de resultados: la corrida inicial, una
segunda después de arreglar un falso negativo del comparador de texto, y un
experimento puntual sobre el caso 5 después de agregar un ejemplo nuevo al
prompt.

## Ronda 1 → Ronda 2: falso negativo del comparador de texto

**Problema detectado:** Northwind guarda categorías y países en inglés
(`'Beverages'`, `'USA'`), pero le pedimos al modelo que responda en español.
Cuando el modelo traduce correctamente (`'Beverages'` → "Bebidas", `'USA'`
→ "Estados Unidos"), la respuesta es correcta para un humano, pero
`coincide_texto()` la marcaba como fallo porque comparaba contra el valor
literal en inglés.

**Arreglo:** `src/evaluator/comparar.py` ahora incluye
`EQUIVALENCIAS_ES_EN`, un diccionario chico con las traducciones más
comunes de las categorías (`Categories.CategoryName`) y países
(`Customers.Country`) de Northwind, y `coincide_texto()` acepta cualquiera
de las variantes conocidas además del valor original en inglés (ver
`test_coincide_texto_reconoce_categoria_traducida` y
`test_coincide_texto_reconoce_pais_traducido` en `tests/test_evaluator.py`).
También se agregó `"none"` a las frases que `expresa_vacio_o_cero()`
reconoce como respuesta vacía.

**Resultado real de volver a correr las 3 evaluaciones completas** (mismo
prompt, mismos modelos, solo cambió el comparador):

| Modelo | Precisión ronda 1 | Precisión ronda 2 (comparador corregido) | Cambio |
|---|---|---|---|
| `qwen2.5-coder:1.5b` | 7/10 (70.0%) | 7/10 (70.0%) | sin cambio |
| `qwen2.5-coder:14b`  | 7/10 (70.0%) | **9/10 (90.0%)** | **+2** |
| `llama3.1:8b`        | 6/10 (60.0%) | 6/10 (60.0%) | sin cambio |

Solo `qwen2.5-coder:14b` cambió, y cambió mucho: los casos 7 (categoría con
más ingresos) y 9 (país con más clientes) pasaron de "fallo" a "acierto" en
cuanto el comparador dejó de penalizar la traducción. Los otros dos modelos
no se beneficiaron porque sus fallos en esos mismos casos eran errores de
SQL genuinos (columna inexistente, agrupación equivocada), no de
traducción — el comparador no puede arreglar un dato mal calculado.

## Matriz caso por caso (ronda 2, comparador corregido)

✅ = acierto · ❌ = fallo (motivo entre paréntesis)

| # | Pregunta | qwen 1.5b | qwen 14b | llama3.1:8b |
|---|---|---|---|---|
| 1 | Productos descontinuados actualmente | ✅ | ✅ | ✅ |
| 2 | Productos bajo su punto de reorden | ✅ | ✅ | ✅ |
| 3 | Producto con más ingresos (con descuento) | ✅ | ✅ | ✅ |
| 4 | Empleado con menos pedidos | ✅ | ✅ | ❌ (SQL — trajo el ID, no el nombre) |
| 5 | Ventas totales entre los 9 empleados (balance) | ❌ (SQL) | ❌ (SQL) | ❌ (SQL) |
| 6 | Proveedor con más productos descontinuados | ✅ | ✅ | ✅ |
| 7 | Categoría con más ingresos | ❌ (SQL no ejecutable) | ✅ | ❌ (SQL no ejecutable) |
| 8 | Clientes sin pedidos en el último año | ✅ | ✅ | ✅ |
| 9 | País con más clientes | ✅ | ✅ | ❌ (SQL — dijo "Venezuela") |
| 10 | Transportista con flete promedio más alto | ❌ (SQL no ejecutable) | ✅ | ✅ |

## Experimento: ¿un ejemplo en el prompt resuelve el caso 5?

El caso 5 ("¿cómo se comparan las ventas totales entre los 9 empleados?")
es el único que los tres modelos fallaban desde el principio. Requiere
**agregación en dos niveles**: `SUM(...)` por empleado, y LUEGO `MAX`/`MIN`
sobre esos totales ya agrupados — un patrón de SQL que el prompt original
nunca mostraba con un ejemplo.

**Cambio:** se agregó a `src/llm/prompts.py` una sección nueva ("PATRÓN:
comparar el total de cada grupo contra el conjunto") con dos ejemplos
completos: uno con subconsulta + `MAX`/`MIN`/`AVG`, y otro con la función de
ventana `AVG(...) OVER ()` para comparar cada fila contra el promedio
general.

**Método:** para aislar el efecto del prompt, se corrió *solo* el caso 5
(no el banco completo) con los tres modelos, comparando el SQL generado
antes y después del cambio.

### Antes (prompt sin el patrón)

Los tres modelos escribían un `GROUP BY` de un solo nivel — la lista
completa de 9 empleados ordenada, sin ningún `MAX`/`MIN`/`AVG` por encima:

```sql
-- qwen2.5-coder:1.5b (antes)
SELECT e.FirstName, e.LastName, SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS TotalSales
FROM Employees e
JOIN Orders o ON e.EmployeeID = o.EmployeeID
JOIN `Order Details` od ON o.OrderID = od.OrderID
GROUP BY e.EmployeeID
ORDER BY TotalSales DESC

-- qwen2.5-coder:14b (antes) — misma idea, un solo nivel de agregación
SELECT Employees.EmployeeID, Employees.FirstName || ' ' || Employees.LastName AS FullName,
       SUM("Order Details".UnitPrice * "Order Details".Quantity * (1 - "Order Details".Discount)) AS TotalSales
FROM Employees
JOIN Orders ON Employees.EmployeeID = Orders.EmployeeID
JOIN "Order Details" ON Orders.OrderID = "Order Details".OrderID
GROUP BY Employees.EmployeeID, Employees.FirstName, Employees.LastName
ORDER BY TotalSales DESC

-- llama3.1:8b (antes) — igual, un solo nivel
SELECT Employees.EmployeeID, SUM("Order Details".UnitPrice * "Order Details".Quantity * (1 - "Order Details".Discount)) AS TotalVentas
FROM Employees INNER JOIN Orders ON Employees.EmployeeID = Orders.EmployeeID
INNER JOIN "Order Details" ON Orders.OrderID = "Order Details".OrderID
GROUP BY Employees.EmployeeID
```

### Después (prompt con el patrón de dos niveles)

```sql
-- qwen2.5-coder:1.5b (después) — SIGUE en un solo nivel, ignoró el ejemplo
SELECT e.FirstName || ' ' || e.LastName AS empleado,
       SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS total
FROM Employees e
JOIN Orders o ON o.EmployeeID = e.EmployeeID
JOIN "Order Details" od ON od.OrderID = o.OrderID
GROUP BY e.EmployeeID
ORDER BY total DESC
LIMIT 9

-- qwen2.5-coder:14b (después) — ADOPTÓ el patrón casi literal del ejemplo
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
-- resultado: (51488395.196, 48314100.765, 49820737.01883333)  <- los dos
-- primeros números son EXACTOS, coinciden con la verdad (51488395.196,
-- 48314100.765) dígito por dígito.

-- llama3.1:8b (después) — SIGUE en un solo nivel, ignoró el ejemplo
SELECT
  e.EmployeeID, e.FirstName || ' ' || e.LastName AS empleado,
  SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS total
FROM Employees e
JOIN Orders o ON o.EmployeeID = e.EmployeeID
JOIN "Order Details" od ON od.OrderID = o.OrderID
GROUP BY e.EmployeeID
ORDER BY total DESC
```

### Resultado del experimento

| Modelo | ¿Cambió de estrategia SQL? | ¿Números correctos? | ¿Pasa el caso automáticamente? |
|---|---|---|---|
| `qwen2.5-coder:1.5b` | No — sigue en un nivel | Sí (están en la lista de 9) | No (0/3, sin cambio) |
| `qwen2.5-coder:14b`  | **Sí — copió el patrón de dos niveles del ejemplo** | **Sí, exactos** | No (0/3, sin cambio — ver nota) |
| `llama3.1:8b`        | No — sigue en un nivel | Sí (están en la lista de 9) | No (0/3, sin cambio) |

**El ejemplo del prompt sí cambió el comportamiento — pero solo del modelo
más grande.** `qwen2.5-coder:14b` fue el único que reconoció la pregunta
como "necesita el patrón de dos niveles" y reprodujo casi literalmente el
`MAX`/`MIN`/`AVG` sobre subconsulta del ejemplo, calculando los dos números
correctos (`51488395.196` y `48314100.765`) con precisión exacta —algo que
antes del cambio ningún modelo lograba ni remotamente. `qwen2.5-coder:1.5b`
y `llama3.1:8b` ignoraron el patrón nuevo y siguieron con su estrategia de
siempre (listar los 9 totales ordenados), que de casualidad también
contiene los números correctos (son el primero y el último de la lista).

**Por qué el marcador automático sigue en 0/3 para los tres:** el SQL de
referencia del banco (`banco.py`) exige una fila con exactamente 2 números
(`MAX`, `MIN`). Los tres modelos devuelven MÁS de 2 números — la lista
completa de 9 empleados, o el resumen de `MAX`/`MIN`/`AVG` con un tercer
valor (el promedio) — y el comparador (`_mismos_numeros` en
`evaluador.py`) exige que la cantidad de números coincida exactamente, no
solo que los correctos estén presentes. Es una **limitación conocida y sin
corregir del banco**, deliberadamente dejada así en esta ronda para no
mezclar "el prompt ayudó" con "también aflojamos el comparador" en el mismo
experimento. Si se quisiera que el caso 5 pase automáticamente cuando el
modelo acierta el dato, el comparador de resultados numéricos debería
aceptar que la verdad esté *contenida* en la respuesta (igual que ya hace
`coincide_numerico()` con el texto en español), en vez de exigir el mismo
número de valores. Queda pendiente como mejora futura.

## Lecciones por caso

- **Caso 5 — mejoró en capacidad, no en puntaje.** El hallazgo más
  interesante de esta ronda: un ejemplo de un patrón de SQL poco común
  (agregación de dos niveles) en el prompt sí es capaz de cambiar la
  estrategia de un modelo (`qwen2.5-coder:14b` la copió casi literal, y
  llegó al número exacto), pero **solo en el modelo más grande** de los
  tres — ni el de 1.5B ni `llama3.1:8b` lo aprovecharon. El tamaño del
  modelo sí importa para "aprender" un patrón nuevo dentro del contexto
  (few-shot), aunque no cambie el resultado automático del banco.
- **Casos 7 y 9 — el comparador de texto ya no penaliza traducciones.**
  Con `EQUIVALENCIAS_ES_EN`, `qwen2.5-coder:14b` pasó de 7/10 a 9/10. Los
  otros dos modelos no se beneficiaron porque sus SQL en esos casos
  calculaban mal el dato, no solo lo traducían.
- **Los 3 modelos siguen coincidiendo en los casos "fáciles" de una sola
  tabla** (1, 2, 3, 6, 8): descontinuados, stock bajo reorden, ingreso de
  un producto, proveedor con más descontinuados y clientes inactivos.
- **`qwen2.5-coder:14b` es ahora el más preciso (9/10)**, seguido de
  `qwen2.5-coder:1.5b` (7/10, y ~5x más rápido) y `llama3.1:8b` (6/10).

## Conclusión

Con el comparador corregido, `qwen2.5-coder:14b` resuelve 9 de las 10
preguntas de negocio sobre Northwind — el único caso que le falta (5) es
además el único que ningún modelo resuelve automáticamente, aunque el de
14b ya calcula el dato exacto. La mejora del comparador (+2 para el modelo
grande) vino gratis y sin riesgo porque solo afectaba un falso negativo
conocido; la mejora del prompt (el patrón de dos niveles) demostró que SÍ
cambia el comportamiento de un modelo lo bastante grande para aprovecharlo,
aunque no haya bastado para mover el puntaje automático por una limitación
distinta y ya identificada del comparador numérico. Las dos son mejoras
independientes y ninguna oculta a la otra: quedan documentadas por separado
precisamente para que se pueda atribuir cada cambio de puntaje a su causa.
