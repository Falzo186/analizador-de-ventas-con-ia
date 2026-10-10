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

CLI (modelos reales):
  python -m src.llm.iteraciones_juez N3 --iteraciones 5 --modo autocorreccion \\
      --pequeno qwen2.5-coder:1.5b --grande qwen2.5-coder:14b --timeout 300 --out docs/evidencia/fase23
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
from src.analytics.registro import NECESIDADES
from src.dashboard import servicio
from src.llm import client
from src.llm import responder_instacart as ri
from src.llm.prompts_instacart import VEREDICTOS_JUEZ, prompt_juez_instacart

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

#: Pregunta fija de cada necesidad, tal cual la constante `PREGUNTA` de su módulo.
PREGUNTAS: dict[str, str] = {
    nid: sys.modules[fn.__module__].PREGUNTA for nid, fn in NECESIDADES.items()
}


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


def texto_referencia(r: ResultadoNecesidad) -> str:
    """Respuesta de referencia que ve el juez: los `hallazgos` y los
    `valores_clave` del resultado determinista, sin reescribirlos. No se
    pasan las tablas completas: ocuparían miles de tokens del contexto de
    4,096 con el que Ollama carga los modelos."""
    lineas = ["Hallazgos:"]
    lineas += [f"- {h}" for h in r.hallazgos]
    lineas.append("Valores clave:")
    lineas += [f"- {v.etiqueta}: {_fmt(v.valor)} ({v.unidad})" for v in r.valores_clave]
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
    justificacion: str
    origen: str
    #: Texto crudo que devolvió el juez (None si no se le llamó).
    crudo: str | None = None


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
    justificacion = datos.get("justificacion") or datos.get("justificación") or ""
    return Veredicto(veredicto, str(justificacion).strip(), ORIGEN_JUEZ, crudo)


def juzgar(pregunta: str, referencia: str, respuesta: str, *, modelo: str, timeout_s: float) -> Veredicto:
    """Pide al `modelo` el veredicto (temperatura 0.0, salida JSON)."""
    sistema, usuario = prompt_juez_instacart(pregunta, referencia, respuesta)
    try:
        crudo = client.chat_con_modelo(
            modelo, sistema, usuario, temperatura=TEMPERATURA_JUEZ, timeout_s=timeout_s, formato_json=True,
        )
    except client.LlmNoDisponible as exc:
        return Veredicto(SIN_VEREDICTO, f"El juez no respondió: {exc}", ORIGEN_JUEZ_ERROR)
    return parsear_veredicto(crudo)


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

    @property
    def ok(self) -> bool:
        return self.error is None

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

    @property
    def pct_correctas(self) -> float:
        return 100.0 * self.correctas / self.n if self.n else 0.0


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

    @property
    def total_esperado(self) -> int:
        return self.n_iteraciones * len(self.modelos)

    @property
    def completa(self) -> bool:
        return self.estado == ESTADO_COMPLETA and len(self.iteraciones) == self.total_esperado

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
            out[modelo] = ResumenModelo(modelo, len(its), cuenta[CORRECTA], cuenta[PARCIAL],
                                        cuenta[INCORRECTA], cuenta[SIN_VEREDICTO])
        return out

    def a_dict(self) -> dict:
        return {
            "necesidad": self.necesidad,
            "pregunta": self.pregunta,
            "referencia": self.referencia,
            "modo": self.modo,
            "modelos": self.modelos,
            "n_iteraciones": self.n_iteraciones,
            "estado": self.estado,
            "iteraciones_registradas": len(self.iteraciones),
            "iteraciones_esperadas": self.total_esperado,
            "temperatura": self.temperatura,
            "temperatura_juez": self.temperatura_juez,
            "timestamp": self.timestamp,
            "resumen": {m: {**asdict(r), "pct_correctas": r.pct_correctas} for m, r in self.resumen().items()},
            "iteraciones": [{"etiqueta": it.etiqueta, **asdict(it)} for it in self.iteraciones],
        }


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


