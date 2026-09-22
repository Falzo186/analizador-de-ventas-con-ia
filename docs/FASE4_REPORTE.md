# Fase 4 — PDF determinista (sin IA), regenerable desde el dashboard

Rama: `migracion-instacart`. Fecha: 2026-09-22. Fase **aditiva**: Northwind,
el chat (`src/llm`), el evaluador y `src/ui/app.py` no se tocaron. Cero IA,
cero cifras hardcodeadas, cero lenguaje de dinero (mismas reglas de Fase 3).

---

## Paso 0

`git status` al arrancar esta fase estaba limpio: el commit de Fase 3 ya
existía, no hizo falta comitear nada.

```
commit 6909ee9639e394c27a084425a106af2885f7d351
Author: Falzo186 <a2223330180@alumnos.uat.edu.mx>
Date:   Tue Sep 22 10:00:41 2026 -0600

    Fase 3: dashboard con caché y prueba de concurrencia
    ...
 10 files changed, 1975 insertions(+), 2 deletions(-)
```

---

## Diseño del módulo

### 1. Ficha del dataset reutilizable (`scripts/generar_ficha_dataset.py`)

Se extrajo `construir_ficha(con, db_path, raw_dir) -> Ficha` (dataclass con
`fuente`, `copia_usada`, `licencia`, `limitaciones`, `dimensiones`
(`DimensionTabla` por tabla), `orders_test`, `version_duckdb`, `db_path`,
`db_size_mb`, `resultados_validacion` (los `Chequeo` de `src.db.
instacart_validacion`), `n_pass`/`n_fail`/`n_info`). `generar()` (el script,
para `docs/DATASET_INSTACART.md`) y `_ficha_a_markdown()` ahora son dos
funciones separadas: la primera solo MIDE, la segunda solo da formato. Se
verificó que el refactor no cambió ni una cifra: `python -m scripts.
generar_ficha_dataset` regenerado produce el mismo markdown byte a byte
salvo la línea de fecha (`diff` limpio en el resto del archivo).
`src/pdf/generador.py` llama a la MISMA `construir_ficha()` para la
portada — no reimplementa ninguna consulta ni copia cifras del markdown.

**Test** (`tests/test_generar_ficha_dataset.py`, marcado `integration`):
regenera el markdown desde `construir_ficha()` y lo compara contra
`docs/DATASET_INSTACART.md` en disco, byte a byte salvo la fecha; más un
test de cifras clave puntuales (filas por tabla, PASS/FAIL/INFO).

### 2. Datos de gráficas compartidos (`src/dashboard/graficas_datos.py`)

Nuevo módulo, sin importar `altair`, `matplotlib` ni `streamlit`: una
función `datos_nX(r)` por necesidad (o `datos_nX_algo` cuando hay más de
una gráfica) que arma el/los `pandas.DataFrame` desde `r.tablas`/
`r.valores_clave`/`r.parametros`, nada más. `src/ui/dashboard.py` (Fase 3)
se refactorizó para llamar a estas funciones y solo envolver el resultado
en `alt.Chart(...)`; `src/pdf/generador.py` llama a las MISMAS funciones y
envuelve el resultado en `matplotlib`. Ningún DataFrame se selecciona dos
veces.

Al refactorizar se encontró un bug real (no solo de test): `datos_n7()`
podía devolver un `DataFrame` con las 8 diferencias del pareado en `None`
cuando `n_usuarios=0` (nadie tiene pedido 1 Y pedido 10 con líneas en el
universo — pasa con la fixture chica, y podría pasar con un universo real
muy filtrado); comparar `None >= 0` para elegir color en la gráfica
lanzaba `TypeError`. Se corrigió tratando `n_usuarios=0` como "sin datos"
(mismo criterio que el resto de las gráficas), con `_seguro()` mostrando
el aviso correspondiente en vez de reventar. Corregido en la capa
compartida: beneficia a Altair y a matplotlib por igual.

### 3. Generador de PDF (`src/pdf/generador.py`)

