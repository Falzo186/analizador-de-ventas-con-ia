# Fase 9: selector de modelo, esfuerzo (timeout) y memoria de Ollama

Rama: `migracion-instacart`. Fecha: 2026-09-23. Base: `57b58e6` (HOTFIX 1),
con el árbol limpio.

---

## 0. Paso 0: investigación

Detalle en [`docs/FASE9_INVESTIGACION.md`](FASE9_INVESTIGACION.md), escrito
antes de implementar nada. Resultado:

- **La app no puede arrancar ni detener Ollama.** Es `ollama.service` de
  **sistema** (`User=ollama`, `Restart=always`). `sudo -n` responde
  *"a password is required"*, y polkit responde `auth_admin_keep` para
  `org.freedesktop.systemd1.manage-units` (exige contraseña de
  administrador), sin reglas locales que lo relajen.
  → **No se implementaron botones de Iniciar/Detener.** La UI y el README
  indican que Ollama debe estar corriendo antes y cómo verificarlo
  (`systemctl status ollama`).
- **Liberar memoria sin tocar el servicio sí funciona**:
  `POST /api/generate {"model": …, "keep_alive": 0}` responde
  `done_reason: "unload"`, `/api/ps` queda vacío y **el proceso
  `llama-server`, con 1.1 GB de RSS, termina**. El `size` de `/api/ps`
  coincide con ese RSS con menos del 1% de diferencia, así que es la cifra
  que muestra la UI.

---

## 1. Diseño

### 1.1 Dónde vive la configuración
Un botón **⚙️** (`st.popover`) en la barra superior, junto a 💬 y 📊. Está
en las dos vistas. Contiene:

- **Modelo** (`st.selectbox`, clave `modelo_sel`):
  - las opciones son exactamente las que devuelve
    `client.modelos_disponibles()` en esa ejecución, nunca una lista fija;
  - valor inicial: `VENTIA_LLM_MODELO_INSTACART` si está en la lista; si
    no, el primero;
  - si Ollama no responde, no hay selector y aparece el aviso *"Ollama no
    responde… verifica con `systemctl status ollama`. La app no puede
    iniciarlo ni detenerlo…"*.
- **Esfuerzo** (`st.select_slider`, clave `esfuerzo_sel`, por defecto
  **Medio**), con el timeout efectivo escrito debajo.
- **Memoria de Ollama**:
  - los modelos cargados, con los MiB que reporta `/api/ps`
    (`client.modelos_cargados()`), consultados en cada ejecución;
  - el botón **🧹 Liberar modelo de memoria** (`client.liberar_modelo` →
    `keep_alive: 0`). Está deshabilitado si no hay nada cargado o si hay
    una pregunta en proceso. El estado que se muestra después del clic se
    vuelve a leer de `/api/ps`: no se supone.

### 1.2 Configuración congelada por pregunta
- Al **enviar** una pregunta, el callback `on_submit` guarda en
  `config_en_curso` el modelo, el esfuerzo y los dos timeouts de ese
  momento.
- `responder_en_vivo` usa esa configuración para la fase SQL
  (`responder_pregunta_instacart(modelo=…, timeout_sql_llm_s=…,
  timeout_respuesta_llm_s=…)`) y para la redacción
  (`redaccion_en_stream(modelo=…, timeout_s=…)`). También la guarda en los
  metadatos del turno (`modelo`, `esfuerzo`, `timeouts_s`).
- Cambiar el selector **no** afecta a la pregunta en curso: aplica a la
  siguiente, que es lo que pide el enunciado.

### 1.3 Cambio de modelo o esfuerzo a mitad de una pregunta: el mecanismo del HOTFIX 1
- Tocar el selector es un rerun completo, igual que cambiar de vista. La
  ejecución en curso se abandona y la nueva pasa por `_resultado_sql`: si la
  fase SQL de **esa pregunta** ya está calculada, se reutiliza; si otro hilo
  la está calculando, **se espera a ese hilo**. No se lanza un cálculo
  paralelo.
- La clave de `sql_en_curso` ahora incluye el modelo. Así, si la **misma**
  pregunta se vuelve a **enviar** explícitamente con otro modelo, no se
  reutiliza el resultado del modelo anterior. Un simple cambio de selector
  no la toca, porque la configuración está congelada.

### 1.4 Cambios fuera de la UI (aditivos, sin cambiar el flujo)
- `src/llm/client.py`:
  - `modelos_cargados()` (`GET /api/ps`) y `liberar_modelo(modelo)`
    (`POST /api/generate`, `keep_alive: 0`), las dos con un timeout de
    administración de 10 s;
  - errores → `LlmNoDisponible`.
