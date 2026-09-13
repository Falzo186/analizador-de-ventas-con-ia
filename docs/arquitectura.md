# Decisiones de diseño

Documento vivo. Explica **por qué** el código está hecho así, no solo qué hace.

## 1. Entorno y dependencias

Usamos un **entorno virtual** (`.venv/`) para que las librerías de este
proyecto no se mezclen con las de tu sistema ni con otros proyectos. Todo se
ejecuta con `.venv\Scripts\python.exe`.

| Dependencia  | Para qué                                                        |
|--------------|----------------------------------------------------------------|
| `faker`      | Generar valores realistas (nombres, fechas, jitter temporal).   |
| `sqlalchemy` | Hablar con SQLite desde Python sin escribir SQL a mano para el CRUD. |
| `pandas`     | Manipular y comparar resultados tabulares (útil en el evaluador y la UI). |
| `ollama`     | Cliente Python para el modelo local.                            |
| `streamlit`  | UI web mínima sin escribir HTML/JS.                             |

`requirements.txt` = para correr la app. `requirements-dev.txt` añade `pytest`.
Las versiones están **fijadas** (`==`) para que la instalación sea idéntica en
cualquier máquina.

## 2. Base de datos

### Por qué SQLite
Es un solo archivo (`data/ventia.db`), no necesita servidor, y viene con
Python. Perfecto para un proyecto local de un solo usuario.

### Tabla `productos`
Catálogo fijo. Columnas: `id`, `nombre`, `categoria`, `precio_base`.
`categoria` está indexada porque casi toda pregunta filtra por ella.

### Tabla `ventas`
Columnas: `id`, `fecha`, `producto_id`, `producto`, `categoria`, `cantidad`,
`precio_unitario`, `total`.

**Desnormalización parcial (decisión clave).** `ventas` guarda `producto_id`
(la relación "correcta") **y además** copia `producto`, `categoria` y
`precio_unitario` en cada fila. Motivos:

1. **El LLM escribe mejor SQL plano.** Un modelo de 8B acierta mucho más con
   `SELECT SUM(cantidad) FROM ventas WHERE categoria='tecnología'` que con un
   `JOIN`. Como el objetivo es medir su precisión, le quitamos obstáculos.
2. **Una venta es un hecho histórico.** Si mañana cambia el precio de un
   producto, las ventas de ayer deben conservar su precio de ayer. Los
   sistemas de punto de venta reales funcionan así.

El costo (datos duplicados que podrían quedar inconsistentes) lo asumimos: el
generador es el único que escribe, y siempre copia los valores correctos.

### Índices
`fecha`, `categoria` y `producto_id` en `ventas`. Son las columnas por las que
se filtra o agrupa casi siempre.

### `VENTIA_DB_PATH`
Variable de entorno opcional para apuntar a otra base. Las pruebas la usan
para trabajar sobre un archivo temporal y nunca tocar tus datos reales.

## 3. Catálogo de productos

Vive en `src/generator/catalog.py` como una estructura de datos fija (no
aleatoria). **Debe ser reproducible**: si los productos cambiaran en cada
ejecución no podríamos comparar "lo que dijo el LLM" contra "la verdad" de
forma estable.

- 4 categorías: `tecnología`, `ropa`, `alimentos`, `hogar`.
- 13–14 productos por categoría (el enunciado pedía 10–15).
- `PESO_CATEGORIA`: frecuencia relativa de venta. Alimentos se vende ~5x más
  seguido que tecnología.
- `RANGO_CANTIDAD`: unidades típicas por venta según categoría (un SSD casi
  siempre es 1; el arroz puede ser 5).

## 4. Generador de ventas

`src/generator/generator.py`. Dos modos, misma lógica de creación de venta.

### Qué hace que los datos parezcan reales
- **Mezcla de categorías** ponderada por `PESO_CATEGORIA`.
- **Horario de tienda** 9:00–21:00 con dos picos (comida y salida de trabajo),
  definido en el diccionario `HORARIO`.
- **Fines de semana** ~40% más volumen (`MULTIPLICADOR_FINDE`).
- **Variación de precio**: 15% de las ventas con descuento (10–25%), el resto
  con ruido de redondeo ±2%.
- **Cantidad** sesgada a números bajos (distribución triangular).
- `total = round(precio_unitario * cantidad, 2)` siempre coherente.

