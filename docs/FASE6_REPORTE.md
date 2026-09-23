# Fase 6: interfaz de dos paneles (chat Instacart con historial + dashboard)

Rama: `migracion-instacart`. Fecha: 2026-09-22.

Qué se entrega:
- una app nueva, `src/ui/app_instacart.py`, con el chat a la izquierda y el
  dashboard a la derecha;
- la corrección del bug de cifras de la Fase 5.

Lo que no cambió:
- **Northwind**: `src/ui/app.py`, `src.llm.responder` y el resto de su
  ruta no se tocaron, y la app nueva no los importa.
- **El dashboard**: `src/ui/dashboard.py` se reorganizó en funciones para
  poder reutilizarlo (sección 3.3), pero sigue haciendo lo mismo.
- **Las respuestas de referencia**: el chat no se compara contra
  `src/analytics`, como en todas las fases anteriores.

```
streamlit run src/ui/app_instacart.py
```

---

## 0. Paso 0

El commit de la Fase 5 existe y el árbol estaba limpio: no hizo falta
comitear nada.

```
commit f7ccb284232175af02e16236613569d8ea39bec7
    Fase 5: IA local sobre Instacart, esquema dinámico, guardas reales y modelo por defecto qwen2.5-coder:1.5b
 14 files changed, 2417 insertions(+)
```

---

## 1. Paso 0.5: corrección del bug de cifras

**Problema** (Fase 5, sección 8): la base devolvió 3421083 y
qwen2.5-coder:1.5b escribió *"Hay 34,210,83 pedidos en total."*. El número
quedó alterado, no solo mal formateado.

**Corrección**: el módulo nuevo `src/llm/cifras_instacart.py` y
`finalizar_redaccion()` en `responder_instacart.py`.

1. Se extraen los tokens numéricos de la respuesta. Cada uno debe coincidir
   con un **valor permitido**:
   - una celda numérica del resultado, o un número dentro de una celda de
     texto (p. ej. "2% milk");
   - un número de la pregunta ("product_id = 1");
   - la cantidad de filas devueltas.

   Los decimales se comparan por redondeo a los dígitos mostrados
   (0.59337 → "0.59"), y también como porcentaje (0.59337 → "59.3%").
2. **"Ignorar separadores" se implementó con cuidado.** Quitar todas las
   comas y puntos convertiría "34,210,83" en 3421083 y lo daría por bueno.
   Por eso solo se aceptan separadores **bien formados**:
   - grupos de miles de exactamente 3 dígitos, con el mismo separador
     (`3,421,083`, `3.421.083`, espacio fino);
   - una parte decimal.

   `lecturas_numericas("34,210,83") == []`: ese token no tiene ninguna
   lectura válida y se rechaza siempre.
3. Si hay una cifra inválida se hace **un solo reintento** de la redacción.
   Ese reintento usa un prompt estricto
   (`prompt_respuesta_estricta_instacart`) con la cifra alterada y los
   valores exactos preformateados.
4. Si el reintento también falla, o si da timeout, la respuesta se arma con
   una **plantilla sin modelo** (`respuesta_plantilla`), con el valor real
   preformateado: *"Resultado (count_star()): 3,421,083."*
5. `ResultadoInstacart` guarda de dónde salió la respuesta
   (`origen_respuesta`: `modelo` / `reintento` / `plantilla`) y qué cifras
   se rechazaron (`cifras_rechazadas`). La UI muestra ambas cosas.

**Antes y después, con el modelo real y la pregunta 1 del banco de
coherencia** (`python -m src.llm.responder_instacart "¿Cuántos pedidos hay
en total?"`, dos corridas, mismo resultado):

| | Respuesta |
|---|---|
| Antes (Fase 5) | *"Hay 34,210,83 pedidos en total."* (cifra alterada, sin detectar) |
| Después (Fase 6) | *"Hay 3,421,083 pedidos en total."* |

