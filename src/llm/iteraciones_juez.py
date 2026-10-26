"""Fase 11: iteraciones configurables + juez por IA contra la respuesta
correcta.

Para la pregunta fija de una necesidad (`PREGUNTA` de su módulo en
`src.analytics`), corre el pipeline completo del chat N veces por modelo
(pequeño y grande) y, después de cada corrida, le pide al MISMO modelo que
actúe como juez y compare su respuesta con la de referencia.

- Pipeline: `ri.responder_pregunta_instacart(..., redactar=True)` tal cual
  (esquema -> SQL -> guardas -> un reintento -> redacción -> validación de
  cifras). No es un camino paralelo: solo cambia la temperatura.
- Temperatura de las iteraciones: `TEMPERATURA_ITERACIONES` = 0.5. Con 0.0
  (la del chat) el modelo elige siempre el token más probable y las N
  corridas de la misma pregunta salen prácticamente idénticas: medido con
  1.5b en N3, 3 corridas a 0.0 dieron la MISMA respuesta las 3 veces (y el
  mismo SQL salvo un desempate en ORDER BY); 3 corridas a 0.5 dieron 3
  respuestas distintas (docs/CLASIFICACION_FALLOS.md, Fase 11). Repetir a
  0.0 no mediría nada. El chat de producción y la entrega de la Fase 10 NO
  cambian: no pasan `temperatura` y siguen en 0.0.
- Respuesta de referencia: NO se escribe aquí. Es el resultado del software
  determinista (`data/referencia/{nid}.json`, cargado con
  `servicio.cargar_necesidad`, que lo recalcula si la BD cambió), el mismo
  que validan los oráculos en pandas de las Fases 2A/2B. Al juez se le
  pasan sus `hallazgos` y `valores_clave` (`texto_referencia`).
- Juez: mismo modelo de la iteración, `TEMPERATURA_JUEZ` = 0.0 (criterio
  estable) y `format="json"` de Ollama. Veredicto: "correcta",
  "parcialmente correcta" o "incorrecta" + justificación breve.
  Si el pipeline no produjo respuesta, no se llama al juez: la iteración
  queda "incorrecta" con origen "automatico" y el error como justificación.
  Si el juez falla o devuelve algo ilegible, queda "sin veredicto" (no
  cuenta como correcta).

Fase 23:
- Modo de iteración (`MODOS`): "independiente" (lo de arriba: N intentos
  desde cero, mide estabilidad) o "autocorreccion" (default): desde el
  intento 2, cada intento recibe el SQL, el resultado o error, la
  redacción y el veredicto del intento anterior del mismo modelo
  (`texto_retroalimentacion`); mide si el modelo converge cuando se le
  dice en qué falló.
- Trazabilidad: cada `Iteracion` guarda su etiqueta "Intento k de N ·
  modelo", el SQL rechazado (si hubo reintento), el resultado crudo, la
  primera redacción (aunque se haya descartado), la segunda y la final.
- Ningún intento terminado se pierde: se agrega a la corrida apenas termina
  (la UI guarda la corrida en sesión ANTES de empezar; la CLI reescribe el
  JSON/TXT de `--out` después de cada intento).
- Export legible para pegar en otra conversación: `a_texto`,
  `exportar_texto`, `exportar_json`.

Fase 24 (10 necesidades de una sola consulta; retroalimentación sin fuga):
- La referencia que ve el juez es la de `src.analytics.necesidades`: SQL de
  referencia + su resultado crudo + la redacción (`texto_referencia`). El
  juez ve además el SQL y el resultado del intento, no solo la redacción.
- El juez devuelve DOS textos: `comparacion` (puede citar la referencia; se
  guarda como `justificacion` y NUNCA se pasa al modelo) y
  `diagnostico_enfoque` (qué está mal del enfoque; es lo único del juez que
  recibe el siguiente intento, saneado con `retroalimentacion.sanear`).
- La retroalimentación ya NO incluye las filas del resultado anterior ni la
  redacción anterior: solo el SQL, el error exacto de DuckDB (completo, con
  sus "Candidate bindings") o la forma del resultado (filas y columnas), el
  chequeo de forma determinista, el veredicto y el diagnóstico saneado. Ver
  `texto_retroalimentacion`.
- `coincide_referencia`: comparación determinista resultado-vs-referencia,
  solo informativa (contrasta al juez-LLM); no se pasa al modelo.

Fase 26 (corte al primer acierto): en autocorrección, la cadena de cada
modelo termina en el primer intento con veredicto "correcta"
(`corta_al_acertar`); los intentos que faltan quedan "no ejecutados (ya
convergió)" (`CorridaIteraciones.convergencia`, `no_ejecutadas`), no fallidos.
Seguir iterando a temperatura 0.5 solo arriesgaba perder el acierto (Fase 25:
~13 % de los aciertos de llama se perdían en el intento siguiente). No aplica
al modo independiente, que mide la variabilidad de N muestras. `--sin-corte`
reproduce la Fase 25.

Fase 25 (el chequeo automático es el árbitro, no el juez-LLM):
- En modo autocorrección (`arbitro_por_defecto`), el veredicto de cada
  intento es el del chequeo automático contra la referencia: coincide ->
  "correcta", no -> "incorrecta". Es lo que se muestra, lo que cuenta en el
  resumen y lo que recibe el intento siguiente.
- El juez-LLM ya no decide. (a) Si el intento falló, `diagnosticar` le dice
  que ya está mal y le pide solo el diagnóstico del enfoque (saneado como en
  la Fase 24). (b) Con `medir_juez` (default), se le pide también, aparte, su
  veredicto con el prompt de la Fase 24 (`juez_*`), solo para seguir midiendo
  qué tan seguido se equivoca; no se pasa al modelo.
- Modo independiente: sin cambios (`arbitro="juez"`). `--arbitro juez` en
  autocorrección reproduce la Fase 24 para comparar.

CLI (modelos reales):
  python -m src.llm.iteraciones_juez N3 --iteraciones 5 --modo autocorreccion \\
      --pequeno llama3.1:8b --grande - --timeout 300 --out docs/evidencia/fase24
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from src.analytics.contrato import ResultadoNecesidad
from src.analytics.necesidades import DEFINICIONES, columnas_ordenadas
from src.dashboard import servicio
from src.llm import client
from src.llm import responder_instacart as ri
from src.llm import retroalimentacion as rf
from src.llm import tecnicas
from src.llm.prompts_instacart import VEREDICTOS_JUEZ, prompt_diagnostico_instacart, prompt_juez_instacart

#: Temperatura de las N corridas del pipeline (solo este modo de prueba).
TEMPERATURA_ITERACIONES = 0.5
#: Temperatura del juez: criterio estable aunque la respuesta evaluada no lo sea.
TEMPERATURA_JUEZ = 0.0

ITERACIONES_DEFAULT = 5
ITERACIONES_MIN = 1
ITERACIONES_MAX = 20

#: Modelo pequeño: el default del chat (Fase 5). Modelo grande: el más
#: grande descargado de la misma familia, ya comparado en el benchmark de
#: la Fase 5. Ambos se pueden cambiar en ⚙️ o con --pequeno/--grande.
MODELO_PEQUENO_DEFAULT = client.MODELO_POR_DEFECTO_INSTACART
MODELO_GRANDE_DEFAULT = "qwen2.5-coder:14b"

CORRECTA, PARCIAL, INCORRECTA = VEREDICTOS_JUEZ
SIN_VEREDICTO = "sin veredicto"

#: De dónde salió el veredicto de una iteración.
ORIGEN_JUEZ = "juez"                  # el modelo respondió un veredicto válido
ORIGEN_AUTOMATICO = "automatico"      # el pipeline falló: no hay respuesta que juzgar
ORIGEN_JUEZ_INVALIDO = "juez_invalido"  # el juez respondió algo que no es un veredicto
ORIGEN_JUEZ_ERROR = "juez_error"      # la llamada al juez falló (timeout, Ollama caído)
ORIGEN_CHEQUEO = "chequeo_automatico"  # Fase 25: resultado comparado con la referencia, sin modelo

#: Fase 25: quién decide el veredicto de cada intento.
#: - "chequeo": el chequeo automático contra la referencia
#:   (`rf.coincide_con_referencia`): coincide -> "correcta", no -> "incorrecta".
#:   El juez-LLM solo se llama (a) para MEDIR qué habría dicho (dato de
#:   transparencia, no decide nada ni pasa al modelo) y (b) cuando el intento
#:   ya falló, para escribir el diagnóstico del enfoque (`diagnosticar`).
#: - "juez": el juez-LLM decide (Fases 11-24). Es el de modo "independiente",
#:   que no cambia, y sirve para comparar contra la Fase 24.
ARBITRO_CHEQUEO = "chequeo"
ARBITRO_JUEZ = "juez"
ARBITROS = (ARBITRO_CHEQUEO, ARBITRO_JUEZ)


def arbitro_por_defecto(modo: str) -> str:
    """Autocorrección -> chequeo automático (Fase 25); independiente -> juez (sin cambios)."""
    return ARBITRO_CHEQUEO if modo == MODO_AUTOCORRECCION else ARBITRO_JUEZ

#: Pregunta fija de cada necesidad, tal cual la `pregunta` de su definición.
PREGUNTAS: dict[str, str] = {nid: d.pregunta for nid, d in DEFINICIONES.items()}


def timeout_consulta(necesidad: str) -> float:
    """Fase 29: límite (s) de la guarda de DuckDB para el SQL que el modelo
    genera para `necesidad`: el de su definición o, si no fija uno, el del chat."""
    propio = DEFINICIONES[necesidad].timeout_consulta_s
    return propio if propio is not None else ri.TIMEOUT_S_DEFAULT

#: Filas del resultado de referencia que ve el juez (contexto de 4,096 tokens).
FILAS_REFERENCIA_JUEZ = 10


# ---------------------------------------------------------------------------
# Referencia
# ---------------------------------------------------------------------------

def _fmt(valor: object) -> str:
    if isinstance(valor, bool) or valor is None:
        return str(valor)
    if isinstance(valor, int):
        return f"{valor:,}"
    if isinstance(valor, float):
        return f"{valor:,.0f}" if valor.is_integer() else f"{valor:,.4f}".rstrip("0")
    return json.dumps(valor, ensure_ascii=False) if isinstance(valor, (list, dict)) else str(valor)


def _filas_referencia(r: ResultadoNecesidad) -> list[dict]:
    return next(iter(r.tablas.values()), [])


def texto_referencia(r: ResultadoNecesidad) -> str:
    """Respuesta de referencia que ve el juez (Fase 24): el SQL de
    referencia, su resultado crudo (hasta `FILAS_REFERENCIA_JUEZ` filas), la
    redacción (`hallazgos`) y los `valores_clave`, sin reescribirlos."""
    filas = _filas_referencia(r)
    columnas = columnas_ordenadas(r.id, filas)
    sql = " ".join(" ".join(c.sql.split()) for c in r.consultas)
    lineas = [f"SQL de referencia: {sql}"]
    lineas.append("Resultado de referencia:")
    lineas.append(_tabla_texto(columnas, [[f[c] for c in columnas] for f in filas], len(filas), FILAS_REFERENCIA_JUEZ))
    lineas.append("Respuesta de referencia:")
    lineas += [f"- {h}" for h in r.hallazgos]
    lineas.append("Valores clave:")
    # Las listas (top 10 de nombres) ya están en el resultado: repetirlas solo gasta contexto.
    lineas += [f"- {v.etiqueta}: {_fmt(v.valor)} ({v.unidad})" for v in r.valores_clave if not isinstance(v.valor, list)]
    return "\n".join(lineas)


def referencia_de(necesidad: str) -> str:
    """Texto de referencia de `necesidad`, desde `data/referencia/{nid}.json`."""
    return texto_referencia(servicio.cargar_necesidad(necesidad).resultado)


# ---------------------------------------------------------------------------
# Juez
# ---------------------------------------------------------------------------

@dataclass
class Veredicto:
    veredicto: str
    #: Fase 24: la "comparacion" del juez contra la referencia (puede citar
    #: valores de la referencia; nunca se pasa al modelo).
    justificacion: str
    origen: str
    #: Texto crudo que devolvió el juez (None si no se le llamó).
    crudo: str | None = None
    #: Fase 24: "diagnostico_enfoque" del juez, crudo (antes de sanearlo).
    diagnostico: str | None = None


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def normalizar_veredicto(texto: object) -> str | None:
    """Texto del campo "veredicto" -> uno de `VEREDICTOS_JUEZ`, o None.
    Acepta mayúsculas, acentos, guion bajo ("parcialmente_correcta") y la
    forma corta "parcial". "incorrect..." se revisa antes que "correct..."."""
    if not isinstance(texto, str):
        return None
    t = " ".join(_sin_acentos(texto).lower().replace("_", " ").replace("-", " ").split())
    if t.startswith("parcial"):
        return PARCIAL
    if t.startswith("incorrect"):
        return INCORRECTA
    if t.startswith("correct"):
        return CORRECTA
    return None


def parsear_veredicto(crudo: str) -> Veredicto:
    """Salida del juez -> `Veredicto`. Si no es un JSON con un veredicto
    reconocible, devuelve "sin veredicto" con el texto crudo como evidencia
    (nunca adivina)."""
    datos = None
    try:
        datos = json.loads(crudo)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", crudo or "", re.DOTALL)
        if m:
            try:
                datos = json.loads(m.group(0))
            except json.JSONDecodeError:
                datos = None
    veredicto = normalizar_veredicto(datos.get("veredicto")) if isinstance(datos, dict) else None
    if veredicto is None:
        return Veredicto(SIN_VEREDICTO, f"El juez no devolvió un veredicto válido: {crudo!r}",
                         ORIGEN_JUEZ_INVALIDO, crudo)
    justificacion = (datos.get("comparacion") or datos.get("comparación") or datos.get("justificacion")
                     or datos.get("justificación") or "")
    diagnostico = datos.get("diagnostico_enfoque") or datos.get("diagnóstico_enfoque")
    return Veredicto(veredicto, str(justificacion).strip(), ORIGEN_JUEZ, crudo,
                     str(diagnostico).strip() if diagnostico else None)


def juzgar(
    pregunta: str, referencia: str, respuesta: str, *, modelo: str, timeout_s: float,
    sql: str | None = None, resultado: str | None = None,
) -> Veredicto:
    """Pide al `modelo` el veredicto (temperatura 0.0, salida JSON). Fase 24:
    `sql`/`resultado` del intento, para que diagnostique el enfoque."""
    sistema, usuario = prompt_juez_instacart(pregunta, referencia, respuesta, sql_generado=sql,
                                             resultado_generado=resultado)
    try:
        crudo = client.chat_con_modelo(
            modelo, sistema, usuario, temperatura=TEMPERATURA_JUEZ, timeout_s=timeout_s, formato_json=True,
        )
    except client.LlmNoDisponible as exc:
        return Veredicto(SIN_VEREDICTO, f"El juez no respondió: {exc}", ORIGEN_JUEZ_ERROR)
    return parsear_veredicto(crudo)


@dataclass
class Diagnostico:
    """Fase 25: lo que escribe el juez cuando el chequeo automático ya decidió
    que el intento falló. `diagnostico` es crudo (se sanea después)."""
    comparacion: str
    diagnostico: str | None
    origen: str
    crudo: str | None = None


def parsear_diagnostico(crudo: str) -> Diagnostico:
    datos = None
    try:
        datos = json.loads(crudo)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", crudo or "", re.DOTALL)
        if m:
            try:
                datos = json.loads(m.group(0))
            except json.JSONDecodeError:
                datos = None
    diagnostico = datos.get("diagnostico_enfoque") or datos.get("diagnóstico_enfoque") if isinstance(datos, dict) else None
    if not diagnostico:
        return Diagnostico(f"El juez no devolvió un diagnóstico válido: {crudo!r}", None, ORIGEN_JUEZ_INVALIDO, crudo)
    comparacion = datos.get("comparacion") or datos.get("comparación") or ""
    return Diagnostico(str(comparacion).strip(), str(diagnostico).strip(), ORIGEN_JUEZ, crudo)


def diagnosticar(
    pregunta: str, referencia: str, respuesta: str, *, modelo: str, timeout_s: float,
    sql: str | None, resultado: str | None, detalle_chequeo: str,
) -> Diagnostico:
    """Fase 25: pide al `modelo` que explique POR QUÉ falló un intento que el
    chequeo automático ya marcó como incorrecto (temperatura 0.0, JSON). No se
    le pide veredicto."""
    sistema, usuario = prompt_diagnostico_instacart(pregunta, referencia, respuesta, sql_generado=sql,
                                                    resultado_generado=resultado, detalle_chequeo=detalle_chequeo)
    try:
        crudo = client.chat_con_modelo(
            modelo, sistema, usuario, temperatura=TEMPERATURA_JUEZ, timeout_s=timeout_s, formato_json=True,
        )
    except client.LlmNoDisponible as exc:
        return Diagnostico(f"El juez no respondió: {exc}", None, ORIGEN_JUEZ_ERROR)
    return parsear_diagnostico(crudo)


# ---------------------------------------------------------------------------
# Iteraciones
# ---------------------------------------------------------------------------

#: Modos de iteración (Fase 23).
#: - "independiente" (Fase 11): N intentos desde cero de la misma pregunta;
#:   mide estabilidad/variabilidad del modelo.
#: - "autocorreccion": a partir del intento 2, cada intento recibe lo que
#:   pasó en el anterior (SQL, resultado o error, redacción y veredicto del
#:   juez); mide si el modelo converge a la respuesta correcta cuando se le
#:   dice en qué falló. Cada modelo tiene su propia cadena.
MODO_INDEPENDIENTE = "independiente"
MODO_AUTOCORRECCION = "autocorreccion"
MODOS = (MODO_INDEPENDIENTE, MODO_AUTOCORRECCION)
MODO_DEFAULT = MODO_AUTOCORRECCION

#: Filas del resultado crudo que se guardan por intento (panel y export).
FILAS_REGISTRADAS_MAX = 50
#: Filas del resultado anterior que se le muestran al modelo en la
#: retroalimentación (el contexto de Ollama es de 4,096 tokens).
FILAS_RETROALIMENTACION_MAX = 10

ESTADO_EN_CURSO = "en curso"
ESTADO_COMPLETA = "completa"


def _valor_json(v: object) -> object:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


@dataclass
class Iteracion:
    modelo: str
    numero: int
    sql: str | None
    respuesta: str | None
    error: str | None
    #: Intentos de SQL del pipeline (1, o 2 si hubo reintento).
    intentos_sql: int
    #: "modelo" / "reintento" / "plantilla" (validación de cifras), o None.
    origen_respuesta: str | None
    veredicto: str
    justificacion: str
    origen_veredicto: str
    crudo_juez: str | None
    duracion_pipeline_s: float
    duracion_juez_s: float
    #: Fase 23: N de la corrida, para etiquetar "Intento k de N".
    total: int = 0
    #: Fase 23 (trazabilidad): primer SQL rechazado y su motivo, si hubo reintento.
    sql_rechazado: str | None = None
    motivo_reintento: str | None = None
    #: Resultado crudo de la consulta: columnas, primeras filas y total de filas.
    columnas: list[str] | None = None
    filas: list[list] | None = None
    n_filas: int | None = None
    #: Redacciones tal cual las devolvió el modelo (aunque se descartaran).
    primera_redaccion: str | None = None
    segunda_redaccion: str | None = None
    cifras_rechazadas: list[str] = field(default_factory=list)
    #: Modo autocorrección: texto exacto del intento anterior que recibió
    #: este intento (None en el intento 1 y en modo independiente).
    retroalimentacion: str | None = None
    #: Fase 24: "diagnostico_enfoque" del juez tal cual lo devolvió...
    diagnostico: str | None = None
    #: ...y saneado (sin oraciones que citen la referencia): lo que recibe el
    #: intento siguiente. `oraciones_omitidas`: cuántas se quitaron.
    diagnostico_retro: str | None = None
    oraciones_omitidas: int = 0
    #: Pistas deterministas sobre la forma del resultado (`rf.chequeo_forma`).
    chequeo_forma: list[str] = field(default_factory=list)
    #: Comparación determinista con la referencia (`rf.coincide_con_referencia`).
    #: Solo informativa: no se pasa al modelo.
    coincide_referencia: bool | None = None
    detalle_coincidencia: str | None = None
    #: Fase 25: quién decidió `veredicto` ("chequeo" o "juez"). Con "chequeo",
    #: `veredicto`/`justificacion`/`origen_veredicto` son los del chequeo
    #: automático, `diagnostico` y `crudo_juez`/`duracion_juez_s` son los de la
    #: llamada de diagnóstico (solo si falló) y `diagnostico_comparacion` es la
    #: comparación que escribió el juez en esa llamada.
    arbitro: str = ARBITRO_JUEZ
    diagnostico_comparacion: str | None = None
    #: Fase 25: lo que el juez-LLM opinaba del intento, solo para medir qué tan
    #: seguido se equivoca. NO decide nada ni se pasa al modelo. Con árbitro
    #: "juez" es su propio veredicto (el que sí decidió).
    juez_veredicto: str | None = None
    juez_justificacion: str | None = None
    juez_origen: str | None = None
    juez_crudo: str | None = None
    duracion_juez_medicion_s: float = 0.0
    #: Fase 30: técnica de contexto del intento, ejemplos few-shot inyectados
    #: [(id, similitud)] y el texto exacto del bloque (None en base).
    tecnica: str = tecnicas.BASE
    ejemplos_usados: list = field(default_factory=list)
    bloque_ejemplos: str | None = None
    #: Fase 31 (solo exponen datos): segundos por fase del pipeline
    #: (`ResultadoInstacart.tiempos`); el «por qué» del veredicto PARA EL
    #: USUARIO (`rf.explicacion_usuario`, puede citar la referencia y nunca va
    #: al prompt); y `pista_modelo`, el texto EXACTO que recibió el intento
    #: siguiente (`texto_retroalimentacion`, saneado). None si no hubo siguiente.
    tiempos: dict = field(default_factory=dict)
    explicacion_usuario: str | None = None
    pista_modelo: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def duracion_juez_total_s(self) -> float:
        """Fase 30: todo lo que tardó el juez en este intento (diagnóstico o
        veredicto, más la opinión de medición)."""
        return round(self.duracion_juez_s + self.duracion_juez_medicion_s, 2)

    @property
    def duracion_total_s(self) -> float:
        """Fase 30: lo que tardó el intento completo (pipeline + juez)."""
        return round(self.duracion_pipeline_s + self.duracion_juez_total_s, 2)

    @property
    def texto_ejemplos(self) -> str:
        """«N5-neg-1 (sim 0.62), ...», o «ninguno» si no se inyectó nada."""
        return ", ".join(f"{i} (sim {s:.2f})" for i, s in self.ejemplos_usados) or "ninguno"

    @property
    def etiqueta(self) -> str:
        """"Intento k de N · modelo": el identificador que se muestra en el
        panel y en el export, para saber de qué intento sale cada cifra."""
        return f"Intento {self.numero} de {self.total} · {self.modelo}"


@dataclass
class ResumenModelo:
    modelo: str
    n: int
    correctas: int
    parciales: int
    incorrectas: int
    sin_veredicto: int
    #: Fase 24: intentos cuyo resultado coincide con la referencia (chequeo determinista).
    coincidencias: int = 0
    #: Fase 24: primer intento con veredicto "correcta" (None si ninguno):
    #: en autocorrección, el intento en el que converge.
    primer_correcto: int | None = None
    primera_coincidencia: int | None = None
    #: Fase 25 (transparencia del juez-LLM): intentos en que el juez opinó,
    #: cuántos dio por "correcta", y en cuántos su opinión ("correcta" o no)
    #: coincidió con el chequeo automático. Falsos negativos: el chequeo dice
    #: que coincide y el juez no lo da por correcto; falsos positivos: al revés.
    juez_opiniones: int = 0
    juez_correctas: int = 0
    juez_acuerdos: int = 0
    juez_falsos_negativos: int = 0
    juez_falsos_positivos: int = 0
    #: Fase 26 (corte al primer acierto): intentos que no se ejecutaron porque
    #: el modelo ya había convergido, y el veredicto del ÚLTIMO intento
    #: ejecutado (el estado en que termina la cadena; None si no hay intentos).
    no_ejecutadas: int = 0
    estado_final: str | None = None

    @property
    def pct_correctas(self) -> float:
        """Sobre los intentos EJECUTADOS. Con corte al primer acierto hay a lo
        sumo uno correcto por modelo: ahí lo que importa es `estado_final`."""
        return 100.0 * self.correctas / self.n if self.n else 0.0

    @property
    def pct_acercamiento(self) -> float:
        """Fase 30: % de intentos ejecutados que cumplieron o se acercaron
        (veredicto «correcta» o «parcial»), solo a partir de los veredictos."""
        return 100.0 * (self.correctas + self.parciales) / self.n if self.n else 0.0


#: Fase 30: orden de los veredictos para decir si un intento mejoró o empeoró.
RANGO_VEREDICTO = {INCORRECTA: 0, PARCIAL: 1, CORRECTA: 2}
MEJORO, EMPEORO, IGUAL = "mejoró", "empeoró", "igual"


def tendencia(anterior: str | None, actual: str) -> str | None:
    """Cambio del veredicto respecto al intento anterior del mismo modelo
    (None en el primero o si alguno no tiene veredicto)."""
    if anterior not in RANGO_VEREDICTO or actual not in RANGO_VEREDICTO:
        return None
    d = RANGO_VEREDICTO[actual] - RANGO_VEREDICTO[anterior]
    return MEJORO if d > 0 else EMPEORO if d < 0 else IGUAL


@dataclass
class CorridaIteraciones:
    necesidad: str
    pregunta: str
    referencia: str
    modelos: list[str]
    n_iteraciones: int
    temperatura: float = TEMPERATURA_ITERACIONES
    temperatura_juez: float = TEMPERATURA_JUEZ
    timestamp: str = ""
    iteraciones: list[Iteracion] = field(default_factory=list)
    modo: str = MODO_INDEPENDIENTE
    #: "en curso" mientras corre; "completa" cuando terminaron TODAS. Una
    #: corrida que se quedó "en curso" fue interrumpida (y conserva los
    #: intentos que alcanzaron a terminar).
    estado: str = ESTADO_EN_CURSO
    #: Fase 24: SQL y filas de la consulta de referencia (para el chequeo
    #: automático y para saber qué valores no pueden fugarse).
    referencia_sql: str = ""
    referencia_filas: list[dict] = field(default_factory=list)
    #: Fase 25: quién decide el veredicto (`ARBITROS`) y si se mide al juez-LLM.
    arbitro: str = ARBITRO_JUEZ
    medir_juez: bool = True
    #: Fase 26: en autocorrección, la cadena de cada modelo se corta en el
    #: primer intento con veredicto "correcta"; `convergencia[modelo]` es ese
    #: intento y los siguientes quedan "no ejecutados (ya convergió)".
    corta_al_acertar: bool = False
    convergencia: dict[str, int] = field(default_factory=dict)
    #: Fase 30: técnica de contexto de todos los intentos de la corrida.
    tecnica: str = tecnicas.BASE

    @property
    def valores_protegidos(self) -> rf.ValoresProtegidos:
        return rf.valores_protegidos(self.pregunta, self.referencia_filas)

    @property
    def total_esperado(self) -> int:
        return self.n_iteraciones * len(self.modelos)

    def no_ejecutadas(self, modelo: str) -> list[int]:
        """Fase 26: intentos de `modelo` que no se corrieron porque ya había
        convergido (vacío si no convergió o si no hay corte)."""
        k = self.convergencia.get(modelo)
        return [] if k is None else list(range(k + 1, self.n_iteraciones + 1))

    @property
    def total_no_ejecutadas(self) -> int:
        return sum(len(self.no_ejecutadas(m)) for m in self.modelos)

    @property
    def completa(self) -> bool:
        """Terminaron todos los intentos esperados: ejecutados o, con corte,
        no ejecutados porque el modelo ya convergió. Una corrida interrumpida
        (Fase 23) se queda "en curso"."""
        return (self.estado == ESTADO_COMPLETA
                and len(self.iteraciones) + self.total_no_ejecutadas == self.total_esperado)

    def de_modelo(self, modelo: str) -> list[Iteracion]:
        return [it for it in self.iteraciones if it.modelo == modelo]

    def resumen(self) -> dict[str, ResumenModelo]:
        """% de iteraciones correctas por modelo. El denominador son TODAS
        las iteraciones del modelo (las fallidas y las "sin veredicto"
        cuentan como no correctas)."""
        out = {}
        for modelo in self.modelos:
            its = self.de_modelo(modelo)
            cuenta = {v: sum(1 for it in its if it.veredicto == v) for v in (*VEREDICTOS_JUEZ, SIN_VEREDICTO)}
            opinados = [it for it in its if it.juez_veredicto is not None and it.coincide_referencia is not None]
            out[modelo] = ResumenModelo(
                modelo, len(its), cuenta[CORRECTA], cuenta[PARCIAL], cuenta[INCORRECTA], cuenta[SIN_VEREDICTO],
                coincidencias=sum(1 for it in its if it.coincide_referencia),
                primer_correcto=next((it.numero for it in its if it.veredicto == CORRECTA), None),
                primera_coincidencia=next((it.numero for it in its if it.coincide_referencia), None),
                juez_opiniones=len(opinados),
                juez_correctas=sum(1 for it in opinados if it.juez_veredicto == CORRECTA),
                juez_acuerdos=sum(1 for it in opinados if (it.juez_veredicto == CORRECTA) == it.coincide_referencia),
                juez_falsos_negativos=sum(1 for it in opinados
                                          if it.coincide_referencia and it.juez_veredicto != CORRECTA),
                juez_falsos_positivos=sum(1 for it in opinados
                                          if not it.coincide_referencia and it.juez_veredicto == CORRECTA),
                no_ejecutadas=len(self.no_ejecutadas(modelo)),
                estado_final=its[-1].veredicto if its else None,
            )
        return out

    def tabla(self) -> list[dict]:
        """Fase 30: una fila por intento ejecutado, en orden: qué respondió,
        el veredicto, si mejoró o empeoró respecto a su intento anterior, la
        pista del juez que pasó al siguiente intento y cuánto tardaron."""
        filas = []
        for modelo in self.modelos:
            anterior = None
            for it in self.de_modelo(modelo):
                filas.append({
                    "intento": it.numero, "modelo": modelo, "tecnica": it.tecnica,
                    "ejemplos": it.texto_ejemplos, "veredicto": it.veredicto,
                    "cambio": tendencia(anterior, it.veredicto),
                    "respuesta": it.respuesta if it.ok else f"(sin respuesta) {it.error}",
                    "pista_del_juez": it.diagnostico_retro,
                    "juez_s": it.duracion_juez_total_s, "iteracion_s": it.duracion_total_s,
                })
                anterior = it.veredicto
        return filas

    def a_dict(self) -> dict:
        return {
            "necesidad": self.necesidad,
            "pregunta": self.pregunta,
            "referencia": self.referencia,
            "referencia_sql": self.referencia_sql,
            "referencia_filas": self.referencia_filas,
            "modo": self.modo,
            "arbitro": self.arbitro,
            "medir_juez": self.medir_juez,
            "corta_al_acertar": self.corta_al_acertar,
            "convergencia": self.convergencia,
            "tecnica": self.tecnica,
            "modelos": self.modelos,
            "n_iteraciones": self.n_iteraciones,
            "estado": self.estado,
            "iteraciones_registradas": len(self.iteraciones),
            "iteraciones_esperadas": self.total_esperado,
            "temperatura": self.temperatura,
            "temperatura_juez": self.temperatura_juez,
            "timestamp": self.timestamp,
            "resumen": {m: {**asdict(r), "pct_correctas": r.pct_correctas, "pct_acercamiento": r.pct_acercamiento}
                        for m, r in self.resumen().items()},
            "tabla_iteraciones": self.tabla(),
            "iteraciones": [{"etiqueta": it.etiqueta, **asdict(it), "duracion_juez_total_s": it.duracion_juez_total_s,
                             "duracion_total_s": it.duracion_total_s} for it in self.iteraciones],
            "no_ejecutadas": [
                {"modelo": m, "numero": k, "etiqueta": f"Intento {k} de {self.n_iteraciones} · {m}",
                 "estado": texto_no_ejecutada(self.convergencia[m])}
                for m in self.modelos for k in self.no_ejecutadas(m)
            ],
        }


@dataclass
class Cierre:
    """Fase 31: cómo terminó la cadena de un modelo."""
    aceptada_en: int | None
    texto: str
    explicacion: str
    corte: bool
    tiempo_modelo_s: float
    tiempo_juez_s: float


def cierre_de(corrida: CorridaIteraciones, modelo: str) -> Cierre:
    """«Aceptada en el intento k» o «rechazada tras N intentos», con el por qué
    (de `explicacion_usuario`), la evolución, si se cortó y los totales."""
    its = corrida.de_modelo(modelo)
    tiempo_modelo = round(sum(it.duracion_pipeline_s for it in its), 1)
    tiempo_juez = round(sum(it.duracion_juez_total_s for it in its), 1)
    secuencia = " → ".join(f"{it.numero}:{it.veredicto}" for it in its) or "sin intentos"
    aceptada = next((it for it in its if it.veredicto == CORRECTA), None)
    corte = bool(corrida.no_ejecutadas(modelo))
    if aceptada is not None:
        texto = f"Resultado final: aceptada en el intento {aceptada.numero}"
        explicacion = aceptada.explicacion_usuario or aceptada.justificacion
        if corte:
            texto += (f" (se cortó la iteración: los intentos {aceptada.numero + 1}-{corrida.n_iteraciones} no se "
                      "ejecutaron)")
    else:
        texto = f"Resultado final: rechazada tras {len(its)} intento(s)"
        # El «mejor» intento: el último que llegó a ejecutar; si ninguno, el último.
        mejor = next((it for it in reversed(its) if it.ok), its[-1] if its else None)
        motivo = "" if mejor is None else (mejor.explicacion_usuario or mejor.justificacion or mejor.error or "")
        explicacion = (f"Mejor intento: el {mejor.numero} — {motivo}" if mejor else "No hubo intentos.")
    explicacion += f" Evolución: {secuencia}."
    return Cierre(aceptada.numero if aceptada else None, texto, explicacion, corte, tiempo_modelo, tiempo_juez)


def texto_no_ejecutada(convergio_en: int) -> str:
    return f"no ejecutada (ya convergió en el intento {convergio_en})"


def _tabla_texto(columnas: list[str] | None, filas: list[list] | None, n_filas: int | None, maximo: int) -> str:
    if not columnas:
        return "(sin resultado)"
    if not filas:
        return "(0 filas)"
    lineas = [" | ".join(map(str, columnas))]
    lineas += [" | ".join("" if v is None else str(v) for v in fila) for fila in filas[:maximo]]
    n = n_filas if n_filas is not None else len(filas)
    if n > maximo:
        lineas.append(f"... ({n - maximo} filas más; {n} en total)")
    return "\n".join(lineas)


def diagnostico_para_retro(diagnostico: str | None, vp: rf.ValoresProtegidos) -> tuple[str, int]:
    """El diagnóstico del juez, sin las oraciones que citan la referencia."""
    return rf.sanear(diagnostico, vp)


def texto_retroalimentacion(it: Iteracion, vp: rf.ValoresProtegidos | None = None) -> str:
    """Lo que el intento siguiente recibe del intento `it` en modo
    autocorrección. Se agrega al mensaje de usuario de la llamada de SQL y de
    la primera redacción (`prompts_instacart._con_retroalimentacion`).

    Fase 24 — qué se pasa y qué NO:
    - SÍ: el SQL que generó (y el rechazado con su motivo, si hubo reintento);
      si falló, el error TAL CUAL (el de DuckDB trae "Candidate bindings" con
      los nombres de columna correctos); si ejecutó, la FORMA del resultado
      (cuántas filas, qué columnas); el chequeo de forma determinista; el
      veredicto; y el diagnóstico del enfoque del juez, saneado con `vp`.
    - NO: la "comparacion" del juez (cita la referencia), las filas del
      resultado anterior ni la redacción anterior. Las filas y la redacción
      son del propio modelo, pero (1) si ya coincidían con la referencia,
      repetirlas es darle la respuesta, y enmascarar solo las que coinciden
      le diría cuáles son las correctas; (2) en la Fase 23 el 1.5b copió
      literalmente su redacción anterior en vez de corregirse.

    Fase 25 (árbitro "chequeo"): el veredicto es el del chequeo automático
    (sin el detalle de qué coincidió, que diría qué parte del resultado ya era
    la correcta). Si fue correcto no hay diagnóstico: solo "conserva el
    enfoque"; si falló, va el diagnóstico saneado de `diagnosticar`."""
    chequeo = it.arbitro == ARBITRO_CHEQUEO
    lineas = [
        f"INTENTO ANTERIOR DE ESTA MISMA PREGUNTA (intento {it.numero} de {it.total}). "
        + ("Un chequeo automático comparó su resultado con el de la respuesta correcta, que no se te muestra."
           if chequeo else "Un evaluador lo comparó con la respuesta correcta, que no se te muestra."),
    ]
    if it.sql_rechazado:
        lineas.append(f"Tu primer SQL fue rechazado: {' '.join(it.sql_rechazado.split())}")
        lineas.append(f"Motivo del rechazo (mensaje exacto): {it.motivo_reintento}")
    lineas.append(f"SQL que generaste: {' '.join(it.sql.split()) if it.sql else '(no se generó SQL válido)'}")
    if it.error:
        lineas.append(f"Error (mensaje exacto): {it.error}")
    else:
        columnas = ", ".join(it.columnas or []) or "(ninguna)"
        n = it.n_filas if it.n_filas is not None else 0
        lineas.append(f"Tu consulta se ejecutó y devolvió {rf.texto_n_filas(n)} fila(s) con las columnas: {columnas}.")
    if it.chequeo_forma:
        lineas.append("Chequeo automático de forma: " + " ".join(it.chequeo_forma))
    if chequeo:
        lineas.append(f"Veredicto (chequeo automático contra la respuesta correcta): {it.veredicto.upper()}.")
        if it.veredicto == CORRECTA:
            lineas.append("Tu resultado coincide con la respuesta correcta: conserva el enfoque.")
            return "\n".join(lineas)
    else:
        lineas.append(f"Veredicto del evaluador: {it.veredicto.upper()}.")
    if it.diagnostico_retro is not None:
        diagnostico = it.diagnostico_retro
    elif vp is not None:
        diagnostico, _ = diagnostico_para_retro(it.diagnostico, vp)
    else:
        diagnostico = ""
    if diagnostico:
        lineas.append(f"Qué está mal en el enfoque, según el evaluador: {diagnostico}")
    return "\n".join(lineas)