def texto_retroalimentacion(it: Iteracion) -> str:
    """Lo que el intento siguiente recibe del intento `it` en modo
    autocorrección: su SQL, el error o el resultado (primeras filas), la
    redacción y el veredicto del juez con su justificación. Se agrega al
    mensaje de usuario de la llamada de SQL y de la primera redacción
    (`prompts_instacart._con_retroalimentacion`)."""
    lineas = [
        f"INTENTO ANTERIOR DE ESTA MISMA PREGUNTA (intento {it.numero} de {it.total}). "
        "Un evaluador lo comparó con la respuesta correcta.",
    ]
    if it.sql_rechazado:
        lineas.append(f"Tu primer SQL fue rechazado: {' '.join(it.sql_rechazado.split())}")
        lineas.append(f"Motivo del rechazo: {it.motivo_reintento}")
    lineas.append(f"SQL que generaste: {' '.join(it.sql.split()) if it.sql else '(no se generó SQL válido)'}")
    if it.error:
        lineas.append(f"Error: {it.error}")
    else:
        lineas.append(f"Resultado que dio esa consulta:\n"
                      f"{_tabla_texto(it.columnas, it.filas, it.n_filas, FILAS_RETROALIMENTACION_MAX)}")
        lineas.append(f"Respuesta que redactaste: {it.respuesta}")
    lineas.append(f"Veredicto del evaluador: {it.veredicto.upper()}. Motivo: {it.justificacion}")
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
) -> Iteracion:
    """Una iteración: pipeline completo (temperatura 0.5) + juez (0.0).
    `retroalimentacion`: intento anterior (modo autocorrección)."""
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
    )
    t1 = time.perf_counter()
    if res.ok:
        v = juzgar(pregunta, referencia, res.respuesta or "", modelo=modelo,
                   timeout_s=timeout_juez_s if timeout_juez_s is not None else timeout_respuesta_llm_s)
    else:
        v = Veredicto(INCORRECTA, f"El pipeline no produjo respuesta: {res.error}", ORIGEN_AUTOMATICO)
    t2 = time.perf_counter()
    filas = None if res.filas is None else [
        [_valor_json(x) for x in fila] for fila in res.filas[:FILAS_REGISTRADAS_MAX]
    ]
    return Iteracion(
        modelo=modelo, numero=numero, sql=res.sql, respuesta=res.respuesta, error=res.error,
        intentos_sql=res.intentos, origen_respuesta=res.origen_respuesta,
        veredicto=v.veredicto, justificacion=v.justificacion, origen_veredicto=v.origen, crudo_juez=v.crudo,
        duracion_pipeline_s=round(t1 - t0, 2), duracion_juez_s=round(t2 - t1, 2),
        total=total, sql_rechazado=res.sql_rechazado, motivo_reintento=res.motivo_reintento,
        columnas=res.columnas, filas=filas, n_filas=None if res.filas is None else len(res.filas),
        primera_redaccion=res.primera_redaccion, segunda_redaccion=res.segunda_redaccion,
        cifras_rechazadas=list(res.cifras_rechazadas), retroalimentacion=retroalimentacion,
    )