```
Resultado: count_star()
           3421083
Respuesta: Hay 3,421,083 pedidos en total.
Cifras:    la primera redacción alteró ['34,210,83']; respuesta final de: reintento
```

La primera redacción del modelo **sigue** escribiendo "34,210,83": el modelo
no cambió. Lo que cambió es que ahora se detecta y se corrige.

Tests con los valores esperados escritos a mano (`tests/test_cifras_instacart.py`,
17 tests):
- el caso real: 3,421,083 y 3421083 se aceptan; "34,210,83" se rechaza;
- las variantes "342,108,3", "3,421,084" y "342108";
- plantilla si el reintento vuelve a fallar (el texto contiene "3,421,083"
  y nunca la variante alterada);
- el reintento cuando corrige bien;
- ninguna llamada extra si la redacción ya era correcta;
- plantilla si el reintento da timeout;
- un caso sintético de flujo completo: la fixture tiene 21 pedidos, el mock
  inventa "57" y el reintento escribe 21.

---

## 2. ¿Streaming real? Sí

- **Evidencia técnica**: `ollama-python` 0.3.3 acepta `stream=True` en
  `Client.chat` y devuelve un generador de dicts `{"message": {"content"},
  "done"}`. Medido contra Ollama real (qwen2.5-coder:1.5b, "cuenta del 1 al
  20"): 136 fragmentos; el primero llegó a los 0,38 s y el último a los 8,0 s.
- **Implementación**:
  - `client.chat_con_modelo_stream(...)` es un generador. En streaming, el
    `timeout_s` de httpx se aplica **por lectura** (tiempo máximo sin recibir
    un fragmento), no a la respuesta completa.
  - `responder_instacart.redaccion_en_stream(res)` va entregando los
    fragmentos. Al terminar, llama a `finalizar_redaccion` (la validación de
    cifras). Si Ollama falla a mitad, deja un error controlado en `res.error`.
  - La UI llama a `st.write_stream(redaccion_en_stream(res))`.
- **Prueba de que llega en fragmentos** (`test_stream_llega_en_fragmentos_no_todo_de_una_vez`):
  - un servidor HTTP local imita `/api/chat` y manda 4 líneas NDJSON
    *chunked* con 0,4 s de pausa entre ellas;
  - el cliente `ollama` real recibe el primer fragmento en menos de 0,4 s,
    mucho antes del último (≈1,6 s).

  `test_redaccion_en_stream_entrega_fragmentos_y_luego_valida` comprueba
  además que la validación ocurre **después** de agotar el stream.
- **Observado en el servidor real** (sección 5): los *deltas* de Streamlit
  llegan uno por palabra (`'Hay 34,210,83 pedidos▕'` → `'… en▕'` →
  `'… en total▕'`…), cada ~60 ms.

**Consecuencia de combinar streaming con validación de cifras (decisión de
diseño):**
- El usuario ve la redacción **mientras se genera**, así que puede alcanzar
  a ver una cifra alterada antes de que se valide.
- Cuando el stream termina y la cifra no es válida, la UI muestra un aviso
  `🔢 La redacción del modelo alteró la cifra 34,210,83 …` y debajo la
  **Respuesta corregida**.
- En el historial queda guardada **solo** la versión corregida.
- La alternativa (no mostrar nada hasta validar) eliminaba el streaming, que
  el brief pide explícitamente.

---

## 3. Diseño de la app de dos paneles

### 3.1 Layout
- `st.columns([5, 6])`, persistente y sin pestañas: el chat a la izquierda
  y el dashboard a la derecha.
- El dashboard se dibuja **antes** de procesar la pregunta del chat. Así ya
  está en pantalla mientras el chat genera la respuesta.

### 3.2 Chat (izquierda)
- `st.session_state["historial"]` es una lista de
  `{rol, contenido, sql, metadatos}`. Los metadatos incluyen: `pregunta`,
  `ok`, `tipo_error`, `intentos`, `motivo_reintento`, `origen_respuesta`,
  `cifras_rechazadas`, `columnas`, `filas` (máx. 25), `n_filas`,
  `contexto_usado` y `modelo`. El historial persiste entre reruns de la
  misma sesión.
- **Un turno**:
  1. `responder_pregunta_instacart(pregunta, redactar=False, contexto=…)`,
     que hace SQL, guardas y reintento, dentro de un `st.status`;
  2. `st.write_stream(redaccion_en_stream(res))`.

  La UI no rehace el flujo: solo muestra el `ResultadoInstacart`.
- **Cada turno muestra**:
  - la pregunta;
  - la respuesta o el error;
  - un expander *"Detalles: SQL, intentos y datos"*, cerrado por defecto
    pero siempre visible, con el SQL (o "No se llegó a generar SQL"), el
    motivo del reintento, la cantidad de intentos, el contexto usado, el
    modelo y la tabla del resultado.
- **Los errores se muestran con el mismo peso que un turno exitoso**, como
  `st.error` con título e ícono, nunca como excepción cruda:
  - `🛡️ Consulta rechazada por las guardas de seguridad` (escritura, SQL no
    seguro);
  - `⏱️ Se canceló por tiempo` (timeout del modelo o de la consulta);
  - `⚠️ No se pudo responder` (cualquier otro error).
- **Pregunta en curso**: la pregunta enviada se guarda en
  `st.session_state["pregunta_en_curso"]` y se borra solo cuando el turno
  termina. Si un rerun completo interrumpe el turno, la ejecución siguiente
  la vuelve a procesar: no se pierde en silencio (observado en la sección 5).

### 3.3 Dashboard (derecha): reutilizado, no duplicado
- `dashboard.py` era un script con todo el código a nivel de módulo:
  importarlo lo habría ejecutado completo, `set_page_config` incluido.
- Se reorganizó en funciones **sin cambiar su comportamiento**:
  - `inicializar_estado()`;
  - `render_controles(area, horizontal=False)`: selector, "Actualizar
    datos" y bloque del PDF, dibujados en `area`;
  - `render_panel(necesidad)`;
  - `main()`, que en modo standalone pone los controles en `st.sidebar` como
    antes, más `if __name__ == "__main__": main()`.
- Las gráficas, `GRAFICOS`, las cachés y los botones son el mismo código.
- **Verificación**: los 35 tests existentes del dashboard
  (`test_dashboard_paneles.py`, `test_dashboard_pdf_boton.py`,
  `test_dashboard_servicio.py`) pasan **sin ningún cambio**. Usan
  `at.sidebar.radio[0]` y los botones de la barra lateral en el modo
  standalone.
- En la app de dos paneles: `render_controles(st.container(),
  horizontal=True)` + `render_panel(...)`, dentro de un `@st.fragment`
  (motivo en la sección 5).

### 3.4 Solo la ruta Instacart
La app importa `src.llm.responder_instacart`, `src.llm.client` y
`src.ui.dashboard`, y nada de Northwind. Lo verifica
`test_no_importa_la_ruta_northwind`.

---

## 4. Política de contexto e historial

**Política**: al modelo de SQL se le mandan la pregunta actual y **los
últimos 3 turnos exitosos** (los que ejecutaron SQL y trajeron filas), cada
uno como par (pregunta, SQL):
- nunca las filas del resultado;
- nunca los turnos fallidos (un rechazo de escritura no se usa como
  contexto);
- nunca el historial completo.

La redacción (llamada 2) no recibe contexto: solo redacta el resultado de
la pregunta actual.

**Justificación medida:**

| Dato | Valor | Fuente |
|---|---|---|
| Contexto máximo del modelo | 32,768 tokens | `ollama show qwen2.5-coder:1.5b` |
| Contexto con el que Ollama 0.34 **lo carga** | **4,096 tokens** | `GET /api/ps` → `"context_length": 4096` (`num_ctx` por defecto; no se cambió) |
| Prompt de SQL sin contexto (esquema + reglas + pregunta) | 1,738 tokens | `prompt_eval_count` |
| Con 1 turno previo | 1,809 (+71) | `prompt_eval_count` |
| Con 3 turnos previos | 1,885 (+147, ~49 por turno) | `prompt_eval_count` |

- Con 3 turnos se usa el ~46% de los 4,096 y queda amplio margen para la
  salida.
- Mandar las filas o el historial completo crecería sin límite. Además,
  cuando el prompt pasa de `num_ctx`, Ollama lo **trunca en silencio** y lo
  primero en perderse es el esquema.
- Se toman 3 turnos y no 1 porque una pregunta de seguimiento puede referirse
  a algo de dos turnos atrás, y el costo es bajo (~49 tokens por turno).

**Prueba de la pregunta de seguimiento ambigua**: *"¿Cuántos pedidos hay de
tipo train?"* → *"¿y de tipo test?"*.

| Escenario | SQL generado | Resultado |
|---|---|---|
| Sin contexto | `SELECT * FROM orders WHERE eval_set = 'test'` | 1,000 filas; respuesta inútil: *"El tipo de test en este dataset es "test"."* |
| Con contexto, si el SQL previo era correcto (`… FROM orders WHERE eval_set = 'train'`) | `SELECT COUNT(*) FROM orders WHERE eval_set = 'test'` | **75000 → "Hay 75000 pedidos en esa situación."** Se resuelve bien. |
| **Conversación real en la app** (sección 6) | turno 1: `COUNT(*) FROM order_products__train`; seguimiento: `order_products__test` → CatalogException → reintento `COUNT(*) FROM order_products` | **No se resuelve**: *"El tipo de test es "test"."* |

Conclusión:
- **El diseño sí transmite la intención del seguimiento.** El modelo
  entendió "lo mismo, pero test" y buscó la tabla análoga.
- **El resultado depende de que el turno anterior sea correcto.** En la
  conversación real, el modelo interpretó "pedidos de tipo train" como
  *líneas* de `order_products__train`, y el contexto propagó ese error.
  Queda como **limitación conocida**.

---

## 5. ¿Los dos paneles son concurrentes? No. Comportamiento observado

**Lo que dice el código de Streamlit 1.39**:
- `runner.fastReruns` hace que una interacción que pide un **rerun
  completo** interrumpa la ejecución en curso (`app_session.py:373`).
- El rerun de un **fragmento** "should *not* cancel the current script run"
  y queda **en cola** (`scriptrunner_utils/script_requests.py:78-89`).
- Streamlit ejecuta un solo script a la vez por sesión: dentro de una misma
  sesión no hay concurrencia real.

**Observación real**:
- Servidor: `streamlit run src/ui/app_instacart.py --server.headless true`
  con Ollama y la base real.
- Cliente: uno mínimo del protocolo websocket de Streamlit
  (`BackMsg`/`ForwardMsg` sobre `tornado.websocket`), que manda los mismos
  mensajes que el navegador. Tiempos desde el inicio de cada escenario.

**A. Diseño elegido (dashboard dentro de `st.fragment`): toco el dashboard
~1 s después de enviar la pregunta.**
```
3.10s  >>> ENVÍO pregunta al chat: '¿Cuántos pedidos hay en total?'
3.22s  STATUS chat: Generando y ejecutando la consulta…
4.11s  >>> TOCO el dashboard (radio -> N3), rerun de FRAGMENTO
4.42s  STATUS chat: Consulta lista
5.64s  CHAT texto: 'Hay 34,210,83 pedidos▕'          ← streaming real, palabra por palabra
5.82s  CHAT texto: 'Hay 34,210,83 pedidos en total.▕'
8.68s  ALERTA: 'La redacción del modelo alteró la cifra 34,210,83 …'
8.68s  CHAT texto: '**Respuesta corregida:** Hay 3,421,083 pedidos en total.'
8.68s  SCRIPT_FINISHED FINISHED_SUCCESSFULLY
8.72s  TITULO dashboard: N3 — Picos de demanda               ← el clic se aplica aquí
8.80s  SCRIPT_FINISHED FINISHED_FRAGMENT_RUN_SUCCESSFULLY
```
- El chat **no se interrumpe**.
- El dashboard **espera**: el clic se aplicó 4,6 s después, cuando el chat
  terminó.
- El clic no se pierde, pero no hay concurrencia.

**B. Contraste (la misma interacción como rerun completo, que es lo que
pasaría sin fragmento).**
```
0.27s  >>> ENVÍO pregunta al chat
0.39s  STATUS chat: Generando y ejecutando la consulta…
1.28s  >>> TOCO el dashboard (radio -> N3), rerun COMPLETO
1.42s  TITULO dashboard: N3 — Picos de demanda               ← el dashboard responde al instante
1.48s  STATUS chat: Generando y ejecutando la consulta…      ← el chat EMPIEZA DE NUEVO
7.08s  CHAT texto: '**Respuesta corregida:** Hay 3,421,083 pedidos en total.'
```
- El dashboard responde al instante.
- La respuesta en curso del chat **se descarta y se vuelve a pedir**
  (gracias a `pregunta_en_curso` no se pierde). El hilo abandonado sigue
  consultando a Ollama en segundo plano, así que el trabajo se duplica.
- **Por eso se eligió el fragmento**: nunca tira una respuesta del chat, a
  cambio de que el dashboard espere.

**C. Caso inverso: envío una pregunta al chat mientras el dashboard genera
el PDF.**
```
0.27s  >>> CLICK en Generar PDF (rerun de FRAGMENTO)
1.27s  STATUS: Generando PDF…
1.27s  >>> ENVÍO pregunta al chat mientras se genera el PDF
1.29s  NEW_SESSION                                           ← el rerun completo se adelanta al fragmento
5.31s  STATUS chat: Generando y ejecutando la consulta…
13.73s CHAT texto: '**Respuesta corregida:** Hay 3,421,083 pedidos en total.'
```
- El rerun del chat **se adelanta a** la ejecución del fragmento del PDF.
- `generar_pdf()` siguió en el hilo abandonado y **sí escribió el archivo**
  (`data/pdf/reporte_instacart.pdf` quedó con hora 21:51), pero se perdió el
  mensaje *"Generado: … KB"* de ese clic.
- El chat tardó ~12,5 s en vez de ~7 s por competir por CPU con la
  generación del PDF.
- Es un **riesgo documentado** (sección 10).

**Evaluación del criterio de salida de la Fase 2A**:
- Streamlit **sí logró** streaming real y dos paneles estables en la misma
  sesión: los tests de AppTest y los escenarios A a C terminan sin
  excepciones y sin perder la conversación.
- Por lo tanto **no se activa** el cambio de la capa de UI.
- La limitación es estructural: un solo script por sesión, sin concurrencia
  real entre paneles. Con el fragmento, el dashboard espera al chat.
- Si en una fase futura se exige que ambos paneles funcionen al mismo
  tiempo, eso sí requeriría otra capa de UI (por ejemplo, un backend con
  endpoints separados).

---

## 6. Conversación real en la app (3 turnos)

Hecha **a través de la app completa** (`AppTest` de `src/ui/app_instacart.py`,
sin mocks: qwen2.5-coder:1.5b y `data/instacart.duckdb` reales):

**Turno 1**: *¿Cuántos pedidos hay de tipo train?* (3,2 s, contexto: 0)
```
SQL:       SELECT COUNT(*) FROM order_products__train
Resultado: 1384617
Respuesta: Hay 1,384,617 pedidos de tipo train.      (origen: modelo, cifras válidas)
```
⚠ Cifra fiel al resultado, pero **respuesta semánticamente equivocada**:
cuenta *líneas* de pedido de `train`, no *pedidos* con `eval_set='train'`
(que son 131,209). La validación de cifras no puede detectar esto, porque el
número sí está en el resultado.

**Turno 2**: *¿y de tipo test?* (4,5 s, contexto: 1 turno)
```
SQL intento 1: SELECT COUNT(*) FROM order_products__test
               → CatalogException: Table with name order_products__test does not exist!
SQL intento 2: SELECT COUNT(*) FROM order_products
Resultado:     33819106
Respuesta:     El tipo de test es "test".
```
El seguimiento no se resolvió (análisis en la sección 4).

**Turno 3**: *Borra todos los pedidos de la base de datos.* (3,4 s, contexto: 2 turnos)
```
SQL:   DELETE FROM order_products__train; DELETE FROM order_products__prior; DELETE FROM orders; …
En pantalla (st.error): 🛡️ **Consulta rechazada por las guardas de seguridad.**
                        No puedo ejecutar esa consulta: La consulta contiene más de una sentencia.
```
Se muestra con el mismo formato que un turno normal (burbuja del asistente
con su expander de Detalles y el SQL visible), sin excepción cruda. No hubo
reintento (es un intento de escritura). Al final había 6 mensajes en
pantalla y 6 en `historial`.

---

## 7. Tests nuevos

`tests/test_app_instacart.py` (11 tests). Usan AppTest con el modelo
simulado (`chat_con_modelo` / `chat_con_modelo_stream`) y la fixture chica
para el chat y el dashboard:

| Test | Qué comprueba |
|---|---|
| `test_layout_de_dos_columnas_chat_izquierda_dashboard_derecha` | Columna 0: chat. Columna 1: selector "Necesidad" y botón del PDF. Nada en la barra lateral. |
| `test_no_importa_la_ruta_northwind` | El código de la app no importa `src.llm.responder` ni `src/ui/app.py`. |
| `test_pregunta_muestra_sql_y_respuesta` | Respuesta, bloque SQL, expander "Detalles" y el turno en `historial`. |
| `test_pregunta_de_escritura_se_muestra_como_rechazo_claro` | Un solo `st.error` que dice "Consulta rechazada…", sin Traceback, con el SQL visible. |
| `test_timeout_del_modelo_se_muestra_como_error_controlado` | `LlmTimeout` se muestra como "Se canceló por tiempo". |
| `test_cifra_alterada_en_el_stream_se_avisa_y_se_corrige` | Aviso 🔢, "Respuesta corregida" y el historial guarda la versión corregida. |
| `test_historial_persiste_entre_dos_preguntas_y_da_contexto` | 4 mensajes tras 2 preguntas; la 2.ª llamada de SQL recibió el turno previo como contexto. |
| `test_dashboard_sigue_funcionando_dentro_de_la_app` | N3, N9 y N11 se muestran sin excepción después de una pregunta, y la conversación sigue. |
| `test_contexto_solo_turnos_exitosos_y_como_maximo_n` | Toma los últimos 3 turnos exitosos e ignora el `DELETE` rechazado. |
| `test_stream_llega_en_fragmentos_no_todo_de_una_vez` | Streaming real contra un servidor NDJSON *chunked* (sección 2). |
| `test_redaccion_en_stream_entrega_fragmentos_y_luego_valida` | La validación ocurre después de agotar el stream. |

Además hay 17 tests en `tests/test_cifras_instacart.py` (sección 1).

Nota: al escribir `test_historial_persiste…`, el mock original redactaba
"Hay 3 pedidos de tipo train" cuando la fixture tiene 2. La validación de
cifras lo detectó y disparó el reintento, y el test falló por eso. Se
corrigió el mock (sin cifras), no la validación.

---

## 8. Los 321 tests previos siguen intactos

- `git diff HEAD --stat -- tests/` sale vacío: **ningún test previo se
  modificó**. Los únicos archivos de test nuevos son `test_app_instacart.py`
  y `test_cifras_instacart.py`.
- `pytest -q` sin esos dos archivos: **`319 passed, 2 xfailed`**,
  idéntico al cierre de la Fase 5.
- `src/ui/app.py` no tiene cambios (`git diff HEAD -- src/ui/app.py` vacío).

---

## 9. Desviaciones (con motivo)

1. **Se editó `src/ui/dashboard.py`.** La instrucción era no tocarlo "en su
   comportamiento", pero reutilizarlo sin reorganizarlo era imposible: el
   archivo es un script, e importarlo lo ejecuta entero y llama a
   `set_page_config` por segunda vez. El comportamiento standalone se
   mantiene y lo prueban los 35 tests previos del dashboard, sin cambios.
   El único parámetro nuevo es `horizontal` del selector, con valor por
   defecto `False` (igual que antes).
2. **El usuario puede ver una cifra alterada durante el stream** antes de
   la corrección. Es el costo de mostrar tokens en vivo y validar al final.
   Queda señalado con un aviso visible y el historial guarda solo la
   versión corregida.
3. **El reintento estricto de la redacción no usa streaming.** Es una
   corrección, no la respuesta principal, y dura ~2,8 s según lo observado
   (entre 5,82 s y 8,64 s en el escenario A).
4. **La validación de cifras rechaza espacios comunes como separador de
   miles** ("3 421 083"). Es deliberado: el espacio común no se puede
   distinguir de números separados en una frase. Esos casos terminan en
   reintento o plantilla, nunca en una cifra alterada.
5. **`num_ctx` se dejó en el valor por defecto de Ollama (4096).** La
   política de contexto cabe con holgura y subirlo aumentaría la RAM, que
   ya es escasa en esta máquina (Fase 1).

---

## 10. Riesgos

- **Errores semánticos que la validación no ve.** Si el SQL responde otra
  pregunta (líneas en vez de pedidos, turno 1 de la sección 6), la cifra
  es fiel al resultado y pasa. La validación garantiza fidelidad al
  resultado, no que el resultado sea el correcto. Comparar contra las
  referencias sigue fuera de alcance.
- **Un seguimiento hereda los errores del turno anterior** (sección 4).
- **No hay concurrencia real entre paneles:**
  - tocar el dashboard mientras el chat genera deja el clic en cola hasta
    que el chat termina (escenario A, ~4,6 s de espera);
  - una pregunta enviada mientras se genera el PDF se adelanta al fragmento:
    el PDF se escribe, pero su confirmación se pierde (escenario C).
- **La validación puede rechazar cifras derivadas legítimas.** Si el modelo
  calcula algo que no está en el resultado (una diferencia, un promedio
  mental), se descarta y se usa el reintento o la plantilla. Es un falso
  positivo seguro: nunca deja pasar una cifra inventada, a veces descarta
  una redacción aceptable.
- **El timeout en streaming es por lectura.** Un modelo que emite muy
  lentamente, pero sin pausas de más de 30 s, puede tardar más de 30 s en
  total.
- **Carga en frío**: la primera pregunta después de arrancar Ollama tarda
  más (en la Fase 5 se midieron 20,8 s).

---

## 11. Estado final

`git status --short` antes del commit:
```
 M src/llm/client.py
 M src/llm/nl2sql_instacart.py
 M src/llm/prompts_instacart.py
 M src/llm/responder_instacart.py
 M src/ui/dashboard.py
?? docs/FASE6_REPORTE.md
?? src/llm/cifras_instacart.py
?? src/ui/app_instacart.py
?? tests/test_app_instacart.py
?? tests/test_cifras_instacart.py
```

`pytest -q`:
```
347 passed, 2 xfailed in 218.00s
```
En total son 349 tests: los 321 previos más 28 nuevos (17 de cifras y 11 de
la app).

`pytest -m integration -q`:
```
34 passed, 313 deselected, 2 xfailed in 105.39s
```
Los 2 xfail son las limitaciones de qwen2.5-coder:1.5b documentadas en la
Fase 5.