def correr_iteracion(
    necesidad: str,
    referencia: str,
    modelo: str,
    numero: int,
    *,
    total: int = 0,
    retroalimentacion: str | None = None,
    timeout_sql_llm_s: float = ri.TIMEOUT_LLM_SQL_S,
    timeout_respuesta_llm_s: float = ri.TIMEOUT_LLM_RESPUESTA_S,
    timeout_juez_s: float | None = None,
    db_path: Path | None = None,
    referencia_filas: list[dict] | None = None,
    arbitro: str = ARBITRO_JUEZ,
    medir_juez: bool = True,
    tecnica: str = tecnicas.BASE,
) -> Iteracion:
    """Una iteración: pipeline completo (temperatura 0.5) + veredicto.
    `retroalimentacion`: intento anterior (modo autocorrección).
    `referencia_filas`: resultado de la consulta de referencia (Fase 24:
    chequeo automático y valores que no pueden pasar al siguiente intento).

    `arbitro` (Fase 25):
    - "juez": el juez-LLM da veredicto + comparación + diagnóstico en una sola
      llamada (Fases 11-24, sin cambios).
    - "chequeo": el veredicto es el del chequeo automático. Con `medir_juez`,
      se llama además al juez de la Fase 24 solo para registrar su opinión
      (`juez_*`). Si el chequeo dice que falló, se llama a `diagnosticar` para
      el diagnóstico del enfoque; si acertó, no hay diagnóstico.
    Si el pipeline no produjo respuesta, no se llama al juez en ningún caso:
    el error exacto ya es la retroalimentación."""
    if arbitro not in ARBITROS:
        raise ValueError(f"arbitro debe ser uno de {ARBITROS}, no {arbitro!r}")
    pregunta = PREGUNTAS[necesidad]
    t0 = time.perf_counter()
    res = ri.responder_pregunta_instacart(
        pregunta,
        db_path=db_path if db_path is not None else ri.DB_PATH_DEFAULT,
        redactar=True,
        modelo=modelo,
        timeout_sql_llm_s=timeout_sql_llm_s,
        timeout_respuesta_llm_s=timeout_respuesta_llm_s,
        temperatura=TEMPERATURA_ITERACIONES,
        retroalimentacion=retroalimentacion,
        timeout_consulta_s=timeout_consulta(necesidad),
        tecnica=tecnica,
    )
    t1 = time.perf_counter()
    referencia_filas = referencia_filas or []
    if referencia_filas:
        coincide, detalle = rf.coincide_con_referencia(necesidad, referencia_filas, res.filas)
    elif arbitro == ARBITRO_CHEQUEO:
        coincide, detalle = False, "no hay filas de referencia contra las que comparar"
    else:
        coincide, detalle = None, None
    timeout_juez = timeout_juez_s if timeout_juez_s is not None else timeout_respuesta_llm_s
    resultado_txt = _tabla_texto(res.columnas, res.filas, len(res.filas or []), FILAS_RETROALIMENTACION_MAX)
    medicion: Veredicto | None = None
    diag: Diagnostico | None = None
    duracion_medicion = 0.0
    if not res.ok:
        v = Veredicto(INCORRECTA, f"El pipeline no produjo respuesta: {res.error}", ORIGEN_AUTOMATICO)
    elif arbitro == ARBITRO_JUEZ:
        v = juzgar(pregunta, referencia, res.respuesta or "", modelo=modelo, timeout_s=timeout_juez,
                   sql=res.sql, resultado=resultado_txt)
        medicion = v
    else:
        v = Veredicto(CORRECTA if coincide else INCORRECTA, detalle or "", ORIGEN_CHEQUEO)
        if medir_juez:
            tm = time.perf_counter()
            medicion = juzgar(pregunta, referencia, res.respuesta or "", modelo=modelo, timeout_s=timeout_juez,
                              sql=res.sql, resultado=resultado_txt)
            duracion_medicion = time.perf_counter() - tm
        if not coincide:
            diag = diagnosticar(pregunta, referencia, res.respuesta or "", modelo=modelo, timeout_s=timeout_juez,
                                sql=res.sql, resultado=resultado_txt, detalle_chequeo=detalle or "")
    t2 = time.perf_counter()
    filas = None if res.filas is None else [
        [_valor_json(x) for x in fila] for fila in res.filas[:FILAS_REGISTRADAS_MAX]
    ]
    diagnostico = diag.diagnostico if diag is not None else (v.diagnostico if arbitro == ARBITRO_JUEZ else None)
    diagnostico_retro, omitidas = diagnostico_para_retro(diagnostico, rf.valores_protegidos(pregunta, referencia_filas))
    return Iteracion(
        modelo=modelo, numero=numero, sql=res.sql, respuesta=res.respuesta, error=res.error,
        intentos_sql=res.intentos, origen_respuesta=res.origen_respuesta,
        veredicto=v.veredicto, justificacion=v.justificacion, origen_veredicto=v.origen,
        crudo_juez=diag.crudo if diag is not None else (v.crudo if arbitro == ARBITRO_JUEZ else None),
        duracion_pipeline_s=round(t1 - t0, 2), duracion_juez_s=round(t2 - t1 - duracion_medicion, 2),
        arbitro=arbitro, diagnostico_comparacion=diag.comparacion if diag is not None else None,
        juez_veredicto=medicion.veredicto if medicion else None,
        juez_justificacion=medicion.justificacion if medicion else None,
        juez_origen=medicion.origen if medicion else None,
        juez_crudo=medicion.crudo if medicion else None,
        duracion_juez_medicion_s=round(duracion_medicion, 2),
        total=total, sql_rechazado=res.sql_rechazado, motivo_reintento=res.motivo_reintento,
        columnas=res.columnas, filas=filas, n_filas=None if res.filas is None else len(res.filas),
        primera_redaccion=res.primera_redaccion, segunda_redaccion=res.segunda_redaccion,
        cifras_rechazadas=list(res.cifras_rechazadas), retroalimentacion=retroalimentacion,
        diagnostico=diagnostico, diagnostico_retro=diagnostico_retro, oraciones_omitidas=omitidas,
        chequeo_forma=rf.chequeo_error(res.error) + rf.chequeo_forma(
            necesidad, res.columnas, res.filas, None if res.filas is None else len(res.filas)),
        coincide_referencia=coincide, detalle_coincidencia=detalle,
        tecnica=res.tecnica, ejemplos_usados=[list(u) for u in res.ejemplos_usados],
        bloque_ejemplos=res.bloque_ejemplos, tiempos=dict(res.tiempos),
        explicacion_usuario=(rf.explicacion_usuario(necesidad, referencia_filas, res.columnas, res.filas, coincide,
                                                    detalle) if referencia_filas else None),
    )