def nueva_corrida(
    necesidad: str, *, modelos: Sequence[str], n: int = ITERACIONES_DEFAULT, modo: str = MODO_DEFAULT,
) -> CorridaIteraciones:
    """Corrida vacía ("en curso"), con la referencia ya cargada. Quien la
    corre (`correr_iteraciones(corrida=...)`) le agrega cada intento apenas
    termina, así que guardarla ANTES de empezar (la UI la pone en
    `st.session_state`) conserva todo lo terminado si se interrumpe."""
    if necesidad not in PREGUNTAS:
        raise ValueError(f"Necesidad desconocida: {necesidad}. Disponibles: {sorted(PREGUNTAS)}")
    if not ITERACIONES_MIN <= n <= ITERACIONES_MAX:
        raise ValueError(f"n debe estar entre {ITERACIONES_MIN} y {ITERACIONES_MAX}, no {n}")
    if modo not in MODOS:
        raise ValueError(f"modo debe ser uno de {MODOS}, no {modo!r}")
    return CorridaIteraciones(
        necesidad=necesidad, pregunta=PREGUNTAS[necesidad], referencia=referencia_de(necesidad),
        modelos=list(dict.fromkeys(modelos)),  # sin repetidos, en orden
        n_iteraciones=n, modo=modo,
        timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
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
        corrida = nueva_corrida(necesidad, modelos=modelos, n=n, modo=modo)
    total = corrida.total_esperado
    for modelo in corrida.modelos:
        anterior: Iteracion | None = None
        for i in range(1, corrida.n_iteraciones + 1):
            if al_iniciar is not None:
                al_iniciar(modelo, i, len(corrida.iteraciones), total)
            retro = (texto_retroalimentacion(anterior)
                     if corrida.modo == MODO_AUTOCORRECCION and anterior is not None else None)
            it = correr_iteracion(
                corrida.necesidad, corrida.referencia, modelo, i, total=corrida.n_iteraciones,
                retroalimentacion=retro,
                timeout_sql_llm_s=timeout_sql_llm_s, timeout_respuesta_llm_s=timeout_respuesta_llm_s,
                timeout_juez_s=timeout_juez_s, db_path=db_path,
            )
            corrida.iteraciones.append(it)
            anterior = it
            if al_avanzar is not None:
                al_avanzar(it, len(corrida.iteraciones), total)
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
    MODO_AUTOCORRECCION: ("autocorrección: desde el intento 2, cada intento recibió el SQL, el resultado o "
                          "error, la redacción y el veredicto del intento anterior del mismo modelo (mide "
                          "si converge a la respuesta correcta)"),
}


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
        f"Modelos: {', '.join(corrida.modelos)} · {corrida.n_iteraciones} intentos por modelo · "
        f"temperatura {corrida.temperatura} (juez {corrida.temperatura_juez})",
        f"Estado: {corrida.estado} · {len(corrida.iteraciones)} de {corrida.total_esperado} intentos registrados",
        "",
        "RESPUESTA DE REFERENCIA (correcta, del software determinista):",
        corrida.referencia,
        "",
        "RESUMEN:",
    ]
    for r in corrida.resumen().values():
        out.append(f"- {r.modelo}: {r.correctas}/{r.n} correctas · {r.parciales} parciales · "
                   f"{r.incorrectas} incorrectas · {r.sin_veredicto} sin veredicto")
    for it in corrida.iteraciones:
        out += ["", "-" * 78, f"{it.etiqueta.upper()}  →  {it.veredicto.upper()}", "-" * 78]
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
        out.append(f"Veredicto del juez: {it.veredicto} [origen: {it.origen_veredicto}]")
        out.append(f"Justificación: {it.justificacion}")
        out.append(f"Duración: pipeline {it.duracion_pipeline_s:.1f} s · juez {it.duracion_juez_s:.1f} s")
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
    print(f"Veredicto:  {it.veredicto.upper()}  [origen: {it.origen_veredicto}]")
    print(f"Juez:       {it.justificacion}")
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
                        help="Una o más necesidades (N1..N11), o «todas»")
    parser.add_argument("--iteraciones", type=int, default=ITERACIONES_DEFAULT)
    parser.add_argument("--modo", choices=MODOS, default=MODO_DEFAULT)
    parser.add_argument("--pequeno", default=MODELO_PEQUENO_DEFAULT)
    parser.add_argument("--grande", default=MODELO_GRANDE_DEFAULT,
                        help="Modelo grande; «-» para correr solo el pequeño")
    parser.add_argument("--timeout", type=float, default=ri.TIMEOUT_LLM_SQL_S,
                        help="Timeout (s) de cada llamada al modelo (SQL, redacción y juez)")
    parser.add_argument("--out", default=None,
                        help=f"Carpeta donde guardar {nombre_archivo('Nx')} y {nombre_archivo_texto('Nx')} "
                             "(se reescriben después de CADA intento)")
    args = parser.parse_args(argv)

    nids = sorted(PREGUNTAS, key=_orden_nid) if "todas" in args.necesidades else list(dict.fromkeys(args.necesidades))
    modelos = [args.pequeno] + ([] if args.grande == "-" else [args.grande])
    salida = Path(args.out) if args.out else None
    if salida:
        salida.mkdir(parents=True, exist_ok=True)

    for nid in nids:
        print(f"\nNecesidad: {nid}\nPregunta:  {PREGUNTAS[nid]}")
        print(f"Modelos:   {', '.join(modelos)} · {args.iteraciones} iteraciones c/u · modo {args.modo}")
        print(f"Temperatura: pipeline {TEMPERATURA_ITERACIONES}, juez {TEMPERATURA_JUEZ} · timeout {args.timeout:.0f} s")
        corrida = nueva_corrida(nid, modelos=modelos, n=args.iteraciones, modo=args.modo)

        def _guardar() -> None:
            if salida:
                (salida / nombre_archivo(nid)).write_text(a_json(corrida), encoding="utf-8")
                (salida / nombre_archivo_texto(nid)).write_text(a_texto(corrida), encoding="utf-8")

        def _avance(it: Iteracion, hechas: int, total: int) -> None:
            _imprimir_iteracion(it, hechas, total)
            _guardar()

        correr_iteraciones(
            nid, modelos=modelos, corrida=corrida,
            timeout_sql_llm_s=args.timeout, timeout_respuesta_llm_s=args.timeout, al_avanzar=_avance,
        )
        _guardar()
        print(f"\n=== Resumen {nid} ({corrida.estado}: {len(corrida.iteraciones)} de "
              f"{corrida.total_esperado} intentos) ===")
        for r in corrida.resumen().values():
            print(f"{r.modelo}: {r.correctas}/{r.n} correctas ({r.pct_correctas:.0f}%) · "
                  f"{r.parciales} parciales · {r.incorrectas} incorrectas · {r.sin_veredicto} sin veredicto")
        if salida:
            print(f"Log: {salida / nombre_archivo(nid)} · {salida / nombre_archivo_texto(nid)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
