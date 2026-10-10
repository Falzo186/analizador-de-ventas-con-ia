# Clasificación de fallos del benchmark de modelos (Fase 11)

**Origen:** retroalimentación del profesor Eder Jahir González Bravo en
clase (ver `Resumen_clase_eder.pdf` que compartió un compañero): no basta
con reportar "acertó N de 15", porque dos modelos con la misma tasa de
acierto pueden estar fallando por razones completamente distintas. Pidió
clasificar cada fallo entre error de **ejecución** (el motor rechazó el
SQL: sintaxis, tabla/columna inexistente) y error **semántico** (el SQL
corrió pero no responde lo que se preguntó).

**Dónde vive:** `scripts/benchmark_modelos_llm.py` — función
`clasificar_fallo()`, campo `ResultadoPregunta.categoria_fallo` y
propiedad `ResultadoModelo.resumen_fallos`. Pruebas en
`tests/test_clasificar_fallo_benchmark.py`.

## Las 5 categorías (`CATEGORIAS_FALLO`)

| Categoría | Qué detecta | Cómo |
|---|---|---|
| `tabla_o_columna_inexistente` | El SQL referencia una tabla o columna que no existe | `tablas_desconocidas` (heurística de regex sobre FROM/JOIN, ya existente) + el mensaje real de DuckDB (`Catalog Error`, `does not have a column`...) |
| `error_sintaxis` | El SQL no es SQL válido | `Parser Error` / `syntax error` en el mensaje de DuckDB |
| `agregacion_incorrecta` | El motor rechazó una agregación mal formada | `must appear in the GROUP BY clause`, `not an aggregate` |
| `agregacion_incorrecta_heuristica` | El SQL **sí corrió**, pero agrupa por una columna de identificador (`*_id`/`id`) mientras calcula un agregado | Heurística de regex, ver limitación abajo |
| `otro` | Cualquier otro error (timeout del LLM, out of memory, error no reconocido) | catch-all |

Si `categoria_fallo` es `None`, la pregunta no falló (o es una trampa, que
se sigue evaluando con `rechazo_correcto`, sin cambios).

## Por qué solo esas 5 (y no "filtro_incorrecto")

El profesor también sugirió una categoría `filtro_incorrecto` (p. ej. una
fecha o un estado mal puesto). **Se decidió no incluirla** porque
detectarla con certeza requiere comparar el resultado contra una respuesta
de referencia por pregunta — exactamente el mecanismo que
`docs/EVALUADOR_NORTHWIND_LEGACY.md` ya documenta como **fuera de
alcance** de este proyecto (pendiente para una etapa futura). Sin ese
oráculo, un filtro incorrecto que igual corre y da un resultado con pinta
de válido es indistinguible, desde este script, de una respuesta correcta.

La única excepción es `agregacion_incorrecta_heuristica`: no es un oráculo
general, es el reconocimiento de **un patrón específico ya visto en este
proyecto** (N9_ia.json: el modelo agrupó por `order_id` — una fila casi
por pedido — en vez de calcular la distribución agregada que pide la
pregunta). Se documenta como heurística, no como verdad verificada: puede
tener falsos positivos (un GROUP BY por id que sí sea correcto para esa
pregunta en particular) y falsos negativos (cualquier otro error
semántico que no deje este rastro).

## Ajustes tras la corrida real (Fase 11c)

La Fase 11 se verificó en tres rondas: Fase 11 (lógica por separado),
Fase 11b (comparación `git stash` para confirmar que las fallas
preexistentes de la suite no las causó este cambio) y Fase 11c (corrida
real contra Ollama en vivo, con `qwen2.5:1.5b`). La Fase 11c encontró dos
problemas reales en la primera versión de `clasificar_fallo()`, ambos
corregidos en este commit:

1. **Falso positivo en la heurística.** 2 de 13 preguntas evaluables eran
   consultas "top N"/"el más X" legítimas (agrupan por `product_id` a
   propósito, p. ej. "producto con más líneas de pedido") y la heurística
   original las marcaba como `agregacion_incorrecta_heuristica` solo por
   el patrón de SQL, sin mirar cuántas filas trajo la respuesta. **Fix:**
   se añadió `UMBRAL_FILAS_AGREGACION_SOSPECHOSA = 50` — la heurística
   ahora exige también `n_filas > 50`, porque ningún "top N" de este banco
   de preguntas pide más de 5 filas, mientras que el bug real de N9 trae
   cerca de mil (topadas por `LIMITE_FILAS_DEFAULT` de las guardas de
   seguridad).