def nueva_corrida(
    necesidad: str, *, modelos: Sequence[str], n: int = ITERACIONES_DEFAULT, modo: str = MODO_DEFAULT,
    arbitro: str | None = None, medir_juez: bool = True, corta_al_acertar: bool | None = None,
    tecnica: str | None = None,
) -> CorridaIteraciones:
    """Corrida vacía ("en curso"), con la referencia ya cargada. Quien la
    corre (`correr_iteraciones(corrida=...)`) le agrega cada intento apenas
    termina, así que guardarla ANTES de empezar (la UI la pone en
    `st.session_state`) conserva todo lo terminado si se interrumpe.
    `arbitro` None -> `arbitro_por_defecto(modo)` (Fase 25).
    `corta_al_acertar` None -> sí en autocorrección (Fase 26). En modo
    independiente no se permite: ese modo mide la variabilidad de N muestras
    independientes, y cortar en el primer acierto la arruinaría (y ahí un
    intento extra no puede "perder" un acierto anterior).
    `tecnica` (Fase 30) None -> `tecnicas.tecnica_por_defecto()`; una
    pendiente lanza `tecnicas.TecnicaPendiente`."""
    tecnica = tecnicas.validar_tecnica(tecnica) if tecnica else tecnicas.tecnica_por_defecto()
    arbitro = arbitro if arbitro is not None else arbitro_por_defecto(modo)
    corta_al_acertar = (modo == MODO_AUTOCORRECCION) if corta_al_acertar is None else corta_al_acertar
    if corta_al_acertar and modo != MODO_AUTOCORRECCION:
        raise ValueError("el corte al primer acierto solo aplica al modo autocorrección")
    if arbitro not in ARBITROS:
        raise ValueError(f"arbitro debe ser uno de {ARBITROS}, no {arbitro!r}")
    if necesidad not in PREGUNTAS:
        raise ValueError(f"Necesidad desconocida: {necesidad}. Disponibles: {sorted(PREGUNTAS)}")
    if not ITERACIONES_MIN <= n <= ITERACIONES_MAX:
        raise ValueError(f"n debe estar entre {ITERACIONES_MIN} y {ITERACIONES_MAX}, no {n}")
    if modo not in MODOS:
        raise ValueError(f"modo debe ser uno de {MODOS}, no {modo!r}")
    r = servicio.cargar_necesidad(necesidad).resultado
    return CorridaIteraciones(
        necesidad=necesidad, pregunta=PREGUNTAS[necesidad], referencia=texto_referencia(r),
        modelos=list(dict.fromkeys(modelos)),  # sin repetidos, en orden
        n_iteraciones=n, modo=modo,
        timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
        referencia_sql="\n".join(c.sql for c in r.consultas),
        referencia_filas=[{k: _valor_json(v) for k, v in f.items()} for f in _filas_referencia(r)],
        arbitro=arbitro, medir_juez=medir_juez, corta_al_acertar=corta_al_acertar, tecnica=tecnica,
    )