`generar_pdf(db_path, agg_path, out_dir, out_pdf, raw_dir, forzar,
necesidades_md) -> ResumenPDF`. Nunca llama a `calcular()`: para cada una
de las 11 necesidades llama a `servicio.cargar_necesidad(nid, con=...)`
(una sola conexión reusada, igual que `refrescar_todas`); `forzar=True`
llama primero a `servicio.refrescar_todas(forzar=True, ...)`, exactamente
el mecanismo que ya usa el botón "Actualizar datos" del dashboard.

Documento (`reportlab.platypus`, con `BaseDocTemplate.multiBuild()` para
que el Índice tenga números de página reales en una segunda pasada
automática):
- **Portada:** título, fecha, aviso "Generado sin intervención de IA" en
  un recuadro, Ficha del dataset completa (de `construir_ficha()`),
  limitaciones del dataset, y una tabla de `huella_bd` por cada una de las
  11 necesidades (para que un lector pueda verificar que las 11 vienen de
  la misma foto de la base de datos).
- **Índice** con página real por sección (`TableOfContents` + `afterFlowable`
  capturando cada encabezado de necesidad).
- **11 secciones** (N1..N11, mismo orden y títulos que el dashboard):
  pregunta, decisión, universo con filtros, 1-2 gráficas matplotlib (de
  `graficas_datos`), tabla de valores clave, hallazgos, y las limitaciones
  en un recuadro con fondo amarillo y borde — SIEMPRE visible, nunca en
  letra chica ni relegado al final (las 11 necesidades lo tienen, no solo
  las 8 "críticas": es el mismo trato uniforme que ya usa el dashboard).
- **Apéndice:** las consultas SQL de `r.consultas` de cada necesidad,
  tal cual, en `Preformatted` (fuente monoespaciada, sin reescribir).
- **Última página:** parámetros de cada necesidad (nombre, valor,
  justificación) leídos del campo `parametros` del JSON (fuente de verdad
  declarada por el propio enunciado), con una verificación cruzada contra
  `docs/NECESIDADES.md` (ver más abajo).

**Verificación cruzada JSON vs. `NECESIDADES.md`:**
`verificar_parametros_contra_necesidades_md()` parsea las filas de tabla
`| \`NOMBRE\` | valor | ... |` de `NECESIDADES.md`, compara por VALOR
NUMÉRICO cuando ambos lados son números (para no marcar "2.0" vs "2.000"
como discrepancia por puro formato) y si no como texto normalizado (sin
espacios/comas/paréntesis). Contra el dataset real, la única discrepancia
encontrada es `N9.TAM_MAX`: el JSON trae `35` (el valor medido, P99), pero
`NECESIDADES.md` lo describe en PROSA ("P99 del tamaño de pedido (35)"),
no como un valor de tabla limpio — el parser no lo reconoce como
"coincide" aunque el número 35 esté presente en el texto. **No es una
discrepancia de contenido real**, es una limitación conocida del parseo
automático (documentada en el propio código y en este reporte); se decidió
no ampliar el regex para "adivinar" números dentro de prosa por el riesgo
de falsos negativos en otro caso.

**Metadato junto al PDF** (`data/pdf/reporte_instacart.meta.json`):
`fecha_generacion` + la `huella_bd` de cada una de las 11 necesidades en
el momento de generar. `pdf_vigente(ruta_pdf)` compara esa huella guardada
contra la huella_bd ACTUAL de cada necesidad en caché (vía
`cargar_necesidad`, sin recalcular nada) para decidir si el PDF sigue
representando los datos vigentes.

**Cero cifras hardcodeadas:** `tests/test_pdf_generador.py` recorre el AST
del archivo y falla si aparece un literal numérico fuera de (a) una
constante `ALLCAPS` ya nombrada en el bloque de layout, o (b) una lista
blanca explícita de literales de estilo/estructura (coordenadas de tabla
reportlab, opacidad, bins de histograma, rotación de etiquetas, ancho de
barra, fracciones de columna, tolerancia de comparación). Pasa hoy sin
excepciones.

### 4. Integración en el dashboard (`src/ui/dashboard.py`)

