# Fase 3 — Dashboard (11 paneles, gráficas, caché, prueba de concurrencia)

Rama: `migracion-instacart`. Fecha: 2026-09-22. Fase **aditiva**: Northwind,
`src/llm`, `src/evaluator` y `src/ui/app.py` no se tocaron. `src/analytics/`
sigue sin importar `streamlit` (verificado con un test, ver abajo). Cero IA
en esta fase, cero cifras hardcodeadas en la UI (todo sale de
`ResultadoNecesidad`), cero lenguaje de dinero.

---

## Paso 0

### Commit de Fase 2B

Ya existía al empezar esta fase (`git status` inicial estaba limpio, sin
cambios pendientes de 2B):

```
commit 109eee9f20da2861aaf7ca61b9975905d6133cb9
Author: Falzo186 <a2223330180@alumnos.uat.edu.mx>
Date:   Tue Sep 22 09:11:12 2026 -0600

    Fase 2B: correcciones y necesidades N7-N11
    ...
 28 files changed, 3338 insertions(+), 82 deletions(-)
```

No hizo falta comitear nada nuevo para cerrar 2B.

### Corrección de wording causal en N9

**Revisión:** al describir la desviación intra-usuario por factor, la
limitación de N9 decía "controla ese **efecto**" — lenguaje que sugiere una
relación causal que el propio módulo se cuida de no afirmar en ningún otro
lado (la necesidad es explícitamente "Descriptivo, no causal").

**Dónde se buscó:** `src/analytics/n9_tamano.py` (único lugar con la
palabra "efecto" ligado a la desviación intra-usuario) y `docs/
NECESIDADES.md` — la sección de N9 en `NECESIDADES.md` **no contenía** la
palabra "efecto" ni otro wording causal (`grep -n "efecto\|causa\b\|
impacta\|provoca\|genera\b" docs/NECESIDADES.md` no encontró nada): no hizo
falta cambiar ese archivo. Se documenta este hallazgo en vez de forzar una
edición innecesaria.

**Cambio en `src/analytics/n9_tamano.py`** (única línea tocada de todo el
módulo):

```diff
-        "tamaño medio de ESE usuario) controla ese efecto: es la comparación más honesta de si el "
-        "factor en sí se asocia con pedidos más grandes o chicos.",
+        "tamaño medio de ESE usuario) controla esa diferencia de composición: es la comparación más "
+        "honesta de si el factor en sí se asocia con pedidos más grandes o chicos.",
```