def correr_iteraciones(
    necesidad: str,
    *,
    modelos: Sequence[str],
    n: int = ITERACIONES_DEFAULT,
    modo: str = MODO_DEFAULT,
    timeout_sql_llm_s: float = ri.TIMEOUT_LLM_SQL_S,
    timeout_respuesta_llm_s: float = ri.TIMEOUT_LLM_RESPUESTA_S,
    timeout_juez_s: float | None = None,
    db_path: Path | None = None,
    al_iniciar: Callable[[str, int, int, int], None] | None = None,
    al_avanzar: Callable[[Iteracion, int, int], None] | None = None,
    corrida: CorridaIteraciones | None = None,
    arbitro: str | None = None,
    medir_juez: bool = True,
    corta_al_acertar: bool | None = None,
    tecnica: str | None = None,
) -> CorridaIteraciones:
    """N iteraciones por modelo, modelo por modelo (todas las del pequeño y
    luego todas las del grande): así Ollama carga cada modelo una sola vez
    en vez de alternarlos en memoria. En modo "autocorreccion", el intento
    k (k >= 2) recibe `texto_retroalimentacion` del intento k-1 del MISMO
    modelo.

    `al_iniciar(modelo, numero, hechas, total)` se llama antes de cada
    intento y `al_avanzar(it, hechas, total)` después, con el intento ya
    agregado a `corrida.iteraciones` (UI: progreso y tarjeta en vivo).
    `corrida`: la de `nueva_corrida` si quien llama necesita tenerla antes
    de que termine; si no, se crea aquí."""
    if corrida is None:
        corrida = nueva_corrida(necesidad, modelos=modelos, n=n, modo=modo, arbitro=arbitro, medir_juez=medir_juez,
                                corta_al_acertar=corta_al_acertar, tecnica=tecnica)
    total = corrida.total_esperado
    vp = corrida.valores_protegidos
    for modelo in corrida.modelos:
        anterior: Iteracion | None = None
        for i in range(1, corrida.n_iteraciones + 1):
            if al_iniciar is not None:
                al_iniciar(modelo, i, len(corrida.iteraciones) + corrida.total_no_ejecutadas, total)
            retro = (texto_retroalimentacion(anterior, vp)
                     if corrida.modo == MODO_AUTOCORRECCION and anterior is not None else None)
            if retro is not None:
                anterior.pista_modelo = retro  # Fase 31: lo que viaja, tal cual
            it = correr_iteracion(
                corrida.necesidad, corrida.referencia, modelo, i, total=corrida.n_iteraciones,
                retroalimentacion=retro,
                timeout_sql_llm_s=timeout_sql_llm_s, timeout_respuesta_llm_s=timeout_respuesta_llm_s,
                timeout_juez_s=timeout_juez_s, db_path=db_path, referencia_filas=corrida.referencia_filas,
                arbitro=corrida.arbitro, medir_juez=corrida.medir_juez, tecnica=corrida.tecnica,
            )
            corrida.iteraciones.append(it)
            anterior = it
            # Fase 26: el primer acierto termina la cadena de este modelo; los
            # intentos que faltan quedan "no ejecutados (ya convergió)".
            if corrida.corta_al_acertar and it.veredicto == CORRECTA:
                corrida.convergencia[modelo] = i
            if al_avanzar is not None:
                al_avanzar(it, len(corrida.iteraciones) + corrida.total_no_ejecutadas, total)
            if modelo in corrida.convergencia:
                break
    corrida.estado = ESTADO_COMPLETA
    return corrida


