# Fase 8: interfaz de vista alternante estilo Claude.ai

Rama: `migracion-instacart`. Fecha: 2026-09-23.

Qué cambia:
- solo la capa de presentación, `src/ui/app_instacart.py`;
- las dos columnas simultáneas de la Fase 6 se reemplazan por dos íconos
  (💬 / 📊) que alternan la vista a todo el ancho;
- las 11 necesidades pasan a estar "fijadas" en la barra lateral.

Qué no cambia:
- el responder, el streaming, la validación de cifras, las guardas, el
  modelo, `src/dashboard`, `src/analytics` y `src/pdf`;
- `src/ui/dashboard.py`: `git diff HEAD -- src/ui/dashboard.py` sale vacío;
- Northwind (`legacy/`).

Punto de partida: `57d3a86` (Fase 7), árbol limpio.

---

## 1. Diseño del nuevo layout

```
┌ sidebar ────────────────┐ ┌ área principal ───────────────────────────────┐
│ 📌 Necesidades fijadas  │ │ [💬] [📊]   ← el activo es type="primary"      │
│ [N1 · Recompra por…   ] │ │ (aviso de pregunta en proceso, si la hay)      │
│ [N2 · Canasta: pares… ] │ │                                                │
│ …                       │ │  vista == "chat"      → chat a todo el ancho   │
│ [N11 · Predicción del…] │ │  vista == "dashboard" → dashboard completo     │
└─────────────────────────┘ └────────────────────────────────────────────────┘
```

- **`st.session_state["vista"]`**: `"chat"` o `"dashboard"`, con `"chat"`
  por defecto.
- **Barra de íconos**:
  - dos `st.button` pequeños, `💬` (help "Chat") y `📊` (help
    "Dashboard"), en una fila de columnas estrechas (`[1, 1, 18]`);
  - un `on_click` cambia `vista` antes del rerun;
  - el activo se dibuja con `type="primary"` y el inactivo con
    `type="secondary"`.
- **Vista activa a todo el ancho**: solo se dibuja la vista elegida.
  - Chat: `render_chat()`. Es el mismo historial, el mismo `st.chat_input`
    y el mismo `responder_en_vivo` de la Fase 6. Al estar a nivel
    superior, el `chat_input` queda fijo abajo, como en Claude.ai.
  - Dashboard: `dashboard.render_controles(...)` + `dashboard.render_panel(...)`,
    las mismas funciones de la Fase 6.
- **Sidebar "fijados"**:
  - 11 `st.button` a lo ancho, con etiqueta `"N3 · Picos de demanda"` y el
    texto alineado a la izquierda mediante CSS mínimo.
  - El de la necesidad abierta usa `type="primary"`, pero solo en la vista
    de dashboard.
  - El clic (`on_click=_abrir_necesidad`) pone `vista="dashboard"` y
    escribe la necesidad en **`st.session_state["necesidad_sel"]`**: es la
    misma clave del radio de `dashboard.render_controles`, así que no hay
    un estado paralelo.
  - **No hay ninguna sección de chats recientes.**
- **Título corto**: el selector del dashboard solo muestra "N1…N11", sin
  título. El texto se toma de la constante `TITULO` de cada módulo de
  `src/analytics` (vía `registro.NECESIDADES`), que es el mismo texto del
  título del panel (`r.titulo`). Solo se leen constantes: no se calcula
  nada.
- **La necesidad elegida se conserva al pasar por el chat.** Streamlit
  borra el estado de un widget con `key` en toda ejecución en la que no se
  dibuja, así que sin ajuste volvería a N1. `inicializar_estado()` lo
  reasigna (`st.session_state["necesidad_sel"] = st.session_state["necesidad_sel"]`),
  el truco documentado para conservarlo. Lo cubre
  `test_la_necesidad_elegida_sobrevive_a_pasar_por_el_chat`.
- **Pregunta en proceso**:
  - `st.chat_input` pasa a usar `on_submit=_al_enviar_pregunta`, que
    guarda `pregunta_en_curso` **antes** de la ejecución. Así la barra ya
    sabe que hay una respuesta en proceso cuando se dibuja.
  - La lógica de la Fase 6 no cambia: la pregunta se procesa en la vista
    de chat y `pregunta_en_curso` se borra solo al terminar.
  - Se agregaron dos avisos (sección 3):
    - en la vista de chat, *"⏳ Respondiendo… si cambias de vista ahora, la
      respuesta se cancela y se vuelve a generar al regresar."*, que se
      vacía (`st.empty`) al terminar;
    - en la vista de dashboard, *"⏳ Hay una pregunta pendiente en el chat:
      «…». Se procesa en la vista de chat: vuelve a 💬 para verla (se
      generará de nuevo desde el principio)."*