**Hallazgo de N9 — ANTES** (texto tal como lo reportaba `docs/
FASE2B_REPORTE.md`, limitación #2):

> El tamaño 'bruto' por factor mezcla composición de clientes (algunos
> usuarios siempre piden más, sin importar el factor): la desviación
> intra-usuario (tamaño del pedido menos el tamaño medio de ESE usuario)
> **controla ese efecto**: es la comparación más honesta de si el factor en
> sí se asocia con pedidos más grandes o chicos.

**Hallazgo de N9 — DESPUÉS** (recalculado contra `data/instacart.duckdb`
real, mismas cifras, solo cambia el wording):

> El tamaño 'bruto' por factor mezcla composición de clientes (algunos
> usuarios siempre piden más, sin importar el factor): la desviación
> intra-usuario (tamaño del pedido menos el tamaño medio de ESE usuario)
> **controla esa diferencia de composición**: es la comparación más
> honesta de si el factor en sí se asocia con pedidos más grandes o chicos.

Ninguna cifra cambió (tamaño medio 10.11, mediana 8, TAM_MAX=35, rangos de
desviación intra-usuario idénticos): es un cambio de texto puro. Los otros
3 textos de `limitaciones`/`hallazgos` de N9 ya usaban "asociación"/
"descriptivo, no causal" correctamente y no se tocaron.

---

## 1. Capa de servicio con caché (`src/dashboard/servicio.py`)

**No importa `streamlit`** (verificado con dos pruebas independientes:
análisis de AST de sus `import`, y un subproceso nuevo que lo importa y
comprueba `sys.modules`).

### Regla de caché

Cada `data/referencia/{id}.json` ya incluye `huella_bd` (conteos de
`orders`/`order_products__prior`/`order_products__train` en el momento en
que se calculó esa necesidad — campo que ya existía desde Fase 2A/2B,
parte del contrato `ResultadoNecesidad`). `cargar_necesidad(id)`:

1. Lee el JSON si existe.
2. Calcula `huella_bd(con)` sobre la BD fuente ACTUAL (3 consultas
   `COUNT(*)` baratas — el mismo mecanismo que `agregados.abrir_agregado()`
   ya usa desde Fase 2A para detectar un agregado "rancio").
3. Si `huella_bd` guardada == `huella_bd` actual: usa el JSON
   (`ResultadoNecesidad.desde_dict()`, agregado nuevo en `contrato.py` —
   reconstruye el dataclass completo y revalida su `__post_init__`, un
   chequeo de integridad extra sobre el archivo leído de disco). No corre
   ninguna consulta analítica.
4. Si no coinciden (o no existe el archivo): llama a
   `src.analytics.registro.invocar(id, con, agg_path)` (la MISMA función
   que usa el CLI de `python -m src.analytics`) y reescribe el JSON.

**Nota sobre el nombre del campo comparado:** el enunciado de esta fase
describe la condición como "su `huella_contenido` coincide con la huella
actual de la BD (`huella_bd` del contrato)". El campo que en efecto se
compara contra la BD fuente es `huella_bd` (el propio enunciado lo aclara
entre paréntesis). `huella_contenido` es el sha256 de Fase 2B/C4: sirve
para comparar el CONTENIDO de dos corridas entre sí (determinismo), no
como chequeo barato de vigencia — calcularlo de nuevo exigiría recalcular
la necesidad completa, justo lo que la caché quiere evitar. Documentado
también en el docstring del módulo.

### `refrescar_todas(forzar=False)`

Recorre las 11 necesidades con UNA sola conexión DuckDB. `forzar=True`
ignora la caché y recalcula las 11 (lo usa el botón "Actualizar datos" con
la casilla marcada, y la prueba de concurrencia). Devuelve un
`ResumenRefresco` con, por necesidad: si se recalculó, cuánto tardó, y el
error si lo hubo (una necesidad que falla no tumba el refresco de las
demás). Acepta un callback `al_terminar_cada_una` para progreso incremental
(lo usa la UI para la barra de progreso).

### Tests (`tests/test_dashboard_servicio.py`, 8 tests)

- Caché vigente → NO toca la BD más allá de las 3 `execute()` de
  `huella_bd()` (verificado con una conexión "espía" que cuenta llamadas a
  `execute()`; sobre una caché vigente, exactamente 3).
- Caché rancia → SÍ recalcula (se "cambia un byte" editando directamente
  `huella_bd.order_products_prior` dentro del JSON en disco, sin tocar la
  BD real) y reescribe el JSON con la huella correcta.
- Sin JSON previo → calcula y escribe; una segunda carga ya viene de caché.
- `refrescar_todas(forzar=False)` solo recalcula lo ausente/rancio;
  `forzar=True` recalcula las 11 aunque la caché ya esté vigente.
- El módulo no importa `streamlit` (AST + subproceso nuevo).

---

## 2. UI (`src/ui/dashboard.py`)

Selector de necesidad (N1-N11) en la barra lateral; el panel principal
muestra, en este orden: título + insignia de caché/fresco + `fecha_
generacion` + duración, pregunta, decisión, universo (con filtros), 1-2
gráficas Altair, valores clave como `st.metric`, hallazgos como lista,
**limitaciones siempre visibles como `st.warning`** (nunca en un expander
colapsado — así se cumple para todas, incluidas el techo de N11 o la
censura de N4, sin tener que decidir caso por caso cuáles son "cruciales"),
y dos expanders opcionales (parámetros, tablas completas).

**Nunca llama a `calcular()`/`invocar()`**: solo importa `src.dashboard.
servicio` (y el tipo `ResultadoNecesidad` de `contrato.py` para type
hints). Una caché de sesión de Streamlit (`st.cache_data`, con un contador
`version_cache` en `st.session_state` como parte de la clave) evita
reabrir conexión + releer JSON en cada rerun de Streamlit (que ocurre en
cada interacción de cualquier widget, no solo al cambiar de panel); el
botón "Actualizar datos" incrementa ese contador para invalidarla.

**Salvedad de robustez encontrada en pruebas:** con universos muy chicos
(la fixture de test), algunas tablas pueden quedar vacías (p. ej. ningún
producto elegible), y Altair no puede inferir el tipo de las columnas de
un `DataFrame` vacío — lanza `ValueError`. Se agregó un helper `_seguro()`
que devuelve `None` en ese caso y la UI muestra `st.info("No hay datos
suficientes...")` en vez de romperse. Esto es una salvaguarda real (podría
pasar en producción con parámetros muy exigentes), no solo un artefacto de
los tests.

### Gráfica elegida por necesidad (todas leen tablas ya existentes en el JSON, ninguna inventa datos)

| Necesidad | Gráfica(s) |
|---|---|
| N1 | Dispersión: `lineas` (log) vs. `tasa_recompra`, color por clasificación (`asegurar_inventario` ∪ `revisar_retirar`) |
| N2 | Barras horizontales de `lift` (top 20 de `top_50_lift`) + tabla completa en el expander |
| N3 | Mapa de calor `order_dow` x `order_hour_of_day`, color = `n_pedidos` |
| N4 | Histograma de `razon` (de `top_50_razon`) + regla vertical en `UMBRAL_RAZON` (2.0) |
| N5 | Barras horizontales de `indice_ancla` (top 20 de `top_50_indice_ancla`) |
| N6 | Dispersión `lineas` vs. `tasa_recompra`, color por `cuadrante` (`departamentos`) |
| N7 | Línea `tamano_medio` por `k` (`por_numero_pedido`) + barras de las 4 diferencias del pareado 1-vs-10 |
| N8 | Histograma de `bin_decimo` (índice_novedad) + reglas en los cortes rutinario/explorador |
| N9 | Histograma de `tamano_bin` + barras de `desviacion_intra_usuario` por factor (selector: hora/día/intervalo/order_number) |
| N10 | Barras de `tasa_recompra` por `tramo` (0=inicio...4=final del carrito) |
| N11 | Barras agrupadas de `hit_rate` por `metodo` x `k` (`metricas`) |

### Captura textual de cada panel (contra el dataset real, `data/instacart.duckdb`)

**N1 — Recompra por producto.** Gráfica: dispersión volumen (log) vs. tasa
de recompra, coloreada Asegurar inventario / Revisar-retirar. Métricas:
Tasa global de recompra=59.0%; Productos elegibles=22,460; Tamaño 'Asegurar
inventario'=2,581; Tamaño 'Revisar/retirar'=4,673; % pares observables
(censura)=67.2%. Hallazgo destacado: *"La tasa global de recompra sobre
todas las líneas es 59.0% (19,955,360 de 33,819,106 líneas con
reordered=1)."* Punto de ejemplo en la dispersión: `Half And Half Ultra
Pasteurized`, 2,995 líneas, tasa_recompra=86.1%.

**N2 — Canasta.** Gráfica: barras de lift (top 20 de 50). Métricas:
Productos candidatos=842; % cobertura=51.1%; Pares generados=340,461;
Pares con lift válido=135,967. Hallazgo: *"El par más frecuente es 'Bag of
Organic Bananas' + 'Organic Hass Avocado' con 64,761 pedidos en común."*
Barra más alta: `Strawberry Rhubarb Yogurt` + `Mixed Berries Whole Milk
Icelandic Style Skyr Yogurt`, lift=154.31.

**N3 — Picos de demanda.** Gráfica: mapa de calor 7×24. Métricas: Día
pico=1; Hora pico=10; Razón pico/valle=60.5x; % en ventana de 4h más
cargada=32.9%. Hallazgo: *"La celda con más pedidos es (Día 1, Hora 10) con
55,671 pedidos (índice=2.73x el promedio)."*

**N4 — Frecuencia de recompra.** Gráfica: histograma de razón (top 50) con
línea roja en UMBRAL_RAZON=2.0. Métricas: Usuarios evaluables=146,468;
Excluidos por MIN_INTERVALOS=59,741; En riesgo=32,507; En riesgo
censurados=18,091. **Limitación visible sin expander (censura de N4):**
*"De esos 32,507 usuarios en riesgo, 18,091 (55.7%) tienen su último
intervalo topado en 30 (censurado): su intervalo real podría ser mayor a
30 días, el dato no lo distingue."*

**N5 — Productos ancla.** Gráfica: barras de índice_ancla (top 20).
Métricas: Productos elegibles=17,170; Veces_primero total=3,346,083;
Producto top volumen=Banana; Producto top índice_ancla=Homestyle Orange
Juice. Hallazgo: *"El producto con mayor índice_ancla entre elegibles es
'Homestyle Orange Juice' (índice_ancla=3.64x, 317 apariciones de 47
usuarios distintos)."*

**N6 — Departamentos y pasillos.** Gráfica: dispersión por cuadrante.
Métricas: Departamento top=produce; Pasillo top=fresh fruits; Líneas
missing/other=115,482 (departamento) / 115,482 (pasillo). Hallazgo: *"El
departamento con más líneas es 'produce' (9,888,378 líneas, 29.2%,
tasa_recompra=65.1%, cuadrante=fiel-alto-volumen)."*

**N7 — Evolución por número de pedido.** Gráficas: línea de tamaño medio
por k + barras del pareado 1-vs-10. Métricas: Pedidos totales=3,346,083;
Líneas totales=33,819,106; Usuarios pareados=107,438; Diferencia media de
tamaño=−0.29. Hallazgo: *"Comparación PAREADA (n=107,438): el tamaño
cambia en promedio −0.29 líneas, la tasa de recompra +64.7%... Esta es la
comparación válida de 'pedido 1 vs pedido 10' que pide el brief."*

**N8 — Rutinarios vs exploradores.** Gráfica: histograma de índice_novedad
con cortes en 0.584 (rutinario) y 0.792 (explorador). Métricas: Usuarios
clasificables=175,072; Excluidos por W=31,137; Correlación Pearson
(confusor)=−0.095. Hallazgo: *"Perfil 'rutinario' (n=43,786): tamaño medio
10.30, tasa de recompra histórica 64.3%."*

**N9 — Tamaño de pedido.** Gráficas: histograma de tamaño + barras de
desviación intra-usuario (factor elegible con selector). Métricas: Tamaño
medio=10.11; Mediana=8.0; TAM_MAX(P99)=35; % pedidos ≤10=62.2%.
Hallazgo (con el wording corregido en esta fase): *"...la desviación
intra-usuario ... controla esa diferencia de composición: es la
comparación más honesta de si el factor en sí se asocia con pedidos más
grandes o chicos."*

**N10 — Final del carrito / impulso.** Gráfica: barras de tasa_recompra
por tramo. Métricas: Líneas incluidas=33,655,513; Excluidas
(MIN_TAM)=163,593; Productos elegibles=27,130. Hallazgo: *"Tasa de
recompra por tramo: tramo 0: 69.6% ... tramo 4: 45.6%"* (cae
monótonamente hacia el final del carrito).

**N11 — Predicción del siguiente pedido.** Gráfica: barras agrupadas
hit_rate por método y K. Métricas: Usuarios evaluados=131,209; DECAY
elegido=0.9; Techo alcanzable=59.9%; hit_rate@10(evaluación)=86.0%; Supera
ambos baselines=Sí. **Limitación visible sin expander (techo de N11):**
*"El TECHO es una propiedad del dataset, no del método: ningún baseline
basado solo en historial de compras puede acertar un producto que el
usuario nunca compró antes."*

### Tests de contrato UI/analytics (`tests/test_dashboard_paneles.py`, 24 tests)

Dos capas por necesidad: (1) lista explícita de qué tabla/columna/
parámetro/valor_clave necesita cada `graficos_nX()`, verificada contra un
`ResultadoNecesidad` real (fixture chica); (2) el dashboard real completo,
corrido de punta a punta con `streamlit.testing.v1.AppTest` (apuntado a la
fixture vía monkeypatch de `servicio.DB_PATH_DEFAULT`/etc., sin tocar
`data/*.duckdb`), seleccionando cada uno de los 11 paneles y verificando
que no lance excepción. Si `src.analytics` renombra un campo que la UI usa,
ambas capas fallan aquí — no en producción.

Para permitir el monkeypatch de la capa 2, `cargar_necesidad`/
`refrescar_todas` se refactorizaron: sus parámetros `db_path`/`agg_path`/
`out_dir` pasaron de default `= CONSTANTE` (congelado al definir la
función, el clásico gotcha de Python) a default `None` resuelto contra el
global del módulo EN EL MOMENTO DE LA LLAMADA — así un test puede
parchear `servicio.DB_PATH_DEFAULT` y que tanto llamadas directas como las
que hace `dashboard.py` (que nunca pasa esos argumentos) usen la ruta
parcheada.

### Comando para correrla (standalone, aislada del chat)

```
streamlit run src/ui/dashboard.py
```

Probado además sin navegador con `streamlit.testing.v1.AppTest` (los 11
paneles, más la interacción del selector de factor de N9 y el botón
"Actualizar datos") y con un servidor real (`streamlit run ... --server.
headless true`, `curl` devolvió HTTP 200).

---

## 3. Prueba de concurrencia con Ollama

Script: `scripts/prueba_concurrencia.py` (`python -m scripts.
prueba_concurrencia`). Mide el caso más pesado posible:
`refrescar_todas(forzar=True)` (recalcula las 11, incluidas N10/N11) EN UN
HILO, al mismo tiempo que 5 preguntas fijas se envían a Ollama (modelo
default actual, `llama3.1:8b`, vía `responder_pregunta()` — SQL sobre
Northwind + redacción, el flujo completo de 2 llamadas al modelo) EN OTRO
HILO. Memoria medida vía `/proc/meminfo` y `/proc/<pid>/status` (sin
agregar `psutil` como dependencia nueva solo para esta prueba puntual),
muestreada cada 0.5s durante toda la corrida.

**Preguntas fijas:** ¿Cuántos pedidos hay en total? / ¿Cuál es el producto
más vendido? / ¿Cuántos clientes distintos hay? / ¿Qué categoría de
producto tiene más unidades vendidas? / ¿Cuál es el pedido con más
artículos distintos? (Northwind, como aclara el punto 6 del prompt: el
prompt del LLM sigue siendo el de Northwind, no es objeto de esta fase
corregirlo.)

### Resultado — AISLADO (uno a la vez)

| | Duración total | Detalle |
|---|---:|---|
| Analytics (`forzar=True`, 11 necesidades) | 19.08s | N1=0.35s, N2=0.17s, N3=0.08s, N4=1.12s, N5=4.75s, N6=1.97s, N7=0.09s, N8=0.45s, N9=1.13s, **N10=3.43s**, **N11=5.48s** |
| Ollama (5 preguntas) | 81.02s | 5.13s, 20.08s, 10.11s, 25.71s, 19.99s — 5/5 OK, 0 timeouts |

### Resultado — CONCURRENTE (analytics + Ollama al mismo tiempo)

Duración de pared (ambos hilos corriendo a la vez): **86.09s**.

| | Duración total | Δ vs. aislado |
|---|---:|---:|
| Analytics (11 necesidades) | 39.95s | **+109%** (2.1x más lento) |
| Ollama (5 preguntas) | 86.09s | +6% |

Detalle por necesidad bajo concurrencia (comparado con aislado, arriba):
N1=0.61s, N2=0.34s, N3=0.13s, N4=1.89s, N5=9.80s (+106%), N6=4.74s
(+140%), N7=0.09s, N8=0.81s, N9=3.45s (+205%), **N10=7.33s (+114%)**,
**N11=10.60s (+94%)**. 0 errores en las 11.

Detalle por pregunta a Ollama bajo concurrencia: 8.13s, 21.76s, 10.85s,
25.34s, 20.01s — **5/5 OK, 0 timeouts**. Las duraciones individuales de
Ollama son parecidas a las aisladas (algunas hasta un poco más rápidas):
la contención de CPU golpeó mucho más fuerte a `analytics` (DuckDB
`threads=1`, un solo hilo compitiendo contra el servidor de Ollama que
usaba varios núcleos a la vez) que a Ollama mismo.

### Memoria durante la corrida concurrente

| Métrica | Valor |
|---|---:|
| RSS combinado pico (este proceso + procesos de Ollama) | **6,580,048 KB ≈ 6.43 GB** |
| RSS propio pico (proceso Python de `analytics`, sin Ollama) | 775,088 KB ≈ 757 MB |
| RSS de Ollama pico (servidor + `llama-server` del modelo cargado) | 5,912,068 KB ≈ 5.77 GB |
| Memoria usada del sistema, pico | 13,421,796 KB ≈ 13.1 GB |
| Memoria disponible del sistema, mínima | 4,820,716 KB ≈ 4.71 GB |
| ¿RSS combinado cruza 2.5GB? | **SÍ** |
| ¿Se agotó la RAM libre (~10GB de Fase 1)? | No — bajó a ~4.71GB disponibles, nunca a 0; no hubo swap |

**El cruce de 2.5GB es casi enteramente por Ollama, no por `analytics`:**
el proceso de `analytics` por sí solo pesa ~757MB (bien por debajo de
2.5GB); es `llama3.1:8b` cargado en memoria (~5.77GB de RSS para el
servidor + el modelo) lo que empuja el combinado a 6.4GB. Esto es
consistente con el tamaño del archivo del modelo (~4.9GB en disco,
`ollama list`) más el overhead del runtime de inferencia.

**Salvedad de medición:** en la corrida "aislada", el RSS de Ollama ya
aparecía alto (~5.75GB) desde el arranque de esa fase — Ollama mantiene el
modelo cargado en RAM unos minutos después de la última consulta
(`keep_alive` por default), y una prueba de latencia hecha minutos antes
en esta misma sesión ya lo había cargado. La comparación aislado-vs-
concurrente de TIEMPO sigue siendo válida (el modelo estaba igual de
"caliente" en ambas corridas), pero el RSS de "aislado" no refleja un
Ollama recién arrancado sin modelo — ver `docs/FASE1_REPORTE.md`, que sí
documentó `GET /api/ps` → `[]` (sin modelos cargados) antes de esa medición.

### Conclusión para Fase 5 (LLM)

- **Ningún timeout** en 10 preguntas totales (5 aisladas + 5 concurrentes),
  con un timeout configurado de 90s por pregunta muy por encima de lo que
  se necesitó (máximo observado: 25.7s).
- La CPU es el recurso que más se disputa: bajo concurrencia, `analytics`
  se vuelve 1.9x-3.1x más lento necesidad por necesidad (promedio ~2.1x en
  total), no falla, pero el margen bajo el objetivo de 5s por necesidad se
  reduce: **N10 (7.33s) y N11 (10.60s) quedan muy por encima del objetivo
  bajo concurrencia**, aun sirviendo desde una corrida `forzar=True` (el
  peor caso: caché fría del día). Esto NO reabre su diseño (ya cerrado en
  2B): confirma que el dashboard NUNCA debe llamar a N10/N11 en vivo
  mientras el chat esté activo, y que la app de Streamlit debe evitar
  dejar el botón "Actualizar datos" disponible sin advertencia mientras
  Ollama esté respondiendo.
- En RAM: `llama3.1:8b` cargado consume ~5.8GB por sí solo. Sumado al pico
  medido de `analytics` (~0.76GB) y al resto del sistema operativo
  (~1-2GB), la máquina de prueba (18GB totales) tiene margen cómodo, pero
  **una máquina con 8-12GB totales quedaría muy ajustada** con este modelo
  cargado más el dashboard activo. Recomendación medida (no una decisión
  tomada aquí): si Fase 5 necesita correr en una máquina más chica,
  considerar un modelo más liviano (`qwen2.5-coder:1.5b`, ya descargado,
  ~1GB en disco) o limitar cuánta RAM puede usar el proceso de `analytics`
  durante un refresco mientras el chat esté activo.

---

## Tiempo de carga de los 11 paneles

**Caché caliente** (JSON ya vigente en disco; `cargar_necesidad()` sin
conexión reusada, midiendo el método completo incluida la apertura/cierre
de conexión para `huella_bd()`):

| Necesidad | Carga (ms) |
|---|---:|
| N1 | 42.8 |
| N2 | 34.5 |
| N3 | 33.4 |
| N4 | 35.5 |
| N5 | 38.4 |
| N6 | 33.7 |
| N7 | 34.2 |
| N8 | 33.4 |
| N9 | 33.4 |
| N10 | 34.1 |
| N11 | 33.8 |

Las 11 cargan en bloque de 33-43ms: dominado por abrir/cerrar la conexión
DuckDB de solo lectura y las 3 consultas de `huella_bd()`, no por leer el
JSON (los JSON van de 9KB a 531KB, triviales de parsear).

**Caché fría** (recalculando de cero — `refrescar_todas(forzar=True)`
aislado, de la tabla de la sección 3 arriba): de 0.08s (N3, la más
liviana) a 5.48s (N11, la más pesada); **N10 y N11 son las únicas que se
acercan o superan el objetivo de 5s incluso aisladas**, consistente con lo
medido en Fase 2B.

---

## Resultado completo de `refrescar_todas()`

(De la corrida aislada, sección 3: `python -m scripts.prueba_concurrencia`
llama internamente a `servicio.refrescar_todas(forzar=True)`.)

```
analytics: 19.08s total, 11 recalculadas, 0 errores
  N1:  0.35s   N2:  0.17s   N3:  0.08s   N4:  1.12s   N5:  4.75s
  N6:  1.97s   N7:  0.09s   N8:  0.45s   N9:  1.13s   N10: 3.43s
  N11: 5.48s
```

Con `forzar=False` (caché ya vigente, corrida inmediatamente después):
0/11 recalculadas, ~0.4s total (11 × ~35ms de `huella_bd()`, ver tabla de
arriba) — verificado también por
`tests/test_dashboard_servicio.py::test_refrescar_todas_forzar_recalcula_todo_aunque_haya_cache`.

---

## Desviaciones (con motivo)

1. **`cargar_necesidad` no devuelve un `ResultadoNecesidad` "pelón"**, sino
   un `CargaNecesidad` (`resultado: ResultadoNecesidad`, `desde_cache:
   bool`, `duracion_carga_s: float`). El punto 2 del brief exige que la UI
   muestre "un aviso si el dato viene de caché vs. recién calculado" — sin
   ese campo, la UI no puede saberlo sin reconsultar `huella_bd` por su
   cuenta (justo la doble consulta que la caché quiere evitar).
2. **`ResultadoNecesidad.desde_dict()`/`.desde_json()` se agregaron a
   `contrato.py`** (Fase 2A/2B, aditivo): sin un loader, `servicio.py`
   habría tenido que trabajar con dicts pelones en vez de con el
   dataclass tipado, perdiendo la revalidación de `__post_init__` al leer
   un JSON de disco. Cambio pequeño y symétrico a `a_dict()` (ya
   existente); no afecta ningún test de 2A/2B.
3. **`db_path`/`agg_path`/`out_dir` de `cargar_necesidad`/
   `refrescar_todas` pasaron de default congelado a default `None`
   resuelto en la llamada** — necesario para que los tests de la capa 2
   (`AppTest` + monkeypatch) pudieran apuntar el dashboard real a una
   fixture chica sin tocar `data/*.duckdb`. Cambio de compatibilidad
   binaria (mismo comportamiento con los valores por default).
4. **Se agregó el helper `_seguro()` en `dashboard.py`** para DataFrames
   vacíos (Altair no puede inferir tipos de columnas sin filas) — no
   estaba pedido explícitamente, pero es necesario para que la UI no se
   rompa con universos chicos (parámetros exigentes, o la fixture de
   test); se documenta como salvaguarda de producción, no solo de tests.
5. **`docs/evidencia/concurrencia_fase3.json`** (el volcado completo de la
   prueba de concurrencia) se generó pero NO se comitea (se agregó a
   `.gitignore`, mismo patrón que `data/referencia/`): es evidencia
   reproducible (`python -m scripts.prueba_concurrencia --out ...`), no
   código ni documentación viva; los números relevantes ya están en este
   reporte.
6. **La prueba de concurrencia corre las dos cargas en dos HILOS del mismo
   proceso Python**, no en dos procesos separados. Es una aproximación
   razonable (DuckDB libera el GIL en cómputo pesado; las llamadas HTTP a
   Ollama también lo liberan mientras esperan), documentada explícitamente
   como tal — no es idéntico a dos procesos de Streamlit/Ollama
   completamente independientes, pero mide la contención de CPU/RAM real
   de la máquina, que es lo que el punto 3 del brief pide.
7. **No se corrigió el prompt de Northwind usado por `responder_pregunta`**
   ni se verificó la CORRECCIÓN de sus respuestas (solo tiempos/errores):
   el punto 3 del brief lo aclara explícitamente ("no es objeto de esta
   fase corregirlo, ver punto 6").

---

## Riesgos

- **N10/N11 bajo concurrencia superan 5s por un margen amplio** (7.33s y
  10.60s medidos): si el botón "Actualizar datos" se presiona mientras el
  chat de Ollama está respondiendo, el usuario del dashboard esperará más
  de 10s en el peor caso. No hay mitigación implementada en esta fase (el
  brief la deja para decisión de Fase 5); la UI sí muestra una barra de
  progreso para que la espera sea visible, no silenciosa.
- **RSS de Ollama con `llama3.1:8b` cargado (~5.8GB) es el factor
  dominante de memoria**, no `analytics`. En una máquina con menos RAM
  total que la de prueba (18GB), el margen se reduce proporcionalmente;
  no se probó en una máquina más chica.
- **La medición de RSS vía `/proc` es específica de Linux** (no portátil a
  Windows/Mac); documentado en el docstring del script. Dado que el
  entorno de desarrollo y el de despliegue previsto son Linux, se aceptó
  la limitación en vez de agregar `psutil` solo para esto.
- **`st.cache_data` de Streamlit vive en memoria del proceso del servidor**:
  con múltiples usuarios concurrentes del dashboard (fuera de alcance de
  esta fase, que es standalone/single-user de evaluación), el
  comportamiel de invalidación por `version_cache` es por sesión de
  Streamlit, no global — no se probó multi-sesión.

---

## git status

```
 M .gitignore
 M src/analytics/contrato.py
 M src/analytics/n9_tamano.py
?? scripts/prueba_concurrencia.py
?? src/dashboard/
?? src/ui/dashboard.py
?? tests/test_dashboard_paneles.py
?? tests/test_dashboard_servicio.py
```

(`docs/FASE3_REPORTE.md` se agrega con el commit final; `docs/evidencia/`
queda ignorado por git, ver Desviación #5.)

## `pytest -q`

```
231 passed, 1 warning in 65.25s
```

199 previos (Fase 2B) + 8 de `test_dashboard_servicio.py` + 24 de
`test_dashboard_paneles.py` = 231. El único warning es una
`AltairDeprecationWarning` interna de Streamlit (tema de gráficas), no de
código propio.

## `pytest -m integration -q`

```
14 passed, 217 deselected in 45.73s
```

Sin cambios respecto a Fase 2B (esta fase no agregó tests marcados
`integration`; los de `test_dashboard_paneles.py` corren contra una
fixture chica, no contra `data/instacart.duckdb`, a propósito, para no
depender del dataset completo en CI).