### Modo `backfill`
Recorre día por día un rango de fechas y genera ~40 ventas/día entre semana
(±25%), más en fin de semana. Para poblar "el histórico".

### Modo `realtime`
Inserta una venta cada `--intervalo` segundos durante `--minutos`, con
`fecha = datetime.now()` y `commit` tras cada una, para simular una tienda
abierta y que la UI muestre "ventas en vivo".

### Semilla (`--semilla`)
Fija la aleatoriedad: dos ejecuciones con la misma semilla y el mismo rango
producen exactamente las mismas ventas. Imprescindible para pruebas y para
poder repetir un experimento de evaluación.

## 5. Pruebas

`tests/` con pytest. `conftest.py` redirige la base a un archivo temporal
**antes** de importar nada de `src.db`, y recrea las tablas vacías antes de
cada prueba. Cubren: forma del catálogo, idempotencia del sembrado, que el
backfill respeta el rango de fechas, reproducibilidad con semilla y coherencia
del `total`.

Las pruebas de `src/llm/` (validación de seguridad, limpieza de SQL,
orquestador) **no llaman al modelo real**: reemplazan `client.chat` por un
doble. Así el `pytest` pasa aunque Ollama esté apagado.

## 6. Traducción pregunta → SQL → respuesta (`src/llm/`)

```
pregunta ─► [modelo #1] ─► SQL ─► validar_sql() ─► [SQLite] ─► filas ─► [modelo #2] ─► respuesta
           nl2sql.py            safety.py          responder.py           responder.py
```

| Archivo         | Responsabilidad                                                    |
|-----------------|-------------------------------------------------------------------|
| `client.py`     | Única puerta a Ollama: modelo, `temperature=0`, error claro si está apagado. |
| `prompts.py`    | Las dos plantillas. La #1 incluye esquema literal, tipos, 3 filas de ejemplo, categorías válidas y la fecha de hoy. |
| `nl2sql.py`     | Llamada #1 + `limpiar_sql()` (quita ```sql, backticks, texto antes del SELECT, `;` final). |
| `safety.py`     | `validar_sql()`: 1 sola sentencia, empieza con SELECT, sin `INSERT/UPDATE/DELETE/DROP/ALTER/...`. Lanza `SqlNoSeguro`. |
| `responder.py`  | Orquesta los 4 pasos, ejecuta el SELECT con SQLAlchemy, llamada #2, y CLI. |

### Por qué `temperature = 0`
Queremos la salida más determinista posible: para una misma pregunta, el
mismo SQL. Facilita depurar y hace que el evaluador (fase siguiente) mida algo
estable, no ruido de muestreo.

### Dos barreras de seguridad, no una
1. `validar_sql()` rechaza cualquier cosa que no sea un `SELECT` limpio.
2. Aun así, `responder.py` ejecuta con `engine.connect()` (sin transacción de
   escritura): si algo se colara, no habría `commit`.
Además, un error de validación se devuelve como `Resultado.error`, nunca como
excepción que tumbe el CLI o la futura UI.

### Por qué SEPARAR "generar SQL" de "redactar respuesta" en dos llamadas

