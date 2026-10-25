# Few-shot con «necesidades negativas» (Fase 30)

## Qué es una necesidad negativa

Para cada una de las 10 necesidades (la pregunta original, A.1) hay una o dos preguntas **muy parecidas pero
distintas** (A.2). Tienen la misma estructura o la misma trampa, pero otra métrica, dimensión o filtro, así que
su **respuesta es distinta**. Nunca son la misma pregunta con otras palabras.

Las escribió y resolvió un modelo grande (`claude-opus-5-5`) y están en el código como ejemplos resueltos:
`src/llm/few_shot/ejemplos.json`. Cuando la técnica activa es **few_shot**, el sistema busca las preguntas
resueltas más parecidas a la del usuario y las manda en el prompt del modelo local como ejemplos.

Hay 14 ejemplos: uno por necesidad, y dos para N2, N4, N5 y N10, que son las que más fallaban.

## Catálogo

| id | origen | pregunta | patrón |
|---|---|---|---|
| N1-neg-1 | N1 | ¿Qué clientes han comprado más productos distintos? | Para rankear clientes, agrupa por orders.user_id; si la métrica está en order_products, une orders con order_products por order_id y ordena por la métrica con un desempate. |
| N2-neg-1 | N2 | ¿Qué pasillo tiene la tasa de recompra más alta? | La tasa de recompra de un grupo es la proporción de TODAS sus líneas con reordered = 1 (SUM(CASE ...) * 1.0 / COUNT(*)), no el promedio de las tasas de cada producto; se llega al grupo por products. |
| N2-neg-2 | N2 | ¿Qué departamento tiene la tasa de recompra más baja? | Tasa de un departamento = líneas recompradas / todas sus líneas, en una sola agregación por departments.department (unido por products); para la más baja, ORDER BY tasa ASC. |
| N3-neg-1 | N3 | De los productos del departamento frozen con al menos 100 compras, ¿cuáles tienen la tasa de recompra más alta? | El filtro de grupo (un departamento) va en WHERE antes de agrupar; el mínimo de compras por producto va en HAVING COUNT(*) >= N; luego ORDER BY la tasa. |
| N4-neg-1 | N4 | De los productos con al menos 200 compras, ¿cuáles tienen la tasa de recompra más baja? | Para la tasa más baja, ORDER BY tasa ASC sin excluir las tasas 0 (no agregues HAVING recompras > 0); el único filtro es el mínimo de compras en HAVING COUNT(*) >= N. |
| N4-neg-2 | N4 | Entre los pasillos con al menos 1,000 compras, ¿cuáles tienen la tasa de recompra más baja? | Mismo cálculo de tasa para cualquier grupo; el mínimo de compras va en HAVING y la más baja es ORDER BY tasa ASC, sin filtrar los ceros. |
| N5-neg-1 | N5 | ¿Cuántos productos recomprados tiene en promedio un pedido? | Para un promedio POR PEDIDO, agrupa primero por order_id en una subconsulta (una fila por pedido) y aplica AVG afuera; AVG directo sobre las líneas da otro número. |
| N5-neg-2 | N5 | ¿Cuántos pasillos distintos tiene en promedio un pedido? | Promedio por pedido: subconsulta con GROUP BY order_id que calcula la cifra de cada pedido (aquí COUNT(DISTINCT ...)) y AVG en la consulta de afuera. |
| N6-neg-1 | N6 | ¿Qué día de la semana tiene el menor promedio de días entre pedidos? | days_since_prior_order está en orders (NULL en el primer pedido, censurado en 30); AVG ya ignora los NULL; para comparar grupos, GROUP BY la dimensión y ordena por el promedio. |
| N7-neg-1 | N7 | ¿Qué departamento tiene más productos distintos en el catálogo? | El catálogo es la tabla products (una fila por producto): para contar productos por grupo basta products JOIN departments/aisles y COUNT(*), sin order_products. |
| N8-neg-1 | N8 | ¿Qué día de la semana concentra el mayor volumen de pedidos? | Los pedidos y sus columnas de tiempo (order_dow, order_hour_of_day) están en orders: COUNT(*) por la columna y ORDER BY el conteo DESC; los códigos son anónimos, no los traduzcas. |
| N9-neg-1 | N9 | ¿Qué producto se compra junto con más frecuencia con Organic Whole Milk? | Productos del mismo pedido: une order_products consigo misma por order_id con product_id distintos; sin un producto fijo, agrupa por los dos product_id (a.product_id < b.product_id) y une products solo al final. |
| N10-neg-1 | N10 | ¿Qué porcentaje de los pedidos tiene más de 15 productos? | Para un % de PEDIDOS, resume cada pedido en una subconsulta (GROUP BY order_id) y afuera cuenta los que cumplen con SUM(CASE ...) * 100.0 / COUNT(*); no calcules el % sobre las líneas. |
| N10-neg-2 | N10 | ¿Qué porcentaje de los pedidos tiene al menos un producto recomprado? | Una condición sobre el pedido entero (todas / ninguna / al menos una línea) se decide en la subconsulta por order_id; afuera, el % de pedidos con SUM(CASE ...) * 100.0 / COUNT(*). |