2. **Patrón faltante para tabla inexistente.** Un fallo real —
   `Binder Error: Referenced table "departments" not found! Candidate
   tables: "p", "op_train"` — no coincidía con ningún patrón de
   `_PATRON_COLUMNA_O_TABLA_INEXISTENTE` (que solo cubría frases tipo
   "does not have a column"/"column ... not found") y caía en el
   catch-all `otro` en vez de `tabla_o_columna_inexistente`. **Fix:** se
   agregó `"referenced table"` como alternativa en ese patrón.

## Verificación

Las pruebas de `tests/test_clasificar_fallo_benchmark.py` cubren las 5
categorías, el caso sin fallo, el caso sin SQL, `ResultadoModelo.resumen_fallos`
(conteo por categoría, usando datos reales de errores vistos en este
proyecto para los mensajes — no inventados), y — a partir de los hallazgos
de la Fase 11c — pruebas de regresión específicas para los dos fixes
anteriores: los "top N" legítimos (1 y 5 filas) que ya no deben marcarse
como fallo, el límite exacto de `UMBRAL_FILAS_AGREGACION_SOSPECHOSA`
(50 filas no marca, 51 sí), y el mensaje real de "Referenced table".

Corrieron, lógica por lógica, contra la implementación real antes de
este commit (ambas rondas: la versión original en la Fase 11, y esta
versión con los dos fixes). Se escribieron desde una sesión sin
`duckdb`/`ollama` instalados, así que esa verificación lógica no sustituía
una corrida real de `pytest` — eso se cerró en la Fase 11d.

### Fase 11d — cierre del ciclo

Con los dos fixes ya aplicados, se corrió `pytest
tests/test_clasificar_fallo_benchmark.py -v` dentro del entorno real del
proyecto: **19 passed, 0 failed** (los 4 tests nuevos de esta ronda
incluidos). Luego se corrió el benchmark en vivo otra vez con
`qwen2.5:1.5b` (el mismo modelo de la Fase 11c): los fallos evaluables
bajaron de 7 a 5, los 5 restantes cayeron en `tabla_o_columna_inexistente`
(errores reales de binder, no falsos positivos), y se confirmó
puntualmente:

- La pregunta del departamento con más líneas reordenadas (el error
  `Referenced table "departments" not found!`) ahora clasifica como
  `tabla_o_columna_inexistente` (antes `otro`).
- "Producto con más líneas de pedido" (`n_filas=1`, `LIMIT 1`) y "top 5 de
  productos más reordenados" (`n_filas=5`, `LIMIT 5`) ya no se marcan
  (`categoria_fallo: null`) — los dos falsos positivos quedaron
  resueltos.