Es tentador pedirle al modelo todo de una vez ("responde la pregunta
consultando la base"). Lo partimos en dos a propósito:

1. **El modelo no puede ejecutar SQL.** Solo genera texto. Necesitamos correr
   la consulta nosotros contra `ventia.db` y devolverle el resultado real. Si
   el modelo "respondiera" en un paso, se estaría *inventando* los números
   (alucinación). Con dos pasos, el número sale de SQLite, no del modelo.

2. **Podemos validar en medio.** Entre la llamada #1 y la #2 pasa
   `validar_sql()`. En un solo paso no hay dónde interponer esa comprobación.

3. **Cada llamada tiene un trabajo y un prompt distintos.** La #1 es
   traducción español→SQL con reglas estrictas ("solo SELECT, sin markdown").
   La #2 es redacción español→español ("1-2 frases, usa el número exacto, no
   inventes"). Un único prompt que hiciera ambas cosas confunde al modelo de
   8B y baja la calidad de las dos.

4. **El evaluador necesita el SQL por separado.** Para medir precisión hay que
   comparar el SQL del modelo (o su resultado) contra una consulta de
   referencia. Si el SQL nunca sale a la superficie, no hay nada que comparar.

5. **Depuración.** Cuando una respuesta está mal, el CLI muestra el SQL
   generado y el resultado crudo: se ve al instante si falló la traducción o
   la redacción.

El costo es una segunda llamada al modelo (~1-2 s más). Para este proyecto,
donde el objetivo es *medir* precisión, esa transparencia vale más que la
latencia.

### CLI

```bash
python -m src.llm.responder "¿cuántos artículos de tecnología se vendieron en agosto?"
```

Imprime: pregunta, SQL generado, resultado crudo y respuesta en español.
`--solo-sql` omite la segunda llamada. El modelo se cambia con la variable de
entorno `VENTIA_LLM_MODELO` (por defecto `llama3.1:8b`).

## 7. Evaluación de precisión (`src/evaluator/`)

Objetivo: poner un número a "¿qué tan bueno es este modelo para responder
preguntas sobre estas ventas?" y poder comparar modelos entre sí.

| Archivo         | Responsabilidad                                                  |
|-----------------|----------------------------------------------------------------|
| `banco.py`      | ~22 `Caso`: pregunta + `sql_referencia` (escrito a mano) + `categoria_pregunta` + `modo` + `nota`. Incluye 3 preguntas ambiguas a propósito. |
| `comparar.py`   | Funciones puras: extraer números de un texto / de un resultado, comparar con tolerancia, detectar "no hubo nada". |
| `evaluador.py`  | Corre cada caso, clasifica el resultado, arma el reporte y el CLI. |

### Cómo se decide un "acierto"

Para cada caso se ejecutan **dos** SQL contra la misma base:

1. `sql_referencia` (el nuestro) → la **verdad**.
2. El SQL que generó el modelo → su **resultado**.

Y se compara en dos niveles:

- **Nivel datos:** ¿el resultado del modelo tiene los mismos números que la
  verdad? Exacto para conteos (`COUNT`, `SUM(cantidad)` llegan como enteros);
  con **1% de tolerancia** para dinero y promedios (`SUM(total)`, `AVG`),
  porque el redondeo puede diferir.
- **Nivel redacción:** ¿esos números aparecen en la frase final en español?
  El modelo no devuelve un número limpio ("Se vendieron 342 artículos..."),
  así que extraemos todos los números del texto (tolerando `1,234` / `1.234`
  / `1 234`) y verificamos que los de la verdad estén ahí.

Hay **acierto** solo si ambos niveles pasan.

Para preguntas cuya respuesta es una **etiqueta** ("¿qué categoría vende más?"
→ `ropa`) el `modo` es `"texto"`: se comprueba que la **frase final** nombre
la etiqueta correcta (normalizada: sin acentos, en minúsculas). En este modo
la forma del resultado del SQL no se exige igual a la de la referencia,
porque una etiqueta se puede obtener de muchas maneras (`GROUP BY ... LIMIT 1`
vs. un `CASE`, `'2026-08'` vs. `'agosto'`, una columna extra...). Si la frase
nombra lo correcto es acierto; si no, el fallo se atribuye al SQL cuando el
resultado ya venía mal, o a la redacción si el resultado era correcto.

Cuando la verdad es `NULL` o `0` (p. ej. "ventas este mes" sin datos de este
mes), se acepta como correcta una respuesta que lo diga en palabras ("no se
registraron ventas..."), y se marca fallo si el modelo **inventa** un número.

### Por qué NO se compara el SQL carácter por carácter

Hay muchísimas formas válidas de escribir la misma consulta:

```sql
SELECT COUNT(*) FROM ventas WHERE categoria = 'ropa'
SELECT COUNT(id) FROM ventas WHERE categoria='ropa'
SELECT COUNT(*) FROM ventas v WHERE v.categoria IN ('ropa')
```

Las tres son correctas y dan el mismo resultado. Comparar texto castigaría
al modelo por elegir un estilo distinto al nuestro, que no es un error.
Lo que importa es **si llega al dato correcto**, y eso solo se sabe
ejecutando. (De hecho en las corridas reales el modelo suele añadir un alias
o una columna extra —`SELECT categoria, SUM(total) ... ORDER BY 2`— y aún así
acierta.)

### Clasificación de los fallos

Separar "el SQL estaba mal" de "el SQL estaba bien pero la frase perdió el
dato" es útil para saber qué mejorar (¿el prompt #1 o el #2?).

| Motivo              | Qué pasó                                                  |
|---------------------|----------------------------------------------------------|
| `SQL mal generado`  | El resultado del SQL del modelo ≠ la verdad.              |
| `redacción final`   | El resultado era correcto, pero la frase no lo refleja.   |
| `SQL no ejecutable` | El SQL del modelo dio error de SQLite.                    |
| `SQL bloqueado`     | `validar_sql()` lo rechazó (no era un SELECT limpio).     |
| `no se generó SQL`  | Normalmente, Ollama apagado.                              |

### Reporte y CLI

```bash
python -m src.evaluator.evaluador --modelo qwen2.5-coder:latest
python -m src.evaluator.evaluador --modelo llama3.1:8b
```

Genera `docs/reporte_evaluacion.md` (precisión global + desglose por tipo +
motivos de fallo + detalle caso por caso + los dos SQL de cada caso) y lo
resume en consola. `--modelo` permite correr el **mismo banco** con distintos
modelos de Ollama y comparar cuál rinde mejor en este dominio. `--limite N`
corre solo los primeros N casos para pruebas rápidas.

### Sobre las preguntas ambiguas

Están en el banco a propósito ("¿qué se vendió más?" sin período ni métrica;
"¿cómo van las ventas este mes?"). No esperamos que el modelo "acierte"
siempre: sirven para **documentar** cómo las interpreta. En la corrida con
`qwen2.5-coder` resolvió bien "¿qué se vendió más?" (producto con más
unidades) pero inventó una cifra en "¿cómo van las ventas este mes?".

## 8. Interfaz web (`src/ui/app.py`)

```bash
.venv\Scripts\streamlit run src/ui/app.py
```

### Por qué Streamlit

Queríamos una interfaz para *usar* el proyecto sin la terminal, con el menor
código posible. Streamlit deja escribir toda la UI en Python plano —sin HTML,
CSS ni JavaScript— y se re-ejecuta el script completo en cada interacción, así
que no hay que gestionar estado de widgets a mano. Para un prototipo /
proyecto de clase es la opción más rápida; el costo (menos control fino sobre
el diseño, un servidor que hay que tener corriendo) no importa aquí.

### Cómo se conecta sin duplicar lógica

`app.py` **no tiene lógica de negocio propia**. Solo llama a lo ya construido:

| Acción en la UI                  | A qué módulo llama                                   |
|----------------------------------|-----------------------------------------------------|
| "Generar venta ahora"            | `src.generator.generator.generar_una_venta()`       |
| "Generar histórico"              | `src.generator.generator.backfill()`                |
| Tabla de últimas ventas          | `SELECT` directo con el `engine` de `src.db`         |
| Selector de modelo               | `src.llm.client.modelos_disponibles()` (`ollama.list()`) |
| "Preguntar"                      | `src.llm.responder.responder_pregunta()`            |
| "Correr evaluación completa"     | `src.evaluator.evaluador.evaluar_banco()`           |

Para que la UI pudiera participar, se añadieron **piezas mínimas y reutilizables**
en los módulos existentes, no copias:

- `generar_una_venta()` en el generador: envuelve `crear_venta()` (la misma
  función que usan el backfill y el modo tiempo real) para una sola venta con
  la hora actual, y devuelve un `dict` en vez del objeto ORM.
- `modelos_disponibles()` en `client.py`: mantiene TODO el trato con `ollama`
  en un solo archivo.
- `client.modelo_actual()` lee `VENTIA_LLM_MODELO` en cada llamada, así la UI
  cambia de modelo con `os.environ[...]` sin reiniciar nada.
- `evaluar_banco(..., on_caso=cb)`: un callback opcional para mover la barra de
  progreso. El bucle de evaluación no se reimplementa en la UI.

### Manejo de errores de Ollama

Si Ollama está apagado o falta el modelo:

- Al **listar modelos**, `modelos_disponibles()` lanza `LlmNoDisponible`; la UI
  lo captura, muestra un aviso amarillo y ofrece un campo de texto para
  escribir el nombre del modelo a mano.
- Al **preguntar**, `responder_pregunta()` ya captura ese fallo internamente y
  lo deja en `Resultado.error`; la UI muestra ese texto en un recuadro rojo.

En ningún caso se ve un traceback de Python en pantalla.

### La raíz del proyecto en `sys.path`

`streamlit run src/ui/app.py` solo pone `src/ui/` en el path, no la raíz, así
que `import src.db...` fallaría. Las primeras líneas de `app.py` añaden la raíz
a `sys.path` (dos carpetas arriba). Es el patrón habitual para un script de
entrada de Streamlit.

## 9. Ampliación del modelo de datos (clientes, proveedores, gastos)

### Por qué se amplió

Con solo `productos` y `ventas`, las preguntas posibles se agotan rápido:
conteos, sumas y filtros sobre una sola tabla. Un punto de venta real maneja
más: **a quién** le vendes, **quién** te surte, y **cuánto te cuesta** operar.
Sin esos datos no se puede preguntar por utilidad (lo que de verdad le importa
a un dueño), ni segmentar por cliente o ciudad, ni comparar el desempeño mes a
mes contra los costos.

También es un mejor banco de pruebas para el LLM: lo obliga a **elegir la
tabla correcta**, a usar `IS NULL` / `IS NOT NULL`, y a escribir restas entre
subconsultas (`ventas - gastos`). Ahí es donde un modelo de 8B empieza a
fallar, que es justo lo que queremos medir.

### Qué se agregó

| Tabla         | Para qué                                                        |
|---------------|----------------------------------------------------------------|
| `clientes`    | id, nombre, ciudad, fecha_registro. ~30 clientes fijos (Faker es_MX con semilla). |
| `proveedores` | id, nombre, categoria_que_provee, contacto. Uno por categoría.  |
| `gastos`      | id, fecha, concepto, categoria_gasto, monto. Renta/nómina/mercancía/servicios, uno de cada uno por mes. |
| `ventas.cliente_id` | FK **nullable** a `clientes`. NULL = venta de mostrador. |

`cliente_id` es nullable a propósito: la mayoría de las ventas de tienda son
anónimas, y así las ventas generadas antes de existir la tabla `clientes`
siguen siendo válidas. Para bases que ya existían, `crear_base_de_datos()`
hace un `ALTER TABLE ventas ADD COLUMN cliente_id` (migración aditiva; ver
`_migraciones_ligeras` en `database.py`).

### Generación realista

- **Clientes por ciudad ponderada:** la lista de ciudades tiene repetidos
  (CDMX ×5, Guadalajara ×4, ...) para que "¿qué ciudad tiene más clientes?"
  tenga una respuesta clara y no un empate.
- **~60% de las ventas** (`PROB_CLIENTE`) se asignan a un cliente al azar; el
  resto quedan de mostrador.
- **Gastos:** la renta es fija mes a mes; nómina, mercancía y servicios se
  sortean dentro de un rango típico. `generar_gastos` es idempotente por
  (mes, concepto).
- **Estacionalidad de mes** (`MULTIPLICADOR_MES`): agosto (regreso a clases)
  y noviembre/diciembre (Buen Fin, Navidad) son altos; enero bajo. Esto hace
  que el backfill de varios meses tenga variación real que comparar — el
  `backfill` ya recorría el rango día a día, solo se le sumó este factor.

### Fechas ya resueltas en el prompt

Un modelo de 8B se equivoca calculando "el mes pasado" o "el trimestre
actual". En vez de pasarle solo "hoy" y confiar en su aritmética,
`contexto_fechas()` calcula y le entrega hechas: hoy, primer y último día del
mes actual y del anterior, y el rango del trimestre. El modelo solo tiene que
**copiar** la fecha correcta al `WHERE`.

### Preguntas de negocio que ahora se pueden hacer

- **Clientes:** cuántos hay, en qué ciudades, qué ciudad concentra más, cuántas
  ventas fueron de mostrador vs. a clientes registrados.
- **Proveedores:** quién surte cada categoría.
- **Gastos:** cuánto se gastó en nómina/renta en un mes, en qué categoría se
  gasta más.
- **Utilidad:** `SUM(ventas.total) - SUM(gastos.monto)` de un mes; si se ganó
  o se perdió.
- **Comparativas:** en qué mes se vendió más, cuánto más que el mes anterior,
  cómo va este mes contra el pasado.

El banco de evaluación (`banco.py`) sumó ~17 casos en estas 5 categorías
nuevas: `clientes`, `proveedores`, `gastos`, `utilidad`, `comparativa`.

## 10. Migración a datos reales (Northwind)

### Por qué se hizo

Hasta aquí, todo el proyecto giraba sobre un simulador (`src/generator/`):
catálogo inventado, ventas generadas con `Faker` + distribuciones a mano,
clientes y proveedores ficticios. Eso sirvió para desarrollar el pipeline
(texto → SQL → resultado → respuesta) y el evaluador sin depender de datos
externos, pero tiene un límite: **mide qué tan bien un LLM traduce preguntas
a SQL contra un esquema que diseñamos nosotros mismos, a la medida del
modelo** (tablas planas, sin JOINs, categorías controladas). Eso no dice
nada de cómo se comporta el mismo modelo contra una base de datos **real**,
con nombres de tabla incómodos, relaciones normalizadas obligatorias y datos
con las inconsistencias propias de un sistema real.

Northwind Traders (`data/northwind.db`, SQLite) es el reemplazo: una base
de datos de negocio real y ampliamente conocida (clientes, empleados,
productos, proveedores, pedidos y envíos de una empresa de importación de
alimentos), con vistas ya armadas y una estructura normalizada de verdad.
El generador sintético (`src/generator/`) **no se borró**: queda como
respaldo por si se necesita reproducir el escenario "esquema simple, a
modo" para depurar el pipeline en aislamiento, pero el flujo principal
(UI, evaluador, prompts) ya no lo usa.

### Lo que reveló inspeccionar el esquema real

Antes de escribir una sola línea se corrió `.schema` contra
`data/northwind.db` y se ejecutaron consultas de verificación (ver
histórico de esta migración). Tres cosas no eran las esperadas y hubiera
sido un error asumirlas:

1. **Las fechas NO son las de 1996-1998** del Northwind clásico de
   Microsoft. Esta copia tiene `Orders.OrderDate` desplazado a
   **2012-07-10 .. 2023-10-28**. `contexto_fechas()` en `prompts.py` usa
   ese rango real (constantes `PRIMERA_FECHA_ORDEN` / `ULTIMA_FECHA_ORDEN`),
   nunca `date.today()`: preguntar "cuántos pedidos hay este mes" solo tiene
   sentido relativo al último pedido registrado, no a la fecha del sistema.
2. **`"Order Details"` tiene un espacio en el nombre de tabla.** SQLite lo
   permite porque la tabla se creó entre corchetes (`[Order Details]`), pero
   cualquier SQL que la referencie sin comillas dobles falla. Esto se
   documentó explícitamente en el prompt del modelo (`prompts.py`) porque un
   LLM de 1.5B-14B tiende a "normalizar" el nombre a `OrderDetails` o
   `Order_Details` si no se le advierte.
3. **`Products.Discontinued` es TEXT, no INTEGER** (`'0'`/`'1'`), aunque por
   afinidad de tipos de SQLite comparar con `= 0` / `= 1` funciona igual
   (verificado). Se documentó en el prompt para que el modelo no dude ni
   intente `= '1'` de forma insegura.

También se confirmó por conteo: 9 empleados, 93 clientes, 77 productos (8
descontinuados), 29 proveedores, 3 transportistas, 16 282 pedidos y 609 283
líneas de pedido.

### Qué cambió en el código

| Área | Cambio |
|---|---|
| `src/db/database.py` | `VENTIA_DB_PATH` ahora apunta por defecto a `data/northwind.db`. `crear_base_de_datos()` detecta ese caso y NO corre `Base.metadata.create_all()`: si lo hiciera, inyectaría las tablas sintéticas (`productos`, `ventas`, ...) dentro del archivo de datos reales. Solo verifica que el archivo exista. Apuntar `VENTIA_DB_PATH` a cualquier otra ruta (p. ej. `data/ventia.db`) sigue creando/migrando el esquema del simulador exactamente como antes. |
| `src/llm/prompts.py` | Esquema completo reescrito con las columnas reales de `Categories`, `Products`, `Suppliers`, `Customers`, `Employees`, `Shippers`, `Orders` y `"Order Details"`, cada una con una fila de ejemplo real. Incluye la nota del espacio en `"Order Details"`, la fórmula de ingreso con descuento (no hay columna `total` precalculada, a diferencia del simulador), y `contexto_fechas()` basado en `ULTIMA_FECHA_ORDEN` en vez de `date.today()`. |
| `src/evaluator/banco.py` | Banco anterior (sintético, ~40 casos) reemplazado por 10 preguntas de negocio sobre Northwind (productos descontinuados, empleado con menos pedidos, comparación de ventas entre los 9 empleados, producto con más ingresos con descuento, proveedor con más descontinuados, categoría con más ingresos, clientes inactivos el último año, país con más clientes, transportista con flete promedio más alto, productos bajo su punto de reorden). **Cada SQL de referencia se ejecutó contra `northwind.db` antes de incluirse** para confirmar que corre sin error y que el resultado es el esperado. |
| `src/evaluator/evaluador.py` | Se cerró un hueco de la lógica de comparación: cuando una pregunta en `modo="texto"` tiene como verdad un resultado VACÍO (p. ej. "¿qué clientes no compraron en el último año?" → ninguno), antes se marcaba acierto automáticamente sin importar la respuesta del modelo (`esperado` vacío hacía que `coincide_texto` devolviera `True` de forma trivial). Ahora ese caso exige, igual que en modo numérico, que el modelo también devuelva 0 filas y que la respuesta lo exprese en palabras (`expresa_vacio_o_cero`). |
| `src/evaluator/comparar.py` | `EQUIVALENCIAS_ES_EN`: diccionario chico con las traducciones más comunes de categorías (`Categories.CategoryName`) y países (`Customers.Country`) de Northwind. `coincide_texto()` ahora acepta cualquiera de esas variantes además del valor original en inglés, para no penalizar una traducción correcta (`'Beverages'` → "Bebidas", `'USA'` → "Estados Unidos"). También se agregó `"none"` a las frases que `expresa_vacio_o_cero()` reconoce. Ver "Segunda ronda" más abajo. |
| `src/ui/app.py` | Se quitó la pestaña "Punto de venta" (generar venta / generar histórico): ya no aplica porque los datos son reales y fijos, no se generan. Queda "🤖 Pregúntale a la IA" (pregunta libre + evaluación completa del banco) y una pestaña nueva "🔍 Explorar tablas" con un selector de tabla y una tabla de datos simple (se ocultan las columnas BLOB `Photo`/`Picture`). |
| `src/generator/` | Intacto, sin usarse en el flujo principal. Sirve de respaldo. |

### Comparativa de los tres modelos

Se corrió el banco de 10 preguntas completo con `qwen2.5-coder:1.5b`,
`qwen2.5-coder:14b` y `llama3.1:8b` (`python -m src.evaluator.evaluador
--modelo <modelo>`). Los reportes completos, caso por caso, quedan en
`docs/evaluaciones/`; el resumen comparativo (con las dos rondas y el
experimento del prompt, ambos detallados abajo) está en
`docs/evaluaciones/comparativa.md`.

**Primera corrida** (comparador original, con el falso negativo de
traducción):

| Modelo | Precisión | Duración |
|---|---|---|
| `qwen2.5-coder:1.5b` | 7/10 (70.0%) | 92 s |
| `qwen2.5-coder:14b`  | 7/10 (70.0%) | 526 s |
| `llama3.1:8b`        | 6/10 (60.0%) | 465 s |

**Segunda corrida**, después de corregir `coincide_texto()` en
`comparar.py` para reconocer traducciones (ver tabla de cambios arriba):

| Modelo | Precisión | Cambio vs. ronda 1 |
|---|---|---|
| `qwen2.5-coder:1.5b` | 7/10 (70.0%) | sin cambio |
| `qwen2.5-coder:14b`  | **9/10 (90.0%)** | **+2** |
| `llama3.1:8b`        | 6/10 (60.0%) | sin cambio |

Solo `qwen2.5-coder:14b` mejoró: sus dos fallos en "categoría con más
ingresos" y "país con más clientes" eran él respondiendo bien pero en
español ("Bebidas", "Estados Unidos") contra un comparador que solo conocía
el valor en inglés. Los otros dos modelos no se beneficiaron porque, en
esos mismos casos, su SQL calculaba mal el dato — el comparador no puede
arreglar una consulta equivocada, solo dejar de penalizar una traducción
correcta.

Hallazgos principales (detalle completo en `comparativa.md`):

- Los tres modelos coinciden en los 5 casos de una sola tabla (productos
  descontinuados, stock bajo el punto de reorden, ingreso de un producto,
  proveedor con más descontinuados, clientes inactivos). El esquema con
  ejemplos y la fórmula de ingreso explícita en `prompts.py` les alcanza
  para esos casos en los tres tamaños de modelo.
- **Ningún modelo resuelve automáticamente la pregunta comparativa entre
  los 9 empleados** (caso 5): requiere dos niveles de agregación anidada
  (`SUM` por empleado, luego `MAX`/`MIN` sobre esos totales). Ver el
  experimento del prompt abajo: sí se logró que un modelo calculara el dato
  exacto, pero el puntaje automático de ese caso no se movió.
- `qwen2.5-coder:14b` es ahora el más preciso (9/10), seguido de
  `qwen2.5-coder:1.5b` (7/10, y ~5x más rápido) y `llama3.1:8b` (6/10). Para
  10 preguntas con el esquema bien documentado, el modelo de 1.5B iguala al
  de 8B y le pierde por poco al de 14B — el tamaño ayuda, pero no de forma
  proporcional a los recursos que consume.
- **Nota operativa:** la primera corrida de `llama3.1:8b`, lanzada
  inmediatamente después de `qwen2.5-coder:14b`, falló por completo (0/10)
  porque Ollama se quedó sin memoria al cambiar de modelo — no porque
  `llama3.1:8b` fuera incapaz. Se confirmó con una llamada de prueba directa
  y se corrió de nuevo desde cero. El mismo síntoma volvió a aparecer al
  reevaluar `qwen2.5-coder:14b` en la segunda ronda (el proceso fue matado
  por bajo nivel de memoria del sistema); se resolvió liberando el modelo
  cargado en Ollama (`ollama stop <modelo>`) antes de cada corrida siguiente.
  Evaluar modelos grandes uno a la vez, y descargar explícitamente el
  anterior de la memoria de Ollama, evita este falso negativo por
  infraestructura.

### Experimento: ¿ayuda un ejemplo de "agregación en dos niveles" en el prompt?

El caso 5 es el único que ningún modelo resolvía. Se le agregó a
`prompts.py` una sección con dos ejemplos concretos de cómo comparar el
total de cada grupo contra el conjunto (subconsulta + `MAX`/`MIN`/`AVG`, y
`AVG(...) OVER ()`), y se volvió a correr *solo ese caso* con los 3 modelos
para aislar el efecto del cambio (detalle completo, con el SQL de antes y
después de cada modelo, en `comparativa.md`).

**Resultado: el ejemplo sí cambió el comportamiento, pero solo del modelo
más grande.** `qwen2.5-coder:14b` pasó de escribir un `GROUP BY` de un solo
nivel (como los otros dos) a reproducir casi literalmente el patrón
`MAX`/`MIN`/`AVG` sobre subconsulta del ejemplo, y calculó los dos números
correctos con precisión exacta (`51488395.196` y `48314100.765`).
`qwen2.5-coder:1.5b` y `llama3.1:8b` ignoraron el ejemplo nuevo y siguieron
con su estrategia de siempre (listar los 9 totales, que de casualidad
también contienen los extremos correctos).

El puntaje automático del caso, sin embargo, **siguió en 0/3 para los
tres**: el comparador de resultados numéricos (`_mismos_numeros` en
`evaluador.py`) exige que la cantidad de números del SQL del modelo
coincida exactamente con la de la referencia (2), y los tres modelos
devuelven más (una lista de 9, o un resumen de 3 con el promedio incluido).
Es una limitación conocida del banco, deliberadamente sin corregir en esta
ronda para no mezclar "el prompt ayudó" con "también aflojamos el
comparador" en el mismo experimento — la mejora de comportamiento del
modelo de 14B es real y verificable en el SQL y el número exacto, aunque el
marcador automático no lo refleje. Corregirlo (aceptar que la verdad esté
*contenida* en la respuesta, igual que ya hace `coincide_numerico()` con el
texto) queda como mejora futura.