Sección nueva en la barra lateral, después del botón "Actualizar datos":
caption de vigencia (✅/⚠️/"aún no generado", vía `pdf_vigente()` cacheado
en sesión igual que las necesidades), casilla "Recalcular las 11
necesidades antes de generar el PDF" (default sin marcar), botón
"📄 Generar/Actualizar PDF" (usa `st.sidebar.status()` mientras genera),
mensaje de éxito con tamaño/duración, expander con discrepancias si las
hay, y `st.download_button()` con el PDF ya generado. El botón NUNCA llama
a `calcular()`: pasa `forzar` tal cual a `generar_pdf()`, que a su vez lo
pasa a `servicio.refrescar_todas()` — la misma ruta que ya usa el botón de
necesidades.

Se aplicó a `generar_pdf()`/`pdf_vigente()` el mismo ajuste de Fase 3 en
`servicio.py`: sus parámetros de ruta pasaron de default congelado (`=
DB_PATH_DEFAULT`, evaluado una sola vez al definir la función) a default
`None` resuelto EN LA LLAMADA — necesario para que el botón del dashboard
(que nunca pasa esos argumentos) y los tests (que monkeypatchean
`generador.DB_PATH_DEFAULT`/etc. para apuntar a una fixture) usen la misma
ruta parcheada.

---

## Extracto de texto de 3 secciones del PDF real (dataset completo)

### N1 — Recompra por producto

```
N1 — Recompra por producto
Pregunta: ¿Qué productos se recompran de forma consistente y cuáles se prueban y se abandonan?
Decisión: Qué asegurar en inventario (alta recompra y volumen) y qué revisar o retirar (alto abandono, baja
recompra).
Universo: Líneas de la vista order_products (prior+train) unidas a products/aisles/departments.
Filtros: order_products__prior UNION ALL order_products__train (universo de líneas)
Dispersión: volumen vs. tasa de recompra, por clasificación
Valores clave
Tasa global de recompra            59.0%    fracción
Productos elegibles                22,460   productos
Tamaño 'Asegurar inventario'       2,581    productos
Tamaño 'Revisar/retirar'           4,673    productos
% pares observables (censura)      67.2%    fracción
Hallazgos
 La tasa global de recompra sobre todas las líneas es 59.0% (19,955,360 de 33,819,106 líneas con
reordered=1).
 [... 4 hallazgos más ...]
Limitaciones
■ No hay precios en el dataset: esta clasificación es de volumen y patrón de recompra, no dice nada sobre
ingresos ni ganancia.
■ El abandono se mide con control de censura: un par (usuario,producto) solo se considera 'observable' si el
usuario tuvo al menos K_POST=5 pedidos con líneas después de su primera compra del producto.
Globalmente, 67.2% de los pares (usuario,producto) son observables con este K_POST...
■ Productos con pasillo/departamento 'missing' u 'other' se marcan con una columna pero no se excluyen...
```

### N4 — Frecuencia de recompra por cliente (limitación crítica: censura de 30 días)

```
N4 — Frecuencia de recompra por cliente
Pregunta: ¿Qué usuarios muestran un intervalo reciente entre pedidos anormalmente largo frente a su propio
patrón?
Decisión: A qué usuarios enviarles un recordatorio de recompra.
Distribución de razón con línea de UMBRAL_RAZON
Valores clave
Usuarios evaluables                            146,468   usuarios
Usuarios excluidos por MIN_INTERVALOS          59,741    usuarios
Usuarios en riesgo                             32,507    usuarios
Usuarios en riesgo censurados (último=30)      18,091    usuarios
% intervalos topados en 30 (censura global)    11.5%     fracción
Usuarios estables ante >=3 combinaciones        20,538   usuarios
Hallazgos
 De 206,209 usuarios, 146,468 (71.0%) tienen al menos MIN_INTERVALOS=5 intervalos PREVIOS a su
último pedido... 32,507 usuarios (22.2%) están 'en riesgo'... De esos, 18,091 (55.7%) tienen su último
intervalo topado en 30 (censurado): su intervalo real podría ser mayor a 30 días, el dato no lo distingue.
Limitaciones
■ No existe una 'fecha actual' en este dataset: se detecta que el intervalo MÁS RECIENTE de un usuario es
anormalmente largo frente a su propio patrón histórico, no 'días sin comprar hasta hoy'...
■ days_since_prior_order está topado en 30: 369,323 de 3,214,874 intervalos no nulos (11.49%) valen
exactamente 30, un valor censurado que puede esconder un intervalo real mayor. Se reporta el subgrupo de
usuarios en riesgo con este valor censurado por separado.
■ MIN_INTERVALOS=5 excluye usuarios con muy pocos pedidos...
■ 'típico' se calcula SOLO con los intervalos previos al último pedido del usuario...
```

