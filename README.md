# ventia: Instacart con IA local

Web app sobre **The Instacart Online Grocery Shopping
Dataset 2017** (pedidos reales de supermercado en línea):

- **Chat con una IA local** (Ollama). Traduce la pregunta en
  español a SQL de solo lectura, la ejecuta contra los datos reales y
  redacta la respuesta en streaming. El SQL queda visible en cada turno y
  el historial se conserva durante la sesión.
- **Dashboard de 11 necesidades analíticas** (N1-N11: recompra,
  canasta, picos de demanda, frecuencia, productos ancla, departamentos,
  evolución, rutinarios/exploradores, tamaño de pedido, impulso y
  predicción del siguiente pedido), con gráficas, valores clave y la
  decisión que ayuda a tomar. Incluye un botón para (re)generar un **PDF
  determinista, sin IA**: cada cifra sale de una consulta SQL.

**Ficha del dataset** (fuente, licencia, limitaciones, filas y columnas por
tabla, medidas con consultas reales): [`docs/DATASET_INSTACART.md`](docs/DATASET_INSTACART.md).
Este README no repite sus cifras: esa ficha es la fuente autorizada.

El encargo completo está en [`docs/BRIEF_INSTACART.md`](docs/BRIEF_INSTACART.md)
y las definiciones formales de N1-N11 en [`docs/NECESIDADES.md`](docs/NECESIDADES.md).

---

## Instalación

