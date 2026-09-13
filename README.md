# ventia

Evaluador de un LLM local respondiendo preguntas de negocio en español sobre
**Northwind Traders**, una base de datos real (SQLite): productos,
categorías, proveedores, clientes, empleados, pedidos y transportistas.

1. **Pregunta** en lenguaje natural a un modelo local vía Ollama
   (traduce a SQL, ejecuta contra la base real, y redacta la respuesta).
2. **Evalúa** qué tan preciso es el modelo comparando su respuesta contra el
   resultado real de un banco de 10 preguntas de negocio con SQL de
   referencia verificado a mano.

El proyecto empezó sobre un simulador de punto de venta con datos
inventados (`src/generator/`); se migró a datos reales de Northwind para
medir el mismo pipeline (texto → SQL → respuesta) contra un esquema
normalizado de verdad, no uno diseñado a modo para el LLM. El simulador
sigue en el repo como respaldo pero ya no es el flujo principal — ver
`docs/arquitectura.md`, sección "Migración a datos reales (Northwind)".

## Obtener `data/northwind.db`

El archivo de datos (~24 MB) **no está versionado** (ver `.gitignore`,
`data/*.db`) para no inflar el repositorio con un binario descargable.
Necesitas colocar una copia de Northwind en SQLite en `data/northwind.db`
antes de correr nada. Debe tener las tablas `Categories`, `Customers`,
`Employees`, `"Order Details"` (con espacio en el nombre), `Orders`,
`Products`, `Shippers`, `Suppliers`, `Territories`, y las vistas usuales
(`Sales by Category`, `Invoices`, ...). Cualquier port de Northwind a
SQLite con ese esquema sirve; solo verifica los rangos reales de fecha en
`Orders.OrderDate` con:

```bash
sqlite3 data/northwind.db "SELECT MIN(OrderDate), MAX(OrderDate) FROM Orders"
```

y ajusta `PRIMERA_FECHA_ORDEN` / `ULTIMA_FECHA_ORDEN` en `src/llm/prompts.py`
si tu copia tiene un rango distinto al documentado (2012-07-10 .. 2023-10-28
en la copia usada para este proyecto).

## Estructura

| Carpeta          | Qué contiene                                                    |
|------------------|----------------------------------------------------------------|
| `data/`          | `northwind.db` (datos reales, no versionado — ver arriba). `ventia.db` del simulador viejo, si lo usas. |
| `src/db/`        | Conexión a SQLite (`VENTIA_DB_PATH`, por defecto `data/northwind.db`). |
| `src/llm/`       | Cliente de Ollama, esquema real + traducción pregunta → SQL, validación y respuesta. |
| `src/evaluator/` | Banco de 10 preguntas de negocio sobre Northwind + comparación respuesta LLM vs. verdad SQL. |
| `src/ui/`        | App de Streamlit: "Pregúntale a la IA" + "Explorar tablas". |
| `src/generator/` | Simulador de punto de venta (datos inventados). Respaldo, sin usarse en el flujo principal. |
| `docs/`          | Documentación de diseño y reportes de evaluación (`docs/evaluaciones/`). |
| `tests/`         | Pruebas con pytest.                                             |

## Puesta en marcha

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

(En Windows: `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`.)

## Preguntar en lenguaje natural

Requiere Ollama corriendo (`ollama serve`) y el modelo descargado
(p. ej. `ollama pull qwen2.5-coder:14b`).

```bash
.venv/bin/python -m src.llm.responder "¿qué productos están descontinuados actualmente?"
```

Imprime la pregunta, el SQL generado (usa comillas dobles para
`"Order Details"`), el resultado crudo y la respuesta en español. Para
cambiar de modelo: `VENTIA_LLM_MODELO=qwen2.5-coder:1.5b`.

## Interfaz web

```bash
.venv/bin/streamlit run src/ui/app.py
```

Pestaña **🤖 Pregúntale a la IA**: elegir modelo de Ollama, hacer una
pregunta (muestra SQL → resultado → respuesta), y correr la evaluación
completa del banco con el reporte dentro de la app. Pestaña
**🔍 Explorar tablas**: navegar Categories, Products, Suppliers, Customers,
Employees, Shippers, Orders y "Order Details" con una tabla simple.

## Evaluar la precisión del modelo

Corre el banco de 10 preguntas de negocio, compara contra el SQL de
referencia y genera un reporte markdown.

```bash
.venv/bin/python -m src.evaluator.evaluador --modelo qwen2.5-coder:14b --salida docs/evaluaciones/qwen2.5-coder-14b.md
```

Cambia `--modelo` para comparar (`llama3.1:8b`, `qwen2.5-coder:1.5b`, ...).
`--limite N` corre solo los primeros N casos. Ver
`docs/evaluaciones/comparativa.md` para la comparación de los tres modelos
usados en este proyecto.

## Pruebas

```bash
.venv/bin/python -m pytest -q
```

Las pruebas no necesitan Ollama (el modelo se sustituye por un doble) ni
`data/northwind.db` (usan un archivo temporal con el esquema del simulador,
vía `tests/conftest.py`).

Ver `docs/arquitectura.md` para las decisiones de diseño explicadas.