---

## 2. Capturas textuales (servidor real)

**Método**: `streamlit run src/ui/app_instacart.py --server.headless true`
con la base y Ollama reales, recorrido con el cliente del protocolo
websocket de Streamlit de la Fase 6 (mensajes `BackMsg`/`ForwardMsg`).

Novedad de esta fase: un **proxy TCP delante de Ollama** (`OLLAMA_HOST`
apuntando al proxy) registra cada `POST /api/chat`: `INICIO`, `RESPUESTA`
(primer byte de Ollama) y `FIN` (cierre de la conexión). Así, "¿se duplica
el trabajo?" se responde **contando llamadas reales al modelo**, no
deduciéndolo de la UI. Los tiempos entre corchetes son `time.time()`, para
cruzarlos con el log del proxy.

### (a) Vista de chat, con 💬 resaltado (carga inicial)
```
0.02s  NEW_SESSION
1.38s  TEXTO: '**📌 Necesidades fijadas**'
1.38s  BOTÓN '💬'   key=vista_chat       type=primary      ← activo
1.38s  BOTÓN '📊'   key=vista_dashboard  type=secondary
1.38s  TÍTULO/SUBTÍTULO: 💬 Pregúntale a la IA (Instacart)
1.38s  CHAT_INPUT visible
1.38s  SCRIPT_FINISHED FINISHED_SUCCESSFULLY
```
El dashboard no aparece en esta vista: no hay radio, ni título de
necesidad, ni botón de PDF.

### (b) Clic en el fijado "N3" de la sidebar
```
1.38s  >>> CLICK en el fijado 'N3' de la sidebar
1.44s  BOTÓN 'N3 · Picos de demanda'  key=fijado_N3        type=primary   ← fijado resaltado
1.44s  BOTÓN '💬'                     key=vista_chat       type=secondary
1.44s  BOTÓN '📊'                     key=vista_dashboard  type=primary   ← la vista cambió sola
1.44s  TÍTULO/SUBTÍTULO: 📊 Dashboard Instacart
1.44s  TÍTULO/SUBTÍTULO: 📄 Reporte PDF
1.61s  DESCARGA: ⬇️ Descargar PDF
1.66s  TÍTULO/SUBTÍTULO: N3 — Picos de demanda                         ← necesidad seleccionada
1.79s  SCRIPT_FINISHED FINISHED_SUCCESSFULLY
1.79s  >>> CLICK en 💬
1.91s  BOTÓN '💬'  type=primary   BOTÓN '📊'  type=secondary
1.91s  TÍTULO/SUBTÍTULO: 💬 Pregúntale a la IA (Instacart)
1.91s  CHAT_INPUT visible
```

### (c) Pregunta en curso + cambio de vista (medido, 2 variantes)

**c.1: clic en 📊 a los 0,3 s, durante la generación del SQL**
```
 0.15s [..148.944]  >>> ENVÍO pregunta: '¿Cuántos pedidos hay en total?'
 0.20s [..148.989]  AVISO chat: '⏳ Respondiendo… si cambias de vista ahora, la respuesta se cancela y se vuelve a generar…'
 0.20s [..148.989]  STATUS: Generando y ejecutando la consulta…
 0.45s [..149.246]  >>> CLICK en 📊 (0.3 s después de enviar la pregunta)
 0.46s [..149.251]  NEW_SESSION                                   ← rerun completo inmediato
 0.60s [..149.388]  ALERTA: '⏳ Hay una pregunta pendiente en el chat: «¿Cuántos pedidos hay en total?». …'
 0.84s [..149.630]  SCRIPT_FINISHED FINISHED_SUCCESSFULLY
 0.84s              --- me quedo 12 s en el dashboard sin tocar nada
12.84s [..161.633]  >>> CLICK en 💬 (vuelvo al chat)
12.89s [..161.678]  STATUS: Generando y ejecutando la consulta…   ← empieza DE NUEVO
13.76s [..162.554]  STATUS: Consulta lista
17.92s [..166.710]  TEXTO: '**Respuesta corregida:** Hay 3,421,083 pedidos en total.'
17.92s [..166.710]  SCRIPT_FINISHED FINISHED_SUCCESSFULLY
```
Log del proxy (llamadas reales a Ollama):
```
..149.061 INICIO    #11 POST /api/chat   ← SQL del intento original
..149.946 RESPUESTA #11                  ← Ollama responde 0.88 s después (el usuario ya está en el dashboard)
..150.016 FIN       #11
          (nada durante los 12 s en el dashboard)
..161.721 INICIO    #12 POST /api/chat   ← SQL otra vez (duplicado)
..162.560 INICIO    #13 POST /api/chat   ← redacción en stream
..164.167 INICIO    #14 POST /api/chat   ← reintento estricto de cifras (34,210,83 → 3,421,083)
```