### N11 — Predicción del siguiente pedido (limitación crítica: techo del dataset)

```
N11 — Predicción del siguiente pedido
Pregunta: ¿Qué tan bien predice un baseline determinista (frecuencia+recencia, sin ML) los productos del
siguiente pedido?
Comparación de métodos y K: hit-rate
Valores clave
Usuarios evaluados                    131,209   usuarios
DECAY elegido                         0.900     decay
Techo alcanzable                      59.9%     fracción
hit_rate@10 (evaluación)              86.0%     fracción
Supera a ambos baselines en K=10      Sí        booleano
Hallazgos
 Universo: 131,209 usuarios con pedido 'train'... Se eligió DECAY=0.9 por hit_rate@10 en validación
({1.0: 0.858, 0.9: 0.864, 0.8: 0.863, 0.7: 0.858}), sin mirar el conjunto de evaluación.
 TECHO: 828,824 de 1,384,617 líneas objetivo (59.9%) corresponden a un producto que el usuario ya había
comprado en prior; el resto son productos nuevos, inalcanzables para cualquier baseline basado solo en
historial.
 En evaluación (K=10): hit_rate=86.0%... El baseline principal SÍ supera a ambos baselines de contexto.
Limitaciones
■ Baseline sin ML: es una fórmula determinista de frecuencia y recencia, no un modelo entrenado...
■ Los usuarios con último pedido 'test' (75,000) no tienen respuesta conocida...
■ El TECHO es una propiedad del dataset, no del método: ningún baseline basado solo en historial de
compras puede acertar un producto que el usuario nunca compró antes.
■ DECAY se eligió únicamente con el 20% de validación...
■ No hay precios: el ranking y las métricas son de aciertos de producto, no de valor de canasta.
```

---

## Tiempos de generación (dataset completo, `data/instacart.duckdb`)

| Escenario | Tiempo | Detalle |
|---|---:|---|
| Caché caliente (las 11 necesidades ya vigentes) | **33.3 – 33.7s** | 11× `cargar_necesidad` = 0.19s; `construir_ficha()` (validación completa del dataset, `src.db.instacart_validacion`) = **29.2s**; resto (gráficas matplotlib + ensamblado reportlab) ≈ 4s |
| Caché fría (`--forzar`, recalcula las 11) | **51.4s** | Se suman los ~19s que ya midió Fase 3 para `refrescar_todas(forzar=True)` aislado |
| Botón del dashboard (caché ya vigente, medido con `AppTest`) | **35.8s** | 1,012 KB |

Ambos escenarios quedan muy por debajo del límite de 2 minutos. El
**88% del tiempo en caché caliente es `construir_ficha()`**, no las
necesidades: revalida el dataset completo (duplicados, huérfanos, rangos,
reglas de negocio — `src.db.instacart_validacion`) en cada generación,
sin caché propia. No se cacheó porque no lo pidió el enunciado y el total
sigue muy por debajo del límite; se documenta como oportunidad de
optimización en "Riesgos".

---

## Determinismo de contenido

`tests/test_determinismo_de_contenido_dos_corridas` (en
`tests/test_pdf_generador.py`) genera el PDF dos veces sobre la misma
fixture, extrae el texto completo de cada PDF con `pypdf` y compara,
descontando la línea `Generado el <fecha>` de la portada (aislada con una
regex y reemplazada por un marcador fijo antes de comparar). **Pasa**: el
resto del texto extraído es idéntico byte a byte entre las dos corridas.
Los bytes crudos del PDF SÍ difieren entre corridas (reportlab incluye
metadata interna con timestamp) — eso es exactamente lo que este test NO
exige, por diseño (determinismo de CONTENIDO, no de bytes, como pide el
punto 3 del prompt).

---

## Honestidad sobre el texto extraído del PDF