- `src/llm/responder_instacart.py`: `responder_pregunta_instacart` acepta
  `modelo=None` y lo pasa a la fase SQL y a la redacción. Antes solo podía
  usar la variable de entorno, que es global al proceso y no sirve para una
  elección por sesión.
- **Sin cambios**:
  - las guardas (LIMIT, timeout de DuckDB de 10 s, solo lectura, validación);
  - el streaming;
  - la validación de cifras;
  - el reintento de SQL.

---

## 2. Tabla esfuerzo → timeout y justificación

### 2.1 Datos de referencia
**Medias de la Fase 5** (benchmark de 15 preguntas, llamadas de SQL /
respuesta): 1.5b **2.9 / 2.5 s** · 8b **12.3 / 9.6 s** · 14b **31.6 / 25.6 s**.

Esas medias mezclan una primera llamada muy lenta con el resto rápidas: la
primera pregunta de cada corrida del benchmark siempre tardó ~20 s. Por eso
en esta fase se midió por separado la llamada de SQL real (prompt de ~1,830
tokens) **en frío**, con el modelo recién cargado, y **en caliente**:

| Modelo | Frío: total (carga + evaluación del prompt + generación) | Caliente (2.ª y 3.ª pregunta) | Memoria (`/api/ps`) |
|---|---|---|---|
| qwen2.5-coder:1.5b | **22.1 s** (3.1 + 18.4 + 0.6) | 1.2 s / 1.6 s | 1,116 MiB |
| llama3.1:8b | **106.0 s** (10.1 + 93.6 + 2.2) | 4.5 s / 7.1 s | 5,318 MiB |
| qwen2.5-coder:14b | **219.6 s** (25.3 + 190.1 + 4.1) | 7.8 s | 9,460 MiB |

Lo caro en frío **no es la carga** del modelo: es la primera evaluación del
prompt de sistema en CPU. Después, Ollama reutiliza ese prefijo en caché y
la evaluación del prompt baja a 0.2-2.5 s.

### 2.2 Tabla

El timeout se aplica a **cada** llamada al modelo. En streaming, httpx lo
aplica por fragmento.

| Esfuerzo | SQL | Respuesta | Qué cubre | Margen |
|---|---:|---:|---|---|
| **Bajo** | 15 s | 15 s | 1.5b **ya cargado** | ×5 sobre su media de la Fase 5 (2.9 s), ×9 sobre lo medido en caliente. **No** cubre el 1.5b en frío (22.1 s): la primera pregunta tras una carga puede dar timeout. |
| **Medio** (por defecto) | 30 s | 30 s | 1.5b **siempre**, incluso en frío; 8b **ya cargado** | ×1.4 sobre el 1.5b en frío (22.1 s); ×2.4 sobre la media del 8b (12.3 s), ×4 sobre lo medido en caliente. Es el valor que ya usaba el chat desde la Fase 5 (×10 sobre la media del 1.5b): **el comportamiento por defecto no cambia**. |
| **Alto** | 60 s | 60 s | 8b y 14b **ya cargados** | ×4.9 sobre la media del 8b; ×1.9 sobre la media del 14b (31.6 s), ×7.7 sobre lo medido en caliente. |
| **Ultra** | 150 s | 150 s | 8b **recién cargado** | ×1.4 sobre el 8b en frío (106 s), el mismo margen que Medio da al 1.5b en frío; ×4.7 sobre la media del 14b. |
| **Extremo** | 300 s | 300 s | 14b **recién cargado** | ×1.37 sobre el 14b en frío (219.6 s). |

Recomendación que se muestra en la ayuda del control: *"Bajo: 1.5b ya
cargado · Medio: 1.5b siempre, 8b cargado · Alto: 8b/14b cargados · Ultra:
8b recién cargado · Extremo: 14b recién cargado"*.

**Peor caso de espera total**: una pregunta hace hasta 4 llamadas al modelo
(SQL, reintento de SQL, redacción y reintento estricto de cifras). En
Extremo, un modelo colgado podría tener al usuario esperando hasta ~20
minutos antes del error controlado. Es el costo de un nivel pensado para
el 14b en frío.

**Qué no cambia el esfuerzo**: el timeout de la consulta en DuckDB (10 s).
Es una guarda de seguridad (Fase 5), no un parámetro de calidad, y la UI lo
indica.

---

## 3. Evidencia del cambio de modelo a mitad de una pregunta

**Método** (el del HOTFIX 1):
- servidor real con Ollama y la base real;
- el envoltorio `app_debug.py`, que registra ejecuciones y, en cada fase
  SQL, el modelo que recibe;
- el proxy delante de Ollama, que ahora también registra el **modelo** que
  pide cada `/api/chat`;
- un cliente websocket que envía la pregunta 1 con el modelo por defecto,
  cambia el selector a `llama3.1:8b` **0.4 s después** (con la respuesta en
  curso) y luego envía la pregunta 2.