La suite completa del proyecto (no solo este archivo) corrió con **393
passed, 3 failed, 2 xfailed**. Los 3 fallos no tocan `clasificar_fallo()`
ni el benchmark y no los causó este cambio: uno ya fallaba en la Fase
11b (`test_modelos_cargados_lee_api_ps`), otro es una diferencia de
separador de ruta de Windows en un test ajeno
(`test_construir_ficha_coincide_con_dataset_instacart_md`, `/` vs `\`), y
el tercero es una variación propia del modelo en una pregunta de
`test_coherencia_instacart` (un join incorrecto que generó
`qwen2.5-coder:1.5b` esa corrida, no reproducible de forma determinista).

## Fase 12 — oráculo de referencia y respuestas que CORREN pero no responden bien

Pedido explícito del usuario después de ver el JSON de la Fase 11d: que
el benchmark no solo reporte "corrió sin error", sino que la IA
"conteste y tenga razón, no solo diga porque sí". Revisando ese mismo
JSON con lupa aparecieron **3 respuestas que `clasificar_fallo()` marcaba
como "sin fallo" (`sql_valido=True`, `categoria_fallo=None`) y que en
realidad respondían algo distinto de lo que se preguntó** -- exactamente
el `filtro_incorrecto` que el profesor sugirió y que la Fase 11 dejó
fuera por falta de oráculo:

1. "¿Cuántos pedidos son de tipo 'train'...?": el SQL fue
   `SELECT COUNT(DISTINCT order_id) FROM orders`, SIN `WHERE eval_set =
   'train'` -- contó TODOS los pedidos, no solo los de train.
2. "Dame el top 5 de productos con más veces reordenados.": el SQL
   agrupó por `product_id` y contó TODAS las líneas de
   `order_products__train`, sin filtrar `reordered = 1` -- cuenta compras
   nuevas y repetidas por igual.
3. "¿Cuál es el promedio de líneas de producto por pedido?": el SQL fue
   `SELECT AVG(product_id) FROM order_products__train` -- el promedio de
   un ID de producto, no el promedio de líneas por pedido.

### Qué se agregó

Para 12 de las 15 preguntas del banco (todas menos la ambigua y las 2
trampas, que ya se evalúan aparte) hay una única respuesta correcta, así
que sí se puede escribir a mano el SQL de referencia -- como hacía el
evaluador archivado de Northwind (`docs/EVALUADOR_NORTHWIND_LEGACY.md`),
pero acotado a este banco fijo, no a un banco abierto. Esto amplía lo que
se dejó fuera de alcance en la Fase 11: ahí no había oráculo porque el
banco era abierto; aquí el banco es fijo (15 preguntas conocidas) y
escribir 12 referencias a mano es viable.

- `REFERENCIAS_SQL` (`scripts/benchmark_modelos_llm.py`): diccionario
  pregunta -> `ReferenciaPregunta(sql, modo)`. `modo` decide cómo se
  compara el resultado del modelo contra la referencia sin exigir que
  use las mismas columnas/alias/orden: `"escalar"` (un solo número),
  `"primera_columna"` (el conjunto de valores de la primera columna,
  tolera columnas extra como un conteo) o `"conjunto_filas"` (el
  conjunto de filas completas, para preguntas de varios campos).
- `comparar_con_referencia()`: hace la comparación. Pruebas en
  `tests/test_referencias_benchmark.py`, incluidos los 3 casos reales de
  arriba.
- `ResultadoPregunta.respuesta_correcta` (`bool | None`):
  `None` = sin oráculo para esa pregunta, o el SQL del modelo no corrió;
  `True`/`False` = si coincidió con la referencia.
  `ResultadoModelo.n_con_referencia` / `n_correctas_segun_referencia` /
  `tasa_correctas_segun_referencia`: resumen por modelo, impreso en
  consola y en el JSON de salida.

**Limitación documentada:** la comparación es por conjuntos/columna, no
fila-a-fila exacta -- un empate real en un "top 1" (dos productos con
exactamente el mismo conteo) podría dar un falso negativo si el modelo y
la referencia eligieron desempates distintos. No se vio ningún caso así
en las corridas reales hasta ahora.

### Benchmark también más honesto en la llamada 2 (texto de la respuesta)

Al escribir las referencias se encontró algo más: el benchmark llamaba al
modelo de la llamada 2 (resultado -> texto) directo, SIN pasar por
`finalizar_redaccion()` -- el mecanismo que el chat real SÍ usa desde la
Fase 6 para rechazar una redacción que altera o inventa una cifra (ver
`src/llm/cifras_instacart.py`, motivado por un caso real: "3421083" ->
"34,210,83"). Por eso el benchmark medía una respuesta MÁS floja que la
que de verdad recibe un usuario del chat. Se corrigió: ahora
`_correr_pregunta()` (cuando `reintento=True`, la ruta por default) usa
`redactar_respuesta_instacart()` + `finalizar_redaccion()`, igual que
`responder_pregunta_instacart()`. Nuevos campos en `ResultadoPregunta`:
`origen_respuesta` ("modelo" / "reintento" / "plantilla") y
`cifras_rechazadas`.

### Dos reglas de negocio nuevas (`src/llm/esquema_instacart.py`)

Motivadas por dos bugs reales de la misma corrida (Fase 11d), distintos
de los 3 de arriba porque ahí el SQL SÍ truena (error de ejecución, ya
cubierto por `tabla_o_columna_inexistente`) -- pero el patrón de fondo es
el mismo tipo de confusión y vale la pena prevenirlo antes de que ocurra,
no solo detectarlo después:

- `products.aisle_id`/`products.department_id` son llaves foráneas, no el
  nombre; hay que hacer JOIN explícito a `aisles`/`departments`. (Un
  modelo real escribió `GROUP BY departments.department` sin JOIN ->
  `Referenced table "departments" not found!`.)
- "reordenado"/`reordered = 1` exige filtrar explícitamente por ese
  valor, no contar todas las líneas.

Pruebas en `tests/test_esquema_instacart.py`
(`test_regla_aisle_department_exige_join_explicito`,
`test_regla_reordered_exige_filtrar_por_1`).

### Fase 13 — verificación real

`pytest tests/test_referencias_benchmark.py tests/test_esquema_instacart.py`
corrió en el entorno real: 24 passed, 1 failed (un test roto por un salto
de línea en medio de la frase que buscaba, no por la regla en sí -- ver
Fase 14). En vivo con `qwen2.5:1.5b`: las 3 respuestas incorrectas
motivadoras SÍ quedaron marcadas `respuesta_correcta: false` (antes
`categoria_fallo: null`, invisibles). Pero las reglas de JOIN nuevas
*todavía no cambiaron el SQL del modelo* -- seguía sin unir
`departments`/`aisles` (mismo error de binder de antes). Una regla en
prosa no bastó; hizo falta un ejemplo literal (Fase 14).

### Fase 14 — ejemplo literal de JOIN: arregla la estructura, no la semántica

Se agregó un patrón de JOIN de 4 líneas, literal, a la regla de
aisle/department (en vez de solo describirlo en prosa), y se corrigió el
test roto comparando contra el texto con espacios colapsados (el `in`
literal se rompía cada vez que la frase buscada caía justo en un salto de
línea del párrafo). Resultado contra `qwen2.5:1.5b`, antes/después:

| Métrica | Fase 13 | Fase 14 |
|---|---|---|
| `tasa_sql_valido` (1er intento) | 69% | **92%** |
| `tasa_sql_valido_final` | 77% | **100%** |
| `resumen_fallos` | `{tabla_o_columna_inexistente: 3}` | `{}` |
| Correctas según referencia | 5/10 (50%) | 5/12 (42%) |

**El ejemplo literal sí resolvió la estructura:** cero errores de binder,
el modelo copia la cadena de JOIN de 3 tablas. **No resolvió la
semántica:** las respuestas correctas NO subieron (siguen en 5, ahora
sobre más preguntas evaluables) -- el modelo pasó de "no corre" a "corre
y está mal". Ejemplos reales de esa misma corrida:
- `COUNT(DISTINCT op.product_id)` en vez de `COUNT(*)` (cuenta productos
  distintos, no líneas) para "departamento con más líneas reordenadas".
- `AVG(p.aisle_id)` sin `GROUP BY` ni subconsulta (promedia un ID) para
  "líneas de pedido por pasillo".
- Sigue usando `order_products__train` en vez de la vista `order_products`
  en varias preguntas, pese a la regla.
- Tradujo `order_dow` a "Lunes" pese a la regla que lo prohíbe
  explícitamente.

**Conclusión (una sola corrida, modelo estocástico -- no es definitiva):**
en un modelo de ~1.5B parámetros, un ejemplo literal en el prompt corrige
la ESTRUCTURA de una consulta (qué tablas unir) pero no su SEMÁNTICA
(qué agregar, cómo). Seguir ajustando el texto del prompt para esta clase
de error tiene rendimientos decrecientes: el límite parece ser la
capacidad del modelo, no la claridad de la instrucción. La métrica que
de verdad importa para este proyecto es **correctas según referencia**,
no `tasa_sql_valido` -- esta última, por sí sola, sobrestima la calidad
(subió a 100% mientras la tasa de respuestas correctas se mantuvo igual o
bajó). Pendiente, si se quiere seguir: repetir la corrida varias veces
(no determinista) y/o comparar contra un modelo más grande para separar
"error de prompt" de "límite de tamaño del modelo".

## Fase 15 — ¿es tamaño del modelo o es el prompt?

Comparación real contra `qwen2.5:1.5b` (Fase 14) con dos modelos más
grandes ya instalados: `qwen2.5-coder:latest` (7B) y `llama3.1:8b`.

| Modelo | SQL válido final | Correctas según referencia | JOIN de 3 tablas (departamento) | Promedio de 2 niveles |
|---|---|---|---|---|
| qwen2.5:1.5b | 100% | 5/12 (42%) | Incorrecto | Incorrecto |
| qwen2.5-coder:latest (7B) | 92% | **9/11 (82%)** | Correcto | Incorrecto |
| llama3.1:8b | 92% | 8/12 (67%) | Correcto | Incorrecto |

**Sí es (en parte) tamaño:** ambos modelos de 7-8B resuelven el JOIN de 3
tablas con `COUNT(*)` y la vista `order_products` que el 1.5B no lograba;
las correctas según referencia suben de 42% a 67-82%.

**No es SOLO tamaño:** los 3 modelos -- 1.5B, 7B y 8B -- fallan el mismo
tipo de pregunta con el mismo patrón: un promedio de DOS niveles
("promedio de líneas por pedido/pasillo", que exige agrupar primero y
promediar el conteo después) sale como `AVG(<una_columna>)` directo, sin
subconsulta ni `GROUP BY` intermedio -- `AVG(product_id)`,
`AVG(order_id)`, `AVG(add_to_cart_order)` según el modelo, pero siempre
el mismo error de fondo. Ningún tamaño probado lo resuelve. Hipótesis sin
probar: al prompt le falta un ejemplo literal de agregación en dos
pasos, igual que la Fase 14 solo resolvió el JOIN cuando se agregó un
ejemplo literal (no una regla en prosa).

**Bug real encontrado en el propio oráculo (ya corregido en este
commit):** `qwen2.5-coder:latest` identificaba bien "el producto con más
líneas de pedido", pero devolvía `product_name` ("Bag of Organic
Bananas") en vez de `product_id`. El modo `"primera_columna"` los
comparaba como valores distintos y marcaba la respuesta como incorrecta
-- un falso negativo del oráculo por REPRESENTACIÓN, no por contenido. Se
agregó el modo `"primera_columna_id_o_nombre"`: la referencia para esas 2
preguntas ("producto más pedido" y "top 5 reordenados") ahora trae
`(product_id, product_name)` por fila, y una fila del modelo coincide si
su primer valor es el id O el nombre (sin permitir que un mismo producto
repetido cuente como varios distintos en un "top 5"). Pruebas en
`tests/test_referencias_benchmark.py`.

**Pendiente:** correr `comparar_con_referencia()` de nuevo sobre los
JSON ya guardados (`benchmark_fase15_prueba.json`,
`benchmark_fase15b_llama_prueba.json`) para ver si "producto más pedido"
cambia de `false` a `true` ahora que el oráculo acepta el nombre; probar
la hipótesis del ejemplo literal de agregación en dos pasos; y, como
siempre, repetir corridas -- son modelos estocásticos y esto es una sola
medición por modelo.

### Fase 17 — ejemplo literal de subconsulta (promedio de dos pasos)

Hipótesis de la Fase 15/16: el fallo `AVG(<columna>)` en los dos promedios de
dos niveles se debía a que el prompt no traía un patrón "conteo por grupo y
luego `AVG` en una subconsulta". Se agregó ese patrón literal
(`SELECT AVG(n) FROM (SELECT <grupo>, COUNT(*) AS n FROM order_products GROUP BY <grupo>) sub`)
a `REGLAS_DE_NEGOCIO`. Evidencia: `docs/evidencia/benchmark_fase17_{qwen15b,qwen7b,llama8b}.json`.

| Modelo | Fase 15/16 (correctas según referencia) | Fase 17 | Promedio por pedido | Líneas por pasillo |
|---|---|---|---|---|
| qwen2.5:1.5b | 5/12 (42%) | **4/11 (36%)** | Incorrecta | Incorrecta |
| qwen2.5-coder:latest (7B) | 10/11 (91%) | **12/12 (100%)** | **Correcta** | **Correcta** |
| llama3.1:8b | 10/12 (83%) | **12/12 (100%)** | **Correcta** | **Correcta** |

(Los denominadores varían entre corridas porque solo cuentan las preguntas cuyo
SQL ejecutó.)

SQL de los modelos grandes (idéntico en forma): `SELECT AVG(n) FROM (SELECT
order_id, COUNT(*) AS n FROM order_products GROUP BY order_id) sub` y, por
pasillo, `... SELECT a.aisle, COUNT(*) AS n ... JOIN products ... JOIN aisles ...
GROUP BY a.aisle) sub`.

**qwen2.5:1.5b aprendió la FORMA pero no el contenido:**
```sql
-- promedio por pedido: agrupa por product_id en vez de order_id -> incorrecta
SELECT AVG(n) FROM (SELECT product_id, COUNT(*) AS n FROM order_products GROUP BY product_id) sub
-- por pasillo: añade WHERE reordered = 1 que nadie pidió -> incorrecta
SELECT AVG(n) AS avg_lines_per_aisle FROM (SELECT p.aisle_id, COUNT(*) AS n FROM order_products op
  JOIN products p ON op.product_id = p.product_id WHERE reordered = 1 GROUP BY p.aisle_id) sub