**c.2: clic en 📊 a los 1,15 s, durante el stream de la redacción**
```
 1.05s [..041.043]  STATUS: Consulta lista
 1.26s [..041.258]  >>> CLICK en 📊
 1.28s [..041.279]  ALERTA: '⏳ Hay una pregunta pendiente en el chat: …'
13.48s [..053.480]  >>> CLICK en 💬 (vuelvo al chat)
13.54s              STATUS: Generando y ejecutando la consulta…   ← de nuevo desde el SQL
15.36s              TEXTO: 'Hay 34,210,83 pedidos▕'               ← streaming real
18.20s              TEXTO: '**Respuesta corregida:** Hay 3,421,083 pedidos en total.'
```
```
..040.194 INICIO    #6   ..041.037 RESPUESTA #6   ..041.268 FIN #6   ← SQL original (completo, se desecha)
..041.049 INICIO    #7   ..041.526 RESPUESTA #7   ..041.529 FIN #7   ← stream original: cortado 3 ms después del primer byte
..053.568 INICIO    #8   ← SQL otra vez
..054.424 INICIO    #9   ← stream otra vez
..055.629 INICIO    #10  ← reintento estricto de cifras
```

**Qué pasa exactamente (respuesta a la pregunta del punto 2):**
- **La generación NO sigue en segundo plano mientras estás en el
  dashboard.**
  - Cambiar de vista es un rerun completo, así que Streamlit abandona la
    ejecución en curso (`runner.fastReruns`, igual que el escenario B de
    la Fase 6).
  - La llamada HTTP que ya estaba en Ollama termina (c.1: el SQL respondió
    0,88 s después), pero el hilo abandonado se detiene en la siguiente
    llamada a Streamlit y su resultado se descarta.
  - Si lo abandonado era el stream (c.2), el cierre del generador corta la
    conexión y Ollama deja de generar.
  - Durante los 12 s en el dashboard **no hubo ninguna llamada** a Ollama.
- **No se pierde la pregunta.** `pregunta_en_curso` sobrevive: el
  dashboard la muestra como pendiente y, al volver a 💬, se procesa.
- **El trabajo se duplica, y está medido.** Al volver, la pregunta se
  reprocesa **desde el principio** (SQL → stream → reintento de cifras si
  aplica). Lo desperdiciado fue 1 llamada de SQL (~0,9 s de Ollama, c.1) o
  la de SQL más el comienzo del stream (c.2).
- **Criterio: se acepta la duplicación, con aviso visible.** No se
  bloquean los íconos. Motivos:
  1. El costo medido es bajo con el modelo por defecto (≈1-2 s de trabajo
     de Ollama por cambio de vista).
  2. Bloquear el cambio de vista contradice el propio escenario pedido
     (poder ir al dashboard con una pregunta en curso) y dejaría la
     interfaz congelada hasta 30 s más 30 s en el peor caso de timeout.
  3. Que la generación continúe de verdad en segundo plano exigiría sacar
     la llamada al modelo del hilo del script (un hilo propio con estado
     compartido y una UI que consulte periódicamente). Eso cambiaría cómo
     funciona el streaming, que esta fase no puede tocar.

  Por eso los dos avisos dicen explícitamente que la respuesta se cancela y
  se genera de nuevo. Nada ocurre en silencio.

---

## 3. PDF desde esta vista (el caso C de la Fase 6)

En este layout **ya no se puede mandar una pregunta mientras se genera el
PDF**: el `chat_input` solo existe en la vista de chat (lo verifica
`test_iconos_alternan_la_vista_a_todo_el_ancho`: `assert not at.chat_input`
en la vista de dashboard). El caso C se transforma en *"cambiar a 💬
mientras se genera el PDF"*. Medido:

```
 0.27s [..073.446]  >>> CLICK en 'Generar/Actualizar PDF'
 0.33s [..073.499]  STATUS: Generando PDF…
 1.28s [..074.449]  >>> CLICK en 💬 (1 s después, con el PDF generándose)
 4.46s [..077.637]  NEW_SESSION                     ← la vista de chat tarda 3,2 s en arrancar
 5.11s [..078.284]  BOTÓN '💬' type=primary · CHAT_INPUT visible
20.11s [..093.285]  >>> CLICK en 📊 (vuelvo al dashboard)
20.16s              TÍTULO/SUBTÍTULO: 📄 Reporte PDF · DESCARGA: ⬇️ Descargar PDF   ← sin mensaje "Generado: …"
```
```
data/pdf/reporte_instacart.pdf  mtime antes: 1790135517   (PDF anterior)
                                mtime a los ~20 s: 1790135517   (todavía no)
                                mtime al revisar después: 1790178105   (≈31,5 s después del clic)
```
- **El PDF sí se genera.** El hilo abandonado siguió en segundo plano
  (`generar_pdf()` no hace llamadas a Streamlit, así que nada lo detiene) y
  escribió el archivo ≈31,5 s después del clic, del orden de los ~36 s
  medidos en la Fase 4.
- **Se pierde la confirmación.** El *"Generado: … KB en …s"* y el
  incremento de `version_cache` ocurren después de `status.update`, donde
  el hilo abandonado se detiene. Al volver al dashboard, la leyenda de
  vigencia del PDF puede quedar desactualizada hasta el siguiente refresco.
  El botón de descarga siempre sirve el archivo que está en disco.
- **El cambio de vista se retrasa 3,2 s**: la generación del PDF
  (reportlab/matplotlib, uso intensivo de CPU) compite con la nueva
  ejecución.
- En conclusión, es el mismo patrón que la Fase 6 (el archivo se escribe,
  la confirmación se pierde), con otro disparador. Queda en riesgos.

---

## 4. Tests

`tests/test_app_instacart.py` pasa de 11 a **19 tests**. Se extendió el
mismo archivo, sin duplicarlo.

**Tests de la Fase 6 adaptados, no borrados:**
| Antes (Fase 6) | Ahora (Fase 8) | Por qué |
|---|---|---|
| `test_layout_de_dos_columnas_chat_izquierda_dashboard_derecha` (columna 0 = chat, columna 1 = radio + PDF) | `test_layout_vista_alternante_arranca_en_chat_a_todo_el_ancho`: arranca en `vista="chat"`, el chat está en pantalla, **no** hay radio ni botón de PDF, nada del dashboard en la sidebar | Ya no existen las dos columnas; el test comprueba el layout nuevo. |
| `test_dashboard_sigue_funcionando_dentro_de_la_app` usaba `at.columns[1].radio[0]` | Mismo test y mismas aserciones (N3, N9 y N11 sin excepción, métricas, historial intacto); entra al dashboard con el ícono 📊 y usa `at.radio(key="necesidad_sel")` | El panel derecho ya no existe. |

Los otros 9 tests de la Fase 6 pasan **sin cambios**: chat con SQL y
respuesta, rechazo de escritura, timeout, cifra alterada, historial y
contexto, contexto solo con turnos exitosos, streaming en fragmentos, etc.

**Nuevos (8):**
| Test | Qué comprueba |
|---|---|
| `test_iconos_existen_y_el_activo_se_distingue` | `💬`/`📊`; `primary`/`secondary`, y se invierten al cambiar de vista |
| `test_iconos_alternan_la_vista_a_todo_el_ancho` | 📊: el chat y el `chat_input` desaparecen y aparece el dashboard; 💬: al revés |
| `test_fijado_de_la_sidebar_abre_el_dashboard_con_esa_necesidad` | 11 fijados; el clic en N3 pone `vista="dashboard"`, el radio en `N3`, el título `"N3 — Picos de demanda"` y resalta el fijado |
| `test_la_necesidad_elegida_sobrevive_a_pasar_por_el_chat` | N7 → 💬 → 📊 sigue mostrando N7 (cubre el borrado de estado de widgets de Streamlit) |
| `test_historial_persiste_al_ir_al_dashboard_y_volver` | Los mismos 2 mensajes y 2 entradas de historial antes y después |
| `test_sidebar_no_tiene_chats_recientes` | La sidebar tiene exactamente los 11 fijados; ningún texto con "reciente", "historial", "conversaci" ni la pregunta hecha |
| `test_pregunta_pendiente_se_avisa_en_dashboard_y_se_procesa_al_volver` | Con el estado que deja un cambio de vista a mitad de respuesta: aviso en el dashboard, 0 llamadas al modelo ahí; al volver, 1 llamada, respuesta en el historial y `pregunta_en_curso=None` |
| `test_aviso_de_respuesta_en_curso_no_queda_en_pantalla_al_terminar` | "Respondiendo…" no queda en pantalla cuando la respuesta termina |