# ---------------------------------------------------------------------------
# Export (para pegar el log en otra conversación con un modelo más grande)
# ---------------------------------------------------------------------------

def a_json(corrida: CorridaIteraciones) -> str:
    return json.dumps(corrida.a_dict(), indent=2, ensure_ascii=False) + "\n"


def nombre_archivo(necesidad: str) -> str:
    return f"{necesidad}_iteraciones_juez.json"


def nombre_archivo_texto(necesidad: str) -> str:
    return f"{necesidad}_iteraciones_juez.txt"


NOMBRE_EXPORT_TODAS_JSON = "todas_iteraciones_juez.json"
NOMBRE_EXPORT_TODAS_TEXTO = "todas_iteraciones_juez.txt"

_DESCRIPCION_MODO = {
    MODO_INDEPENDIENTE: "independiente: cada intento desde cero (mide estabilidad/variabilidad)",
    MODO_AUTOCORRECCION: ("autocorrección: desde el intento 2, cada intento recibió del anterior (mismo modelo) "
                          "su SQL, el error exacto o la forma del resultado, el chequeo de forma, el veredicto y "
                          "el diagnóstico del enfoque saneado, nunca valores de la referencia (mide si converge "
                          "a la respuesta correcta)"),
}


_DESCRIPCION_ARBITRO = {
    ARBITRO_CHEQUEO: ("chequeo automático contra la referencia (Fase 25); el juez-LLM solo diagnostica los "
                      "intentos fallidos y su opinión se registra aparte, sin decidir nada"),
    ARBITRO_JUEZ: "juez-LLM (Fases 11-24)",
}