Requisitos: Python 3.11 o superior (probado con 3.12), y
[Ollama](https://ollama.com) corriendo en local solo para el chat.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # incluye pytest; requirements.txt solo lo de ejecución
ollama pull qwen2.5-coder:1.5b                    # modelo por defecto del chat
```

(En Windows: `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`.)

## Cargar los datos

1. Descarga los 6 CSV de Instacart (copia usada: Kaggle, usuario *yasserh*;
   licencia original de uso no comercial) y colócalos en
   `data/instacart/raw/`: `aisles.csv`, `departments.csv`, `products.csv`,
   `orders.csv`, `order_products__prior.csv` y `order_products__train.csv`.
2. Construye la base DuckDB, con esquema explícito y construcción atómica:
   ```bash
   .venv/bin/python -m src.db.instacart --raw data/instacart/raw --out data/instacart.duckdb
   ```
   Opciones: `--rebuild` (reconstruir si ya existe), `--memory-limit 4GB`
   (valor por defecto) y `--threads N`.
3. (Opcional) Valida la carga contra los CSV:
   ```bash
   .venv/bin/python -m src.db.instacart_validacion --db data/instacart.duckdb --raw data/instacart/raw
   ```
4. Genera las tablas agregadas que usan las necesidades pesadas:
   ```bash
   .venv/bin/python -m src.analytics.agregados --db data/instacart.duckdb --out data/instacart_agg.duckdb
   ```
5. (Opcional) Precalcula las 11 respuestas de referencia en `data/referencia/`.
   Si no lo haces, el dashboard las calcula la primera vez y las guarda en
   caché; también puedes usar su botón "Actualizar datos".
   ```bash
   .venv/bin/python -m src.analytics --todas --db data/instacart.duckdb --agg data/instacart_agg.duckdb --out-dir data/referencia
   ```

Todo lo que se genera (`data/*.duckdb`, `data/referencia/`, `data/cache/`,
`data/pdf/`) está en `.gitignore`.

## Correr la app

```bash
.venv/bin/streamlit run src/ui/app_instacart.py
```

Arriba del área principal hay dos íconos, **💬** (chat) y **📊**
(dashboard), que alternan qué vista ocupa la pantalla. El activo se ve
resaltado. La barra lateral lista las 11 necesidades como fijados: un clic
abre el dashboard en esa necesidad.

- **Dashboard standalone** (sin chat): `.venv/bin/streamlit run src/ui/dashboard.py`
- **Chat por línea de comandos**: `.venv/bin/python -m src.llm.responder_instacart "¿Cuántos pedidos hay en total?"`
- **PDF por línea de comandos**: `.venv/bin/python -m src.pdf.generador` (añade `--forzar` para recalcular las 11 necesidades)

## Variables de entorno

| Variable | Default | Efecto |
|---|---|---|
| `VENTIA_LLM_MODELO_INSTACART` | `qwen2.5-coder:1.5b` | Modelo de Ollama del chat. Se lee en cada pregunta. El default se eligió con el benchmark de 3 modelos de la Fase 5. |
| `OLLAMA_HOST` | `http://localhost:11434` | La lee la librería `ollama`: dónde está el servidor. |

Las variables de la etapa Northwind (`VENTIA_DB_PATH`, `VENTIA_LLM_MODELO`)
**se eliminaron del código** en la Fase 7. Ya no tienen ningún efecto.

## Pruebas

```bash
.venv/bin/python -m pytest -q                  # suite completa
.venv/bin/python -m pytest -m integration -q   # solo las que usan data/instacart.duckdb real
```

- La mayoría de los tests usan fixtures chicas (`tests/fixtures/`) y
  sustituyen el modelo por un doble, así que no necesitan Ollama.
- Las pruebas `integration` se saltan si falta `data/instacart.duckdb`.
- Las de coherencia del chat (`tests/test_coherencia_instacart.py`) se
  saltan si Ollama no responde.

---

## Qué garantiza la IA y qué no

El chat usa un modelo local chico (qwen2.5-coder:1.5b). Las guardas
garantizan **coherencia estructural**: que la respuesta sea fiel a los datos
que devolvió la consulta. **No garantizan que la consulta haya sido la
correcta para la pregunta.**

**Sí garantiza (con código y tests, no solo con el prompt):**
- **Solo lectura, una sentencia, con LIMIT y timeout.** Hay dos barreras:
  la validación de texto (`validar_sql`) y una conexión DuckDB
  `read_only=True` a nivel de motor. El LIMIT (1000) se fuerza y la
  consulta tiene un timeout real de 10 s. En la Fase 5 se desactivó cada
  guarda a propósito y su test falló. Ejemplo real: *"Borra todos los
  pedidos"* → el modelo generó `DELETE FROM orders` → se rechazó sin
  reintento (Fase 5 y Fase 6, sección 6).
- **No inventa tablas ni columnas que se ejecuten.** El esquema del prompt
  se obtiene leyendo la base real. Un SQL con una columna inexistente falla
  en el motor, se reintenta una sola vez con el motivo exacto y, si vuelve
  a fallar, el chat muestra un error controlado, nunca un resultado
  inventado.
- **No inventa dinero.** El dataset no tiene precios. A *"¿Cuánto dinero
  se gastó?"* el modelo intentó `SUM(price)`, el motor lo rechazó dos
  veces y el chat terminó en un error controlado, sin ningún monto (Fase 5).
- **No altera las cifras del resultado.** Cada número de la respuesta se
  compara contra el resultado de la consulta. Caso real: la base devolvió
  3421083 y el modelo escribió *"Hay 34,210,83 pedidos"*. Ahora se detecta
  y la respuesta final es *"Hay 3,421,083 pedidos en total."* Si la
  segunda redacción también falla, se usa una plantilla con el valor real
  (Fase 6). Como la respuesta llega en streaming, la cifra alterada puede
  verse unos segundos antes de que aparezca el aviso y la versión corregida.

**NO garantiza:**
- **Corrección semántica.** Si el SQL responde otra pregunta, la cifra es
  fiel a *ese* resultado y pasa todas las guardas. Caso real (Fase 6,
  turno 1): a *"¿Cuántos pedidos hay de tipo train?"* el modelo contó las
  **líneas** de `order_products__train` en vez de los **pedidos** con
  `eval_set = 'train'`, y respondió con esa cifra de líneas como si fueran
  pedidos.
- **Acertar en consultas con JOIN.** En el banco de 15 preguntas de la
  Fase 5, qwen2.5-coder:1.5b produjo SQL válido en el 54% de las preguntas
  no trampa; el reintento no mejoró esa tasa. Dos preguntas del test de
  coherencia están marcadas `xfail` por esta limitación medida.
- **Resolver bien las preguntas de seguimiento.** Ver "Limitaciones conocidas".

**Pendiente para una etapa futura:** la **comparación automática** de las
respuestas del chat contra las respuestas de referencia deterministas de
`src/analytics` (las mismas del dashboard y el PDF). Ninguna fase la
implementó: el brief la dejó fuera de alcance. El evaluador de la etapa
Northwind es otro mecanismo y está archivado
(ver [`docs/EVALUADOR_NORTHWIND_LEGACY.md`](docs/EVALUADOR_NORTHWIND_LEGACY.md)).

---

## Limitaciones conocidas del sistema completo

Consolidadas de los reportes de fase; el detalle y las cifras están en cada
documento enlazado.

**Del dataset** (ver [`docs/DATASET_INSTACART.md`](docs/DATASET_INSTACART.md)):
- Sin precios, fechas reales ni datos demográficos: nada del sistema puede
  hablar de dinero ni de calendario.
- `order_dow` y `order_hour_of_day` son anónimos: se etiquetan "Día 0..6" y
  "Hora 0..23", nunca "lunes".
- Los pedidos `test` no tienen productos y se excluyen de todo análisis de
  productos.

**De las necesidades** (ver [`docs/NECESIDADES.md`](docs/NECESIDADES.md)):
- **N4**: `days_since_prior_order` está topado en 30. Una parte de los
  usuarios "en riesgo" tiene su último intervalo censurado (el real podría
  ser mayor). Además no existe "hoy": se compara el último intervalo contra
  el patrón propio del usuario, nunca "días sin comprar hasta hoy".
- **N11**: el baseline (frecuencia + recencia, sin ML) tiene un **techo**:
  solo puede acertar productos que ya estaban en el historial del usuario.
  El hit-rate se reporta junto con ese techo.
- **N5 y N10**: el orden de agregado al carrito puede reflejar la
  **interfaz** de Instacart (p. ej. listas de "comprar de nuevo" primero),
  no una preferencia. "Producto ancla" e "impulso" son hipótesis, no hechos.
- **N7 y N9** son descriptivas, no causales.
- N2 (lift) favorece pares raros; se exige un soporte mínimo documentado.

**Del chat** (ver [Fase 5](docs/FASE5_REPORTE.md) y [Fase 6](docs/FASE6_REPORTE.md)):
- Corrección semántica no garantizada (sección anterior).
- **Una pregunta de seguimiento hereda los errores del turno anterior.** Al
  modelo se le mandan los últimos 3 turnos exitosos como (pregunta, SQL).
  *"¿y de tipo test?"* se resuelve bien si el SQL anterior era correcto, y
  falla si no lo era (caso real de la Fase 6).
- La primera pregunta después de arrancar Ollama tarda más (carga del
  modelo). El timeout del modelo es de 30 s por llamada; en streaming es
  por fragmento.
- La validación de cifras puede descartar una cifra derivada legítima (un
  cálculo que no está en el resultado) y sustituirla por el reintento o la
  plantilla. Es un falso positivo seguro.

**De la interfaz** (ver [Fase 8](docs/FASE8_REPORTE.md), secciones 2 y 3):
- **No hay concurrencia real.** Streamlit ejecuta un solo script a la vez
  por sesión, y cambiar de vista es un rerun completo:
  - si cambias al dashboard con una respuesta en proceso, esa respuesta
    **se cancela y se vuelve a generar desde el principio** al regresar al
    chat (en la medición se desperdiciaron 1-2 llamadas a Ollama). La
    pregunta no se pierde y ambas vistas lo avisan;
  - si cambias al chat mientras se genera el PDF, el PDF se escribe igual,
    pero se pierde su mensaje de confirmación y el cambio de vista tarda
    unos segundos.

**Del rendimiento** (ver [Fase 1](docs/FASE1_REPORTE.md) y [Fase 3](docs/FASE3_REPORTE.md)):
- Los paneles se sirven desde caché (`data/referencia/`, invalidada por la
  huella de la base). Recalcular N10 y N11 tarda varios segundos y solo
  ocurre con "Actualizar datos", nunca al cambiar de panel.

---

## Historial de fases

| Fase | Qué se hizo | Commit | Reporte |
|---|---|---|---|
| 0 | Auditoría del proyecto Northwind antes de migrar | — | [`AUDITORIA.md`](docs/AUDITORIA.md) |
| 1 | Capa de datos: carga de los 6 CSV a DuckDB con esquema explícito, validación y ficha del dataset | `2657cc4` | [`FASE1_REPORTE.md`](docs/FASE1_REPORTE.md) |
| 2A | Contrato analítico (`ResultadoNecesidad`) y necesidades N1-N6 | `e383f1e` | [`FASE2A_REPORTE.md`](docs/FASE2A_REPORTE.md) |
| 2B | Correcciones de la 2A y necesidades N7-N11 | `109eee9` | [`FASE2B_REPORTE.md`](docs/FASE2B_REPORTE.md) |
| 3 | Dashboard de 11 paneles con caché y prueba de concurrencia con Ollama | `6909ee9` | [`FASE3_REPORTE.md`](docs/FASE3_REPORTE.md) |
| 4 | PDF determinista sin IA, integrado al dashboard | `fb3a697` | [`FASE4_REPORTE.md`](docs/FASE4_REPORTE.md) |
| 5 | IA local sobre Instacart: esquema leído de la base, guardas reales, reintento, benchmark de 3 modelos y modelo por defecto `qwen2.5-coder:1.5b` | `f7ccb28` | [`FASE5_REPORTE.md`](docs/FASE5_REPORTE.md) |
| 6 | App de dos paneles, chat con streaming e historial, validación de cifras | `a92c9db` | [`FASE6_REPORTE.md`](docs/FASE6_REPORTE.md) |
| 7 | Retiro de Northwind y del simulador a `legacy/`, limpieza de dependencias y este README | `57d3a86` | [`FASE7_REPORTE.md`](docs/FASE7_REPORTE.md) |
| 8 | Interfaz de vista alternante estilo Claude.ai: íconos 💬/📊 y necesidades fijadas en la barra lateral | ver `git log` | [`FASE8_REPORTE.md`](docs/FASE8_REPORTE.md) |

El código de la etapa anterior (chat sobre Northwind, simulador de ventas
sintéticas y evaluador del banco de Northwind) está archivado en
[`legacy/`](legacy/README.md), con su historial de git intacto.