```
opciones del selector (de modelos_disponibles): ['qwen2.5-coder:1.5b', 'qwen2.5-coder:14b', 'llama3.1:8b']
 0.15s  >>> ENVÍO pregunta 1 con el modelo por defecto
 0.55s  >>> CAMBIO el selector de modelo a llama3.1:8b (a mitad de la respuesta)
22.06s  STATUS: Consulta lista
29.54s  TEXTO: '**Respuesta corregida:** Hay 3,421,083 pedidos en total.'
30.59s  >>> ENVÍO pregunta 2 (debe usar llama3.1:8b)
60.75s  STATUS: La consulta no se pudo completar        ← timeout de Medio: 8b recién cargado (sección 2)
```
Servidor (envoltorio):
```
..572.420  responder_pregunta_instacart INICIO modelo=qwen2.5-coder:1.5b timeout_sql=30.0   ← ÚNICA fase SQL de la pregunta 1
..594.098  … FIN → responder_en_vivo ABORTADO por StopException   (hilo abandonado por el cambio de selector)
..601.689  responder_en_vivo FIN (modelo del turno=qwen2.5-coder:1.5b)  ← la ejecución nueva ESPERÓ y reutilizó
..602.857  responder_pregunta_instacart INICIO modelo=llama3.1:8b timeout_sql=30.0          ← pregunta 2
..632.934  … FIN ok=False intentos=0 (timeout a los 30 s)
```
Proxy (llamadas reales a Ollama):
```
#14 POST /api/chat  modelo qwen2.5-coder:1.5b   ← SQL de la pregunta 1 (una sola vez)
#16 POST /api/chat  modelo qwen2.5-coder:1.5b   ← redacción (stream) de la pregunta 1: su modelo congelado
#17 POST /api/chat  modelo qwen2.5-coder:1.5b   ← reintento estricto de cifras (34,210,83 → 3,421,083)
#20 POST /api/chat  modelo llama3.1:8b          ← pregunta 2: el modelo nuevo
```

Qué pasa exactamente al cambiar el modelo a mitad de una pregunta:
1. La pregunta en curso **no se duplica ni cambia de modelo**. Hay una
   sola fase SQL, la ejecución nueva espera a ese hilo (mecanismo del
   HOTFIX 1), y la redacción usa el modelo con el que se envió. El turno
   queda registrado como `qwen2.5-coder:1.5b`.
2. El modelo nuevo se usa **desde la siguiente pregunta**.
3. **Hallazgo:** la pregunta 2, con 8b recién cargado y esfuerzo Medio,
   **terminó en timeout** a los 30 s, con un error controlado en pantalla.
   Es lo que predice la sección 2 (el 8b en frío tarda 106 s) y es la razón
   del nivel Ultra.
4. La fase SQL de la pregunta 1 tardó 21.7 s porque el 1.5b se había
   descargado durante la investigación: fue su costo en frío (22.1 s en la
   tabla), no un efecto del cambio de modelo. Se repitió el escenario dos
   veces, con el mismo resultado.

Este caso también queda fijado en un test determinista:
`test_cambiar_de_modelo_a_mitad_de_una_pregunta_no_crea_otro_calculo`.

---

## 4. Verificación en navegador real (Firefox headless)

Popover ⚙️ abierto con `qwen2.5-coder:1.5b` cargado:
```
/api/ps (curl) antes: ['qwen2.5-coder:1.5b']
Modelo de Ollama: qwen2.5-coder:1.5b · Esfuerzo: Medio
Timeout por llamada: 30 s (SQL) · 30 s (respuesta). El timeout de la consulta en DuckDB (10 s) es una guarda y no cambia.
🟢 qwen2.5-coder:1.5b cargado · 1,116 MiB en memoria (según /api/ps)
[clic en 🧹 Liberar modelo de memoria]
/api/ps (curl) después del clic: []                       ← verificado por FUERA de la app
Ningún modelo cargado en memoria (/api/ps vacío).
Liberado: qwen2.5-coder:1.5b. El estado de arriba es el de /api/ps después de liberar.
Log del servidor: sin errores
```

---

## 5. Tests

`tests/test_app_instacart.py` suma **16 tests** (de 33 a 49). El fixture
simula `modelos_disponibles` y `modelos_cargados`, para que los tests no
consulten al Ollama real. `ModeloSimulado` ahora registra el modelo y el
`timeout_s` de cada llamada.