def _resumen_texto(r: ResumenModelo) -> str:
    conv = f"primer «correcta» en el intento {r.primer_correcto}" if r.primer_correcto else "ningún «correcta»"
    if r.no_ejecutadas:
        conv += f" (convergió: {r.no_ejecutadas} intento(s) no ejecutado(s))"
    if r.estado_final is not None:
        conv += f" · termina en «{r.estado_final}»"
    coin = (f"primera coincidencia en el intento {r.primera_coincidencia}" if r.primera_coincidencia
            else "ninguna coincidencia")
    texto = (f"{r.modelo}: {r.correctas}/{r.n} correctas · {r.parciales} parciales · {r.incorrectas} incorrectas · "
             f"{r.sin_veredicto} sin veredicto · {conv} · chequeo automático: {r.coincidencias}/{r.n} coinciden "
             f"con la referencia ({coin})")
    texto += (f" · cumplió {r.pct_correctas:.0f}% · cumplió o se acercó (correcta o parcial) "
              f"{r.pct_acercamiento:.0f}%")
    if r.juez_opiniones:
        texto += (f" · juez-LLM: {r.juez_correctas}/{r.juez_opiniones} «correcta», de acuerdo con el chequeo en "
                  f"{r.juez_acuerdos}/{r.juez_opiniones} ({r.juez_falsos_negativos} falsos negativos, "
                  f"{r.juez_falsos_positivos} falsos positivos)")
    return texto


