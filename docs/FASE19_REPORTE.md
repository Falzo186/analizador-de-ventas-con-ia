# Fase 19/20 — Cambio del modelo de producción y cierre del ciclo

Fecha: 2026-10-03 · Rama: `migracion-instacart` (sin commits desde `c27e4ff`; todo el
trabajo de las Fases 11-20 está en el árbol de trabajo, sin confirmar)

## 0. Punto de partida

El ciclo nació de la retroalimentación del profesor Eder Jahir González Bravo
(el contenido de esa retroalimentación no está en el repositorio ni en esta
sesión; este reporte solo documenta el trabajo técnico que se hizo a partir de
ella). La pregunta de fondo que quedó en pie: **¿el benchmark de modelos mide si
la respuesta es correcta, o solo si el SQL corre?** En la Fase 5 el modelo por
defecto (`qwen2.5-coder:1.5b`) se eligió por SQL válido, rechazo de trampas y RSS
bajo; ninguno de esos criterios comprueba la corrección semántica.

## 1. Qué se agregó (arco de las Fases 11-18)

| Fase | Qué se agregó | Archivos |
|---|---|---|
| 11 | `clasificar_fallo()`, `categoria_fallo`, `resumen_fallos` (categorías de fallo del SQL) | `scripts/benchmark_modelos_llm.py`, `tests/test_clasificar_fallo_benchmark.py`, `docs/CLASIFICACION_FALLOS.md` |
| 11b-11d | Verificación real con Ollama y base; fixes: umbral de filas en la heurística de agregación y patrón "Referenced table" | idem |
| 12-13 | **Oráculo de referencia** (`REFERENCIAS_SQL` + `comparar_con_referencia()`): un SQL que corre puede seguir siendo incorrecto; la llamada 2 del benchmark usa `finalizar_redaccion()` como el chat real | `scripts/benchmark_modelos_llm.py`, `tests/test_referencias_benchmark.py` |
| 12-14 | Reglas de negocio nuevas en el prompt: JOIN explícito aisles/departments (con cadena literal de 4 tablas) y filtro `reordered = 1` | `src/llm/esquema_instacart.py`, `tests/test_esquema_instacart.py` |
| 16 | Modo `primera_columna_id_o_nombre` del oráculo (falsos negativos: el modelo devolvía el nombre y la referencia comparaba el id) | `scripts/benchmark_modelos_llm.py` |
| 17 | Ejemplo literal de subconsulta para el promedio de dos pasos | `src/llm/esquema_instacart.py` |
| 18 | Medición del modelo que realmente era default, latencia y RAM | `docs/CLASIFICACION_FALLOS.md` |
| 19/20 | Cambio de `MODELO_POR_DEFECTO_INSTACART` + test | `src/llm/client.py`, `src/llm/responder_instacart.py`, `tests/test_client_modelo_default.py` |

## 2. Qué mostraron los datos

Correctas según referencia (oráculo), una corrida por modelo:

| Modelo | Fase 14 | Fase 15/16 | Fase 17 (con ejemplo de subconsulta) | Fase 18 |
|---|---|---|---|---|
| qwen2.5:1.5b | 5/12 (42%) | — | 4/11 (36%) | — |
| qwen2.5-coder:1.5b (default anterior) | — | — | — | 10/12 (83%) |
| qwen2.5-coder:latest (7B) | — | 9/11 → 10/11 | **12/12** | — |
| llama3.1:8b | — | 8/12 → 10/12 | **12/12** | — |

Hallazgos:
1. `tasa_sql_valido` **sobrestima** la calidad: el 1.5B llegó a 100% de SQL válido
   con 42% de respuestas correctas.
2. Parte de los fallos del 7-8B eran del **oráculo** (id vs nombre), no del modelo.
3. El fallo `AVG(<columna>)` en los promedios de dos niveles era falta de un
   **ejemplo de agregación** en el prompt: con el patrón literal, 7B y 8B pasan a
   acertar; el 1.5B copia la forma pero elige mal la columna (límite de capacidad).
4. Costo de pasar a un modelo grande en esta máquina (RTX 5060, 8 GB VRAM; 31.9 GB
   RAM, 15.8 GB libres): latencia por pregunta con modelo caliente 1.64 s
   (coder-1.5b), 2.06 s (llama3.1:8b), 2.30 s (qwen2.5-coder 7B); `ollama ps`
   muestra llama3.1:8b de 5.3 GB al 100% en GPU.

## 3. Resultado: cambio de modelo de producción

`MODELO_POR_DEFECTO_INSTACART`: `qwen2.5-coder:1.5b` → **`llama3.1:8b`** (el más
rápido de los dos modelos que llegaron a 12/12). Se puede volver al modelo chico
con la variable de entorno `VENTIA_LLM_MODELO_INSTACART`. Los comentarios de
`client.py` y `responder_instacart.py` (timeouts) quedaron actualizados.

## 4. Verificación de esta fase

1. `git diff --stat`: `src/llm/client.py` (+20/-), `src/llm/responder_instacart.py`
   (+8/-); `tests/test_client_modelo_default.py` es nuevo (untracked).
2. `pytest tests/test_client_modelo_default.py -v` → **3 passed**
   (default es llama3.1:8b; `modelo_actual` lo usa sin variable de entorno; la
   variable permite volver al modelo chico).
3. `pytest` completo → **2 failed, 418 passed, 2 xpassed**. Los 2 fallos son los
   preexistentes de siempre (`test_app_instacart::test_modelos_cargados_lee_api_ps`
   y `test_generar_ficha_dataset`, por `data\` vs `data/` en Windows). Sin
   regresiones por el cambio de default.
4. Verificación en la práctica, sin variable de entorno
   (`VENTIA_LLM_MODELO_INSTACART` vacía): con `ollama ps` vacío antes, se corrió
   `python -m src.llm.responder_instacart "¿cuántos pedidos hay en total?"`:
   `SELECT COUNT(order_id) FROM orders` → 3 421 083 → "Hay 3.421.083 pedidos en
   total." Después, `ollama ps` listó únicamente `llama3.1:8b` (5.3 GB, 100% GPU),
   es decir, el chat real carga el modelo nuevo.

## 5. Limitaciones

- Una sola corrida por modelo, sin repeticiones; el 12/12 de 7B y 8B podría no
  repetirse siempre.
- Los modelos grandes reproducen casi literalmente las plantillas del prompt (JOIN
  de 4 tablas, subconsulta de promedio): funciona para esas formas de pregunta;
  no se probó con agregaciones de dos niveles distintas a las del ejemplo.
- El oráculo cubre 12 de las 13 preguntas evaluables y es un conjunto pequeño.
- Latencia y RAM medidas solo en esta máquina (con GPU). En una máquina sin GPU o
  con poca RAM conviene volver a `qwen2.5-coder:1.5b` vía la variable de entorno
  y re-medir.
- `scripts/prueba_concurrencia.py` lee `/proc/meminfo` y falla en Windows; por eso
  `RSS_ollama_pico` sale 0 MB en estas corridas (problema de portabilidad previo).
- Nada de esto está confirmado en git todavía.