---

## 5. `dashboard.py` standalone y el resto del proyecto, sin cambios

- `git diff HEAD -- src/ui/dashboard.py` sale vacío. Sus 35 tests
  (`test_dashboard_paneles.py`, `test_dashboard_pdf_boton.py`,
  `test_dashboard_servicio.py`) dan **35 passed**, sin cambios.
- Único archivo de test modificado: `tests/test_app_instacart.py`. Ningún
  otro archivo de `src/` cambió (solo `src/ui/app_instacart.py`).
- `pytest -q`: **309 passed, 2 xfailed** = 303 anteriores + 8 nuevos. Los
  2 xfailed son las mismas limitaciones de qwen2.5-coder:1.5b de la Fase 5.

---

## 6. Desviaciones (con motivo)

1. **Se quitó el `@st.fragment` del dashboard** que había puesto la Fase 6.
   Existía para que el dashboard no interrumpiera al chat cuando ambos
   estaban en pantalla, y eso ya no ocurre. Con el fragmento, elegir una
   necesidad desde el radio no actualizaría el resaltado de la sidebar
   (está fuera del fragmento). No cambia la lógica del dashboard: son las
   mismas funciones `render_controles`/`render_panel`.
2. **`st.chat_input` usa ahora `on_submit`** en vez de leer su valor de
   retorno. Es el mismo efecto (`pregunta_en_curso = pregunta`), pero antes
   de dibujar la barra, para poder mostrar el aviso de respuesta en curso.
   Los 9 tests de chat de la Fase 6 pasan sin cambios.
3. **Se agregaron dos avisos de texto** sobre la pregunta en proceso. El
   prompt los deja a criterio (punto 2); no agregan funcionalidad.
4. **El título corto viene de `TITULO` en `src/analytics/nX_*.py`** porque
   el selector del dashboard no muestra títulos (solo "N1…N11"). Se lee la
   constante sin calcular nada y sin tocar `src/analytics`.
5. **La barra de íconos usa `st.columns([1, 1, 18])`** solo para poner los
   dos botones uno junto al otro. Las vistas no están en columnas.
6. **Se actualizó `README.md`** (fuera de `app_instacart.py`, pero es
   documentación). Describía el layout de dos paneles y la limitación de
   concurrencia de la Fase 6, que ya no aplican. Cambió la sección "Correr
   la app", la limitación de la interfaz (ahora con lo medido en esta fase)
   y se agregó la fila de la Fase 8 al historial. El commit de la Fase 7 se
   anotó como `57d3a86`.

---

## 7. Riesgos

- **Cambiar de vista con una respuesta en curso duplica trabajo** (1-2
  llamadas de Ollama medidas). Con un modelo más lento (14b: ~30 s por
  llamada en la Fase 5) el costo sería mucho mayor. El aviso lo advierte,
  pero no lo impide.
- **Cambiar de vista mientras se genera el PDF**: el archivo se escribe,
  pero se pierden la confirmación y la actualización de la leyenda de
  vigencia, y el cambio de vista se retrasa unos 3 s por competencia de
  CPU.
- **El truco de reasignar `necesidad_sel`** depende de cómo Streamlit
  limpia el estado de los widgets. Si una versión futura cambia ese
  comportamiento, el test `..._sobrevive_a_pasar_por_el_chat` lo detecta.
- **CSS de la sidebar**: el selector
  `section[data-testid="stSidebar"] div.stButton > button` depende de los
  `data-testid` de Streamlit 1.39. Si cambian, los fijados solo pierden la
  alineación a la izquierda; siguen funcionando.
- Siguen vigentes las limitaciones del chat y de la IA documentadas en el
  README (corrección semántica no garantizada, seguimiento que hereda
  errores, etc.).

---

## 8. Estado final

`git status --short` antes del commit:
```
 M README.md
 M src/ui/app_instacart.py
 M tests/test_app_instacart.py
?? docs/FASE8_REPORTE.md
```

`pytest -q`:
```
309 passed, 2 xfailed in 194.13s
```

`pytest -m integration -q`:
```
34 passed, 275 deselected, 2 xfailed in 109.08s
```