| Test | Qué comprueba |
|---|---|
| `test_selector_lista_solo_los_modelos_que_devuelve_ollama` | Las opciones son exactamente las de `modelos_disponibles` (simulado); si el default del proyecto no está, se elige el primero real |
| `test_selector_respeta_el_modelo_por_defecto_si_esta_disponible` | Respeta `VENTIA_LLM_MODELO_INSTACART` si está en la lista |
| `test_ollama_caido_se_informa_sin_romper_la_app` | Aviso con `systemctl status ollama`, sin selector, sin excepción; el chat sigue |
| `test_modelo_elegido_se_usa_en_la_siguiente_pregunta` | Las llamadas de SQL y de stream usan el modelo elegido; los metadatos del turno lo registran |
| `test_esfuerzo_cambia_el_timeout_efectivo_de_la_siguiente_llamada[Bajo…Extremo]` (5) | Para cada nivel: `timeout_s` de SQL y de stream = los de la tabla |
| `test_medio_es_el_default_y_conserva_los_timeouts_previos` | Medio es el default y vale 30/30 (= `TIMEOUT_LLM_*_S`); los niveles son crecientes |
| `test_liberar_memoria_llama_a_liberar_modelo_y_muestra_el_estado_real` | "1,116 MiB" antes; el clic llama a `liberar_modelo("qwen2.5-coder:1.5b")`; después, "Ningún modelo cargado" |
| `test_liberar_esta_deshabilitado_sin_modelos_cargados_o_con_pregunta_en_curso` | Botón deshabilitado en los dos casos |
| `test_cambiar_de_modelo_a_mitad_de_una_pregunta_no_crea_otro_calculo` | Con un hilo que todavía calcula la fase SQL y el selector cambiado: **0** fases SQL nuevas; el stream usa el modelo congelado (`modelo-a`, 60 s); la siguiente pregunta usa `modelo-b` |
| `test_liberar_modelo_hace_post_api_generate_con_keep_alive_0` | **Nivel HTTP**, contra un servidor que imita Ollama: `POST /api/generate` con `model` y `keep_alive: 0`, sin prompt |
| `test_modelos_cargados_lee_api_ps` | **Nivel HTTP**: `GET /api/ps`, con el `size` parseado |
| `test_liberar_modelo_inexistente_da_error_controlado` | **Nivel HTTP**: un 404 se convierte en `LlmNoDisponible` |

Los 33 tests anteriores del archivo (Fases 6 y 8 y el HOTFIX 1) pasan sin
cambios en sus aserciones. Solo cambiaron el fixture (los simuladores
nuevos) y `ModeloSimulado`, que ahora registra más datos.

---

## 6. Desviaciones (con motivo)

1. **No hay botones de Iniciar/Detener Ollama**: el paso 0 demostró que no
   es posible sin contraseña de administrador. Es la rama "SI NO es
   posible" del enunciado.
2. **Se editó `src/llm/responder_instacart.py`** (3 líneas, aditivas): el
   parámetro `modelo`. Sin él, elegir el modelo por sesión solo habría sido
   posible cambiando una variable de entorno global del proceso, que
   afectaría a todas las sesiones.
3. **Ultra quedó en 150 s y no en un número redondo más bajo**: tiene que
   cubrir el 8b en frío, medido en 106 s.
4. **La configuración se congela al enviar la pregunta.** Es la
   interpretación de "se usa en la siguiente pregunta" que evita que una
   respuesta mezcle dos modelos: SQL de uno y redacción de otro.

---

## 7. Riesgos

- **La primera pregunta después de cargar o liberar un modelo es lenta**
  (1.5b ~22 s, 8b ~106 s, 14b ~220 s). Con un esfuerzo bajo para ese caso,
  da timeout (visto con el 8b en Medio). Liberar memoria tiene ese costo
  en la pregunta siguiente; la ayuda del botón lo advierte.
- **Dos modelos cargados a la vez agotan la RAM**: con 1.5b y 8b cargados
  quedaron 229 MB libres (4.7 GB disponibles). Ollama mantiene cada modelo
  ~5 min (`keep_alive` por defecto) después de usarlo.
- **El popover consulta a Ollama en cada ejecución** (`/api/tags` y
  `/api/ps`, milisegundos). Si Ollama tarda, esa ejecución espera hasta el
  timeout de administración (10 s).
- **Liberar un modelo que otra sesión está usando** (otra pestaña o tu
  servidor 119499) le hará pagar la carga en frío. La app solo deshabilita
  el botón para la pregunta en curso de **esta** sesión.
- Extremo puede significar esperas muy largas ante un modelo colgado
  (sección 2).

---

## 8. Estado final

`git status --short` antes del commit:
```
 M README.md
 M src/llm/client.py
 M src/llm/responder_instacart.py
 M src/ui/app_instacart.py
 M tests/test_app_instacart.py
?? docs/FASE9_INVESTIGACION.md
?? docs/FASE9_REPORTE.md
```

`pytest -q`:
```
342 passed, 2 xfailed in 200.46s
```
Son 344 = 328 anteriores + 16 de esta fase. Los 2 xfailed son los mismos de
la Fase 5.