def a_texto(corrida: CorridaIteraciones) -> str:
    """Log legible de una corrida, completo y sin resumir: pregunta,
    referencia y, por cada intento, SQL, resultado crudo, redacciones,
    veredicto y (en autocorrección) el contexto que recibió."""
    sep = "=" * 78
    out = [
        sep,
        f"NECESIDAD {corrida.necesidad} · {corrida.timestamp}",
        f"Pregunta: {corrida.pregunta}",
        f"Modo: {_DESCRIPCION_MODO.get(corrida.modo, corrida.modo)}",
        f"Árbitro del veredicto: {_DESCRIPCION_ARBITRO.get(corrida.arbitro, corrida.arbitro)}",
        f"Modelos: {', '.join(corrida.modelos)} · {corrida.n_iteraciones} intentos por modelo · "
        f"temperatura {corrida.temperatura} (juez {corrida.temperatura_juez}) · "
        f"límite de la consulta en DuckDB {timeout_consulta(corrida.necesidad):.0f} s",
        f"Estado: {corrida.estado} · {len(corrida.iteraciones)} de {corrida.total_esperado} intentos ejecutados"
        + (f", {corrida.total_no_ejecutadas} no ejecutados porque ya había convergido" if corrida.total_no_ejecutadas else "")
        + (" · corte al primer acierto: sí" if corrida.corta_al_acertar else ""),
        f"Técnica de contexto: {corrida.tecnica}",
        "",
        "RESPUESTA DE REFERENCIA (correcta, del software determinista):",
        corrida.referencia,
        "",
        "RESUMEN:",
    ]
    for r in corrida.resumen().values():
        out.append(f"- {_resumen_texto(r)}")
    out += ["", "TABLA DE INTENTOS (intento · modelo · veredicto · cambio vs. el anterior · juez s · intento s):"]
    for f in corrida.tabla():
        out.append(f"- {f['intento']} · {f['modelo']} · {f['veredicto']} · {f['cambio'] or '—'} · "
                   f"{f['juez_s']:.1f} s · {f['iteracion_s']:.1f} s")
    cambios = {f"{f['modelo']}#{f['intento']}": f["cambio"] for f in corrida.tabla()}
    for it in corrida.iteraciones:
        out += ["", "-" * 78, f"{it.etiqueta.upper()}  →  {it.veredicto.upper()}", "-" * 78]
        out.append(f"Técnica: {it.tecnica} — ejemplos usados: {it.texto_ejemplos}")
        if it.bloque_ejemplos:
            out += ["Ejemplos inyectados (texto exacto):", it.bloque_ejemplos, ""]
        if cambios.get(f"{it.modelo}#{it.numero}"):
            out.append(f"Cambio respecto al intento anterior: {cambios[f'{it.modelo}#{it.numero}']}")
        if it.retroalimentacion:
            out += ["Contexto recibido del intento anterior:", it.retroalimentacion, ""]
        if it.sql_rechazado:
            out += [f"SQL rechazado (1.er intento de SQL): {it.sql_rechazado}", f"Motivo: {it.motivo_reintento}"]
        out.append(f"SQL ejecutado ({it.intentos_sql} intento(s) de SQL): {it.sql or '(no generado)'}")
        if it.error:
            out.append(f"Error: {it.error}")
        else:
            out += [f"Resultado crudo ({it.n_filas} fila(s); se registran hasta {FILAS_REGISTRADAS_MAX}):",
                    _tabla_texto(it.columnas, it.filas, it.n_filas, FILAS_REGISTRADAS_MAX)]
            out.append(f"Primera redacción del modelo: {it.primera_redaccion}")
            if it.cifras_rechazadas:
                out.append(f"Cifras de la primera redacción que NO están en el resultado: "
                           f"{', '.join(it.cifras_rechazadas)}")
            if it.segunda_redaccion:
                out.append(f"Segunda redacción (estricta): {it.segunda_redaccion}")
            out.append(f"Respuesta final [origen: {it.origen_respuesta}]: {it.respuesta}")
        if it.chequeo_forma:
            out.append(f"Chequeo automático de forma: {' '.join(it.chequeo_forma)}")
        if it.arbitro == ARBITRO_CHEQUEO:
            out.append(f"Veredicto (chequeo automático): {it.veredicto} [origen: {it.origen_veredicto}] "
                       f"— {it.justificacion}")
            if it.juez_veredicto is not None:
                out.append(f"Opinión del juez-LLM (solo comparación, no decide): {it.juez_veredicto} "
                           f"[origen: {it.juez_origen}] — {it.juez_justificacion}")
            if it.diagnostico_comparacion is not None:
                out.append(f"Comparación del juez al diagnosticar (NO se pasa al modelo): {it.diagnostico_comparacion}")
        else:
            out.append(f"Veredicto del juez: {it.veredicto} [origen: {it.origen_veredicto}]")
            out.append(f"Comparación con la referencia (NO se pasa al modelo): {it.justificacion}")
        out.append(f"Diagnóstico del enfoque (crudo): {it.diagnostico}")
        if it.oraciones_omitidas:
            out.append(f"  -> {it.oraciones_omitidas} oración(es) omitida(s) por citar la referencia; "
                       f"pasa al siguiente intento: {it.diagnostico_retro}")
        if it.coincide_referencia is not None and it.arbitro != ARBITRO_CHEQUEO:
            out.append(f"Chequeo automático vs. referencia: {'COINCIDE' if it.coincide_referencia else 'NO coincide'} "
                       f"({it.detalle_coincidencia})")
        out.append(f"Duración: pipeline {it.duracion_pipeline_s:.1f} s · juez {it.duracion_juez_s:.1f} s"
                   + (f" · juez de medición {it.duracion_juez_medicion_s:.1f} s" if it.duracion_juez_medicion_s else "")
                   + f" · intento completo {it.duracion_total_s:.1f} s")
        if it.numero == corrida.convergencia.get(it.modelo):
            for k in corrida.no_ejecutadas(it.modelo):
                out += ["", "-" * 78,
                        f"INTENTO {k} DE {corrida.n_iteraciones} · {it.modelo.upper()}  →  "
                        f"{texto_no_ejecutada(it.numero).upper()}", "-" * 78]
    return "\n".join(out) + "\n"