El SQL de cada ejemplo y el resumen real de su resultado están en el JSON. En el dashboard se ven en
«🧩 Necesidades negativas (ejemplos few-shot)», filtrables por necesidad.

## Cómo se crea, verifica y agrega un ejemplo

1. Agrega un objeto a `ejemplos.json` con estos campos: `id` (`N5-neg-3`), `necesidad_origen`, `pregunta`,
   `palabras_clave`, `sql` y `patron`. El patrón es la lección general, en una o dos líneas.
2. Reglas de diseño:
   - Misma estructura o trampa que la necesidad original, pero con otra respuesta.
   - El SQL es **una sola sentencia que empieza con `SELECT`** (la guarda no acepta `WITH`) y usa los JOIN del
     proyecto: `products` es el puente hacia `aisles` y `departments`, y `order_products` es la vista prior+train.
   - Debe terminar dentro del límite de la guarda: 10 s, o 25 s en la familia de N9. En N9 se usa un producto
     ancla, que es barato, en lugar del self-join completo.
3. Corre `python -m scripts.verificar_few_shot --escribir`. Para cada ejemplo, el script:
   - lo ejecuta con la guarda y la conexión read_only reales y exige que devuelva filas;
   - comprueba que su resultado **no coincida** con el de referencia de su necesidad, ni según el comparador
     automático ni porque la fila top-1 tenga un valor clave;
   - guarda en el JSON el resumen real (primeras filas), `modelo_grande`, `verificada: true` y la fecha.
   Sin `--escribir`, solo comprueba. Un ejemplo sin verificar no carga: `few_shot.cargar_ejemplos` lo rechaza.
4. Corre `pytest tests/test_fase30_few_shot.py`. Comprueba que el top-1 de cada necesidad sigue siendo su
   hermana y que no hay fugas.

## Reglas anti-fuga

- Ningún ejemplo usa como filtro o ancla la respuesta de una de las 10 necesidades (46408, dairy eggs,
  Raw Veggie Wrappers, Bananas + Avocado, 21.49 %, la hora 10…). Lo verifica un test.
- El resultado de cada ejemplo difiere del de su necesidad. Lo verifican el script y un test con la BD.
- El bloque que va al prompt lleva la pregunta, el SQL y el patrón, **nunca el resultado**. Para cada una de
  las 10 preguntas, el bloque recuperado no contiene el valor clave de su necesidad. Se comprueba con el mismo
  `valores_protegidos` + `fugas` del test de no-fuga de la Fase 24.

## Cómo funciona la recuperación

- El texto se normaliza: minúsculas, sin acentos, sin stopwords en español, plurales simples y sin tokens de
  un carácter.
- La similitud es **TF-IDF coseno** con TF binario, en Python puro. Se calcula sobre la pregunta y las
  `palabras_clave` de cada ejemplo.
- Se inyectan los **k = 2** ejemplos más parecidos (cámbialo con `VENTIA_FEWSHOT_K`), y solo los que superan
  **`UMBRAL_SIMILITUD = 0.4`**. Si ninguno lo supera, no se inyecta nada y la pregunta se comporta como base.
- Calibración:
  - en las 10 preguntas de las necesidades, la hermana sale top-1 con similitud de 0.45 a 0.85;
  - en preguntas libres, las que solo comparten palabras sueltas («pedidos», «producto») quedan entre 0.14 y
    0.35;
  - «¿cuál es la capital de Francia?» da 0.
- El bloque va en el prompt de sistema de la llamada de SQL: después del esquema y las reglas de negocio, antes
  de las lecciones de la bitácora del chat y antes de la pregunta. La bitácora (dinámica, aprendida) y few-shot
  (estático, curado) conviven sin mezclarse. En autocorrección, los ejemplos van en todos los intentos.

## Cómo activar la técnica

| Dónde | Cómo |
|---|---|
| Dashboard / chat | ⚙️ → «Técnica de contexto» → Few shot (RAG y Harness se ven deshabilitadas) |
| CLI de iteraciones | `python -m src.llm.iteraciones_juez N5 --tecnica few_shot` |
| Matriz | `python -m src.llm.matriz_tecnicas --tecnicas base few_shot` |
| Variable de entorno | `VENTIA_TECNICA=few_shot` |

El valor por defecto **sigue siendo `base`** (`tecnicas.TECNICA_DEFAULT`) hasta que la matriz demuestre
mejora. El interruptor es esa constante, o `VENTIA_TECNICA`.

## Pendiente (el profesor lo indicó para después)

RAG y Harness están registradas en `src/llm/tecnicas.py` como **pendientes**. Se ven en la UI, pero
deshabilitadas, y pedirlas lanza un error claro. **No están implementadas.**

- **RAG:** recuperar contexto con embeddings sobre la documentación del esquema, las reglas de negocio y un
  glosario, en vez de mandar siempre todo el esquema y todas las reglas.
- **Harness:** un bucle de agente con herramientas: hacer `DESCRIBE`, ejecutar un SQL de prueba, leer el error y
  corregir antes de responder. La autocorrección actual (Fases 23-26: el intento siguiente recibe el SQL, el
  error y la pista del juez) es un precursor parcial del harness. Le falta que el modelo decida qué herramienta
  usar y cuándo.