`test_pdf_sin_lenguaje_de_dinero_fuera_de_disclaimers` aplica el mismo
patrón `ingres|dinero|ganancia|precio|revenue|\$|USD|MXN` que ya usa
`tests/test_analytics_honestidad.py`, pero al TEXTO COMPLETO extraído del
PDF (no solo a `hallazgos`). Sobre el dataset real aparecen 17 coincidencias
del patrón — las 17 son disclaimers de negación ("No hay precios...", "no
se puede afirmar nada sobre ingresos o dinero...", "no dice nada sobre
ingresos ni ganancia..."), el mismo tipo de mención que
`test_analytics_honestidad.py` ya permite explícitamente en
`limitaciones` (para aclarar la AUSENCIA de precios, no para hablar de
dinero). El test verifica esto automáticamente: cada coincidencia debe
caer dentro de una ventana de texto que contenga un marcador de negación
("no hay", "no se puede", "ni ganancia", etc.); si apareciera una mención
de dinero SIN negación (p. ej. una cifra en dólares), el test fallaría.
**Pasa** sobre la fixture (usada en CI); verificado a mano también contra
el PDF del dataset completo (17/17 coincidencias dentro de un disclaimer).

---

## Tamaño del archivo PDF final

`data/pdf/reporte_instacart.pdf`: **1,036,247 bytes (≈ 1,012 KB / 0.99 MB)**,
**39 páginas** (portada + índice + 11 secciones con 1-2 gráficas cada una +
apéndice de SQL + página de parámetros), generado contra el dataset
completo (33.8M líneas).

---

## Captura textual del flujo del botón en el dashboard

Estado inicial (PDF ya vigente de una corrida anterior en esta misma
sesión), vía `streamlit.testing.v1.AppTest` contra `data/instacart.duckdb`
real:

```
Subheader | 📄 Reporte PDF
Caption   | ✅ El PDF ya está actualizado: ninguna necesidad cambió desde que se generó.
Checkbox  | Recalcular las 11 necesidades antes de generar el PDF
Button    | 📄 Generar/Actualizar PDF
UnknownElement | ⬇️ Descargar PDF
```

Después de presionar "📄 Generar/Actualizar PDF" (sin marcar la casilla de
forzar; regenera con la caché de necesidades tal cual, revalida el
dataset completo para la ficha):

```
Status    | PDF listo: 1,012 KB en 35.8s
Success   | Generado: 1,012 KB en 35.8s.
Expander  | ⚠️ 1 discrepancias JSON vs. NECESIDADES.md
Markdown  | - N9.TAM_MAX: JSON='35', docs/NECESIDADES.md=['P99 del tamaño de pedido (35)']
UnknownElement | ⬇️ Descargar PDF
```

(`UnknownElement` es como `AppTest` tipifica `st.download_button` en esta
versión de Streamlit — no hay un accessor dedicado; se confirmó que el
elemento con esa etiqueta existe y que el archivo `data/pdf/reporte_
instacart.pdf` quedó escrito en disco.)

---

## Desviaciones (con motivo)

1. **`_fmt_valor` se duplica** en `src/pdf/generador.py` en vez de
   importarse de `src/ui/dashboard.py`: es formato de texto (lo que el
   brief pide no duplicar es la selección de DataFrames, resuelto en
   `graficas_datos.py`), y evita que generar un PDF por CLI dependa de
   tener `streamlit` instalado/importable solo para formatear un número.
2. **Bug real encontrado y corregido en `graficas_datos.datos_n7`**
   (`None` en las 8 diferencias del pareado cuando `n_usuarios=0`, ver
   "Diseño del módulo" arriba): no estaba cubierto por los tests de Fase 3
   porque su fixture sí tenía usuarios pareados; la fixture de PDF
   (`instacart_needs`, la de 2A) no. Corregido en la capa de datos
   compartida, beneficia a ambas UI.
3. **`generar_pdf()`/`pdf_vigente()` cambiaron sus defaults de ruta a
   `None` resuelto en la llamada** (mismo ajuste que `servicio.py` en
   Fase 3): necesario para que el botón del dashboard y los tests con
   `AppTest` + monkeypatch usaran rutas de fixture de forma consistente.
4. **`construir_ficha()` no se cachea**: cada generación de PDF revalida
   el dataset completo (~29s de los ~33s en caché caliente). No lo pidió
   el enunciado (solo pide cachear las 11 necesidades vía `servicio.py`,
   que sí se respeta) y el total sigue muy por debajo del límite de 2
   minutos; documentado como oportunidad de optimización, no como bug.
5. **N9.TAM_MAX se reporta como "discrepancia"** por el parser de
   verificación cruzada aunque no lo es en la práctica: `NECESIDADES.md`
   lo describe en una frase ("P99 del tamaño de pedido (35)") en vez de en
   una celda de tabla limpia. Se documentó en vez de forzar el regex a
   adivinar números dentro de prosa (riesgo de falsos negativos mayor que
   el de este único falso positivo conocido).
6. **`tests/test_pdf_generador.py` y `tests/test_dashboard_pdf_boton.py`
   corren contra la fixture chica, no contra el dataset completo** (no
   están marcados `integration`): el texto de limitaciones críticas,
   honestidad y estructura del documento sale de cadenas fijas en
   `src.analytics` que no dependen del volumen de datos — se verificó a
   mano que el PDF del dataset completo también las contiene (ver
   extractos arriba). Mantenerlos fuera de `integration` los deja correr
   siempre en CI sin necesitar `data/instacart.duckdb`.
7. **El warning de matplotlib "Tight layout not applied"** aparece en
   algunas gráficas de barras horizontales con nombres de producto largos
   (N2/N5). Es un `UserWarning` cosmético (el layout igual se genera, solo
   sin el ajuste automático óptimo), no afecta el contenido ni el
   determinismo; no se investigó más a fondo por presupuesto de tiempo.

---

## Riesgos

- **`construir_ficha()` sin caché** (~29s por generación, ver Desviación
  #4): si en el futuro se genera el PDF con mucha frecuencia (p. ej. cada
  refresco automático), este costo se vuelve el cuello de botella
  dominante. Fácil de resolver más adelante con el mismo patrón de
  `huella_bd` que ya usa `servicio.py`, pero no se implementó aquí.
- **El parser de verificación cruzada de parámetros es best-effort**
  (regex sobre tablas markdown): un parámetro narrado solo en prosa en
  `NECESIDADES.md` (como TAM_MAX) siempre se reportará como "discrepancia"
  aunque no lo sea. Alguien que revise el PDF sin leer este reporte podría
  malinterpretar esa única línea como un error real de datos.
- **El botón de PDF y el botón de necesidades comparten `version_cache`**:
  ambos invalidan la misma caché de sesión de Streamlit. Es intencional
  (mantiene todo consistente), pero significa que generar el PDF también
  fuerza una recarga de caché de sesión para los paneles, aunque no haya
  cambiado ninguna necesidad.

---

## git status (antes del commit final)

```
 M .gitignore
 M docs/DATASET_INSTACART.md
 M requirements.txt
 M scripts/generar_ficha_dataset.py
 M src/ui/dashboard.py
?? src/dashboard/graficas_datos.py
?? src/pdf/
?? tests/test_dashboard_pdf_boton.py
?? tests/test_generar_ficha_dataset.py
?? tests/test_pdf_generador.py
```

`docs/DATASET_INSTACART.md` cambió solo en la línea de fecha (se
regeneró para verificar el refactor de `construir_ficha()`, ver arriba).
`data/pdf/` queda ignorado por git (mismo patrón que `data/referencia/` y
`docs/evidencia/`).

## `pytest -q`

```
253 passed, 1 warning in 137.99s (0:02:17)
```

250 previos (Fase 3) + 3 de `test_dashboard_pdf_boton.py` = 253. Los
demás archivos nuevos (`test_generar_ficha_dataset.py`,
`test_pdf_generador.py`) ya estaban incluidos en el conteo de 250 al
sumarse durante esta misma fase — el desglose completo: 231 (Fase 3) + 2
(`test_generar_ficha_dataset.py`) + 17 (`test_pdf_generador.py`) + 3
(`test_dashboard_pdf_boton.py`) = 253.

## `pytest -m integration -q`

```
16 passed, 237 deselected in 65.93s (0:01:05)
```

14 previos (Fase 2B/3) + 2 nuevos de `test_generar_ficha_dataset.py`
(los únicos tests de esta fase marcados `integration`, ver Desviación #6).