def exportar_json(corridas: Sequence[CorridaIteraciones]) -> str:
    """Varias corridas (p. ej. todas las de la sesión) en un solo JSON."""
    return json.dumps({"corridas": [c.a_dict() for c in corridas]}, indent=2, ensure_ascii=False) + "\n"


def exportar_texto(corridas: Sequence[CorridaIteraciones]) -> str:
    return "\n".join(a_texto(c) for c in corridas)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _imprimir_iteracion(it: Iteracion, hechas: int, total: int) -> None:
    print(f"\n=== [{hechas}/{total}] {it.etiqueta} "
          f"(pipeline {it.duracion_pipeline_s:.1f} s, juez {it.duracion_juez_s:.1f} s) ===")
    if it.retroalimentacion:
        print("Contexto recibido del intento anterior:\n  " + it.retroalimentacion.replace("\n", "\n  "))
    if it.sql_rechazado:
        print(f"SQL rechazado: {' '.join(it.sql_rechazado.split())}\nMotivo:     {it.motivo_reintento}")
    print(f"SQL ({it.intentos_sql} intento(s)): {' '.join((it.sql or '(no generado)').split())}")
    if it.error:
        print(f"ERROR:      {it.error}")
    else:
        print("Resultado:  " + _tabla_texto(it.columnas, it.filas, it.n_filas, 10).replace("\n", "\n            "))
        if it.origen_respuesta != "modelo":
            print(f"1.ª redacción: {it.primera_redaccion}  [cifras rechazadas: {', '.join(it.cifras_rechazadas)}]")
    print(f"Respuesta:  {it.respuesta}" + (f"  [redacción: {it.origen_respuesta}]" if it.origen_respuesta else ""))
    if it.chequeo_forma:
        print(f"Forma:      {' '.join(it.chequeo_forma)}")
    print(f"Veredicto:  {it.veredicto.upper()}  [origen: {it.origen_veredicto}]")
    if it.arbitro == ARBITRO_CHEQUEO:
        print(f"Detalle:    {it.justificacion}")
        if it.juez_veredicto is not None:
            print(f"Juez-LLM:   {it.juez_veredicto.upper()} (solo opinión, no decide) — {it.juez_justificacion}")
    else:
        print(f"Juez:       {it.justificacion}")
    print(f"Diagnóst.:  {it.diagnostico}" + (f"  [{it.oraciones_omitidas} oración(es) omitida(s)]"
                                            if it.oraciones_omitidas else ""))
    if it.coincide_referencia is not None and it.arbitro != ARBITRO_CHEQUEO:
        print(f"Chequeo:    {'COINCIDE' if it.coincide_referencia else 'NO coincide'} ({it.detalle_coincidencia})")
    sys.stdout.flush()


def _orden_nid(nid: str) -> int:
    return int(nid[1:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.llm.iteraciones_juez",
        description="N iteraciones de la pregunta de una necesidad por modelo, juzgadas contra la referencia.",
    )
    parser.add_argument("necesidades", nargs="+", metavar="necesidad",
                        choices=[*sorted(PREGUNTAS, key=_orden_nid), "todas"],
                        help=f"Una o más necesidades ({min(PREGUNTAS, key=_orden_nid)}.."
                             f"{max(PREGUNTAS, key=_orden_nid)}), o «todas»")
    parser.add_argument("--iteraciones", type=int, default=ITERACIONES_DEFAULT)
    parser.add_argument("--modo", choices=MODOS, default=MODO_DEFAULT)
    parser.add_argument("--arbitro", choices=ARBITROS, default=None,
                        help="Quién decide el veredicto (default: chequeo en autocorrección, juez en independiente)")
    parser.add_argument("--sin-corte", action="store_true",
                        help="Autocorrección: seguir iterando después del primer acierto (comportamiento de la Fase 25)")
    parser.add_argument("--sin-medir-juez", action="store_true",
                        help="Con árbitro «chequeo», no llamar al juez-LLM solo para registrar su opinión")
    parser.add_argument("--tecnica", choices=list(tecnicas.TECNICAS), default=None,
                        help="Técnica de contexto (Fase 30): base o few_shot; rag/harness están pendientes. "
                             "Default: VENTIA_TECNICA o «base»")
    parser.add_argument("--pequeno", default=MODELO_PEQUENO_DEFAULT)
    parser.add_argument("--grande", default=MODELO_GRANDE_DEFAULT,
                        help="Modelo grande; «-» para correr solo el pequeño")
    parser.add_argument("--timeout", type=float, default=ri.TIMEOUT_LLM_SQL_S,
                        help="Timeout (s) de cada llamada al modelo (SQL, redacción y juez)")
    parser.add_argument("--out", default=None,
                        help=f"Carpeta donde guardar {nombre_archivo('Nx')} y {nombre_archivo_texto('Nx')} "
                             "(se reescriben después de CADA intento)")
    args = parser.parse_args(argv)
    try:
        tecnica = tecnicas.validar_tecnica(args.tecnica) if args.tecnica else tecnicas.tecnica_por_defecto()
    except ValueError as exc:
        parser.error(str(exc))

    nids = sorted(PREGUNTAS, key=_orden_nid) if "todas" in args.necesidades else list(dict.fromkeys(args.necesidades))
    modelos = [args.pequeno] + ([] if args.grande == "-" else [args.grande])
    salida = Path(args.out) if args.out else None
    if salida:
        salida.mkdir(parents=True, exist_ok=True)

    for nid in nids:
        print(f"\nNecesidad: {nid}\nPregunta:  {PREGUNTAS[nid]}")
        print(f"Modelos:   {', '.join(modelos)} · {args.iteraciones} iteraciones c/u · modo {args.modo} · "
              f"árbitro {args.arbitro or arbitro_por_defecto(args.modo)}")
        print(f"Temperatura: pipeline {TEMPERATURA_ITERACIONES}, juez {TEMPERATURA_JUEZ} · timeout {args.timeout:.0f} s"
              f" · consulta en DuckDB {timeout_consulta(nid):.0f} s · técnica {tecnica}")
        corrida = nueva_corrida(nid, modelos=modelos, n=args.iteraciones, modo=args.modo, arbitro=args.arbitro,
                                tecnica=tecnica,
                                medir_juez=not args.sin_medir_juez,
                                corta_al_acertar=False if args.sin_corte else None)

        def _guardar() -> None:
            if salida:
                (salida / nombre_archivo(nid)).write_text(a_json(corrida), encoding="utf-8")
                (salida / nombre_archivo_texto(nid)).write_text(a_texto(corrida), encoding="utf-8")

        def _avance(it: Iteracion, hechas: int, total: int) -> None:
            _imprimir_iteracion(it, hechas, total)
            if it.numero == corrida.convergencia.get(it.modelo) and corrida.no_ejecutadas(it.modelo):
                faltan = corrida.no_ejecutadas(it.modelo)
                print(f"\n=== Intentos {faltan[0]}-{faltan[-1]} de {corrida.n_iteraciones} · {it.modelo}: "
                      f"{texto_no_ejecutada(it.numero)} ===")
            _guardar()

        correr_iteraciones(
            nid, modelos=modelos, corrida=corrida,
            timeout_sql_llm_s=args.timeout, timeout_respuesta_llm_s=args.timeout, al_avanzar=_avance,
        )
        _guardar()
        print(f"\n=== Resumen {nid} ({corrida.estado}: {len(corrida.iteraciones)} de "
              f"{corrida.total_esperado} intentos ejecutados"
              + (f", {corrida.total_no_ejecutadas} no ejecutados porque ya había convergido"
                 if corrida.total_no_ejecutadas else "") + ") ===")
        for r in corrida.resumen().values():
            print(_resumen_texto(r))
        if salida:
            print(f"Log: {salida / nombre_archivo(nid)} · {salida / nombre_archivo_texto(nid)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
