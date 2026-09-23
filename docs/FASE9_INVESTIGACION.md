# Fase 9, paso 0: investigación de la gestión de Ollama

Fecha: 2026-09-23. Base: `57b58e6` (HOTFIX 1). Esta investigación no tocó
el servicio: no hubo ningún start, stop ni restart. Todas las comprobaciones
de permisos se hicieron **sin ejecutar la acción**.

## 1. Cómo está gestionado Ollama

- `systemctl status ollama`: **`ollama.service`** de sistema, `enabled`,
  `active (running)` desde 2026-09-22 20:46, con `Main PID: 1419
  /usr/local/bin/ollama serve`.
- Unidad `/etc/systemd/system/ollama.service`: `User=ollama`,
  `Group=ollama`, `Restart=always`, `RestartSec=3`.
- No existe un servicio de usuario: `systemctl --user status ollama` →
  *"Unit ollama.service could not be found"*.
- Usuario actual: `alexpro186`, en los grupos `alexpro186 wheel kvm ollama`.

## 2. ¿Puede la app (como usuario normal) arrancar o parar el servicio? **NO.**

Evidencia:

| Vía | Comando (no interactivo) | Resultado |
|---|---|---|
| sudo | `sudo -n systemctl status ollama` / `sudo -n -l` | `sudo: a password is required`: **no hay sudo sin contraseña** |
| polkit, la vía de `systemctl start/stop` sin sudo | `pkcheck --action-id org.freedesktop.systemd1.manage-units --process $$` | `polkit.result=auth_admin_keep`, *"Authorization requires authentication"*, exit 2: **exige la contraseña de un administrador** |
| Política de esa acción | `pkaction --verbose --action-id org.freedesktop.systemd1.manage-units` | `implicit active: auth_admin_keep`, `implicit any/inactive: auth_admin` |
| Reglas locales que lo relajen | `grep` de `systemd1` u `ollama` en `/etc/polkit-1/rules.d/` y `/usr/share/polkit-1/rules.d/` | ninguna |
| Enviar una señal al proceso | — | `ollama serve` corre como el usuario `ollama`, no como `alexpro186`. Además `Restart=always` lo relanzaría a los 3 s |

**Conclusión**: la app, corriendo como `alexpro186`, **no puede arrancar
ni detener el servicio real sin que alguien escriba una contraseña de
administrador**. `auth_admin_keep` exige autenticarse la primera vez y
solo mantiene la autorización unos minutos después de hacerlo; aun así,
la primera vez necesita un agente de polkit con alguien delante. **No se
implementan botones de iniciar/detener Ollama.** En su lugar, la UI y el
README documentan que Ollama debe estar corriendo antes (`systemctl status
ollama`).

Si en el futuro se quisiera permitirlo, haría falta **una decisión del
administrador de la máquina**: una regla de polkit para ese usuario y esa
unidad, o una entrada de `sudoers` limitada a `systemctl start|stop
ollama`. Eso cambia la seguridad del sistema y queda fuera del alcance de
la app.

## 3. Liberar un modelo de RAM sin tocar el servicio: **SÍ, con `keep_alive: 0`**

Contra el servidor real (`localhost:11434`):

```
GET  /api/ps                                   → {"models":[]}
POST /api/generate {"model":"qwen2.5-coder:1.5b","prompt":"hola","options":{"num_predict":1}}
                                               → load_duration 2.59 s (carga el modelo)
GET  /api/ps                                   → [{"name":"qwen2.5-coder:1.5b","size":1169980128,
                                                   "size_vram":0,"expires_at":"…11:11:02","context_length":4096}]
     proceso hijo de ollama serve: pid 178346 /usr/local/lib/ollama/llama-server …  RSS = 1,148,292 kB
POST /api/generate {"model":"qwen2.5-coder:1.5b","keep_alive":0}      (sin prompt)
                                               → HTTP 200 {"response":"","done":true,"done_reason":"unload"}
GET  /api/ps                                   → {"models":[]}
     procesos hijos de ollama serve: ninguno (el llama-server terminó)
```

- **Descarga el modelo de verdad**: `/api/ps` queda vacío y **el proceso
  `llama-server` que tenía 1.1 GB de RSS desaparece**.
- **Qué cifra de memoria es real**: el RSS de `ollama serve` (PID 1419)
  apenas cambia (25 MB → 36 MB), porque el modelo vive en el proceso hijo
  `llama-server`. El `size` de `/api/ps` (1,169,980,128 B = 1,116 MiB)
  coincide con el RSS real de ese hijo (1,148,292 kB = 1,121 MiB) con una
  diferencia de menos del 1%. En esta máquina, sin GPU, `size_vram` = 0.
  **La UI mostrará el `size` de `/api/ps`**: es el dato que da la API,
  coincide con lo medido y no requiere leer `/proc` de un proceso de otro
  usuario (aunque aquí sí es legible, `0444`).
- **Casos borde**:
  - con un modelo instalado pero no cargado (`llama3.1:8b`) → HTTP 200,
    `done_reason: "unload"`, sin efecto;
  - con un modelo inexistente → HTTP 404, `{"error":"model 'no-existe:1b' not found"}`.
- **Desde Python**: `ollama-python` 0.3.3 lo soporta tal cual:
  `Client.generate(model=…, prompt='', keep_alive=0)` y `Client.ps()`
  (firmas verificadas con `inspect.signature`).

## 4. Qué se implementa a partir de esto

| Pedido | ¿Se implementa? | Motivo |
|---|---|---|
| Botones Iniciar/Detener Ollama | **No** | La sección 2 muestra que requiere autenticación de administrador |
| Aviso "Ollama debe estar corriendo…" en la UI y el README | Sí | Es la limitación real |
| Estado real de Ollama (responde / no responde) | Sí | Se verifica con la API, sin suponerlo |
| Liberar modelo (`keep_alive: 0`) con estado real (`/api/ps`) | Sí | La sección 3 muestra que funciona |