```
Además, una pregunta que antes acertaba ("producto más pedido") ahora falla con
`Ambiguous reference to column name "product_id"` (categoría `otro`).

**Conclusión (hipótesis cerrada):**
- **Para 7B/8B, la causa era la falta de un ejemplo de agregación en el prompt,
  no el tamaño**: ambos pasan de fallar los dos promedios a acertarlos y
  llegan a 12/12 según el oráculo.
- **Para 1.5B el ejemplo no basta:** copia la estructura de subconsulta pero elige
  mal la columna de agrupación / agrega filtros. El techo de 1.5B sí es de
  capacidad.
- **Cautela:** los modelos grandes reproducen casi literalmente la plantilla del
  prompt. Funciona para esta forma de pregunta; no demuestra que generalicen a
  otras agregaciones de dos niveles no cubiertas por el ejemplo.
- Una sola corrida por modelo; el 12/12 podría no repetirse siempre.
- Suite completa tras el cambio: 2 failed (preexistentes), 415 passed, 2 xpassed.

## Fase 18 — el modelo que de verdad estaba en producción nunca se midió

Las Fases 11-17 probaron `qwen2.5:1.5b` a fondo. Pero el default real de
producción (`src/llm/client.py`, `MODELO_POR_DEFECTO_INSTACART`) es
**`qwen2.5-coder:1.5b`** -- un modelo distinto, nunca medido con el
oráculo hasta esta fase. Corrida real:

| Modelo | `tasa_sql_valido_final` | Correctas según referencia | Latencia / pregunta (modelo cargado) | RAM/VRAM |
|---|---|---|---|---|
| qwen2.5-coder:1.5b (default anterior) | 92% | 10/12 (**83%**) | 1.64s | 986 MB |
| qwen2.5-coder:latest (7B) | 100% | 12/12 (100%) | 2.30s | 4.7 GB |
| llama3.1:8b | 92% | 12/12 (100%) | 2.06s | 4.9 GB |

El default real ya rendía mucho mejor que el modelo que se estuvo
probando todo este tiempo (83% contra el 36-42% de `qwen2.5:1.5b`) --
las Fases 11-17 fueron una medición válida de la metodología (el oráculo,
las reglas con ejemplos literales), pero no describían el modelo que el
dashboard realmente usa. Máquina de referencia para la latencia/RAM: 32GB
RAM (15.8GB libres), GPU de 8GB VRAM (5.7GB libres), Ollama corriendo
100% en GPU -- estas cifras NO aplican a una máquina sin GPU o con menos
RAM.

## Fase 19 — cambio del modelo de producción

Con los 3 datos de la Fase 18 (corrección del modelo real, latencia por
pregunta, RAM/VRAM disponible), se decidió cambiar
`MODELO_POR_DEFECTO_INSTACART` de `qwen2.5-coder:1.5b` a **`llama3.1:8b`**:
mismo 100% que el otro candidato de 7B, pero más rápido (2.06s contra
2.30s por pregunta). Costo: +4GB de RAM/VRAM y +0.4s por pregunta sobre
el default anterior, que la máquina de referencia (Fase 18) tiene de
sobra. `VENTIA_LLM_MODELO_INSTACART` sigue permitiendo volver al modelo
chico sin tocar código (p. ej. una demo en una máquina sin GPU). Pruebas
en `tests/test_client_modelo_default.py`.

**Limitación documentada:** la decisión se tomó con una sola corrida por
modelo y en una sola máquina (ver Fase 18). Si el proyecto se corre en
otro equipo con menos RAM o sin GPU, hay que remedir antes de confiar en
estos números -- `VENTIA_LLM_MODELO_INSTACART=qwen2.5-coder:1.5b` es la
salida de emergencia para ese caso.

## Cómo se usa

```
python -m scripts.benchmark_modelos_llm --out docs/evidencia/benchmark_modelos.json
```

La salida de consola ahora imprime, por modelo, el desglose de fallos tras
el resumen de siempre (`tasa_sql_valido`, `trampas_rechazadas`...). El JSON
de salida incluye `resumen_fallos` por modelo además de `categoria_fallo`
en cada pregunta individual (`preguntas[].categoria_fallo`).
