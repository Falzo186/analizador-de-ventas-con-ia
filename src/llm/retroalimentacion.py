"""Fase 24: retroalimentación específica entre intentos SIN fugar la respuesta.

En modo autocorrección (`src.llm.iteraciones_juez`), el intento k+1 recibe
lo que pasó en el intento k. Este módulo decide QUÉ puede recibir sin que se
le filtre la respuesta correcta:

1. `valores_protegidos` / `fugas` / `sanear`: los valores de la referencia
   (nombres y cifras del resultado de la consulta de referencia) no pueden
   aparecer en la retroalimentación. El diagnóstico del juez se sanea quitando
   cada ORACIÓN que cite uno; no se enmascara el valor con "[oculto]", porque
   enmascarar un nombre o una cifra le diría al modelo dónde estaba el dato
   correcto.
2. `chequeo_forma`: pistas deterministas que no dependen de la referencia,
   solo de la pregunta (cuántas filas pide, si pide nombres): "la pregunta
   pide 10 filas y tu consulta devolvió 49,688".
3. `coincide_con_referencia`: comparación determinista del resultado del
   intento con el de la referencia. Es SOLO informativa (panel, log, reporte),
   para contrastar el veredicto del juez-LLM; NUNCA se pasa al modelo.

Reglas de detección de cifras (`fugas`), decididas en esta fase:
- Las cifras que aparecen en la PREGUNTA no cuentan (10, 50, 1 de
  "add_to_cart_order = 1"): son parte de la pregunta, no de la respuesta.
- Enteros de la referencia con |v| >= `MIN_ENTERO_PROTEGIDO` (10): cualquier
  lectura del token igual a v ("491291", "491,291", "491.291"). Los enteros
  chicos (0, 1, 2 recompras) son demasiado genéricos para considerarlos fuga.
- No enteros de la referencia (tasa 0.942, promedio 10.107, 59.006%): se
  comparan v y v*100 (fracción -> porcentaje) redondeados a los decimales que
  muestra el token ("94.2", "0.94", "10.11"); con 0 decimales, solo si el
  redondeo es >= 10 ("94", "10", "59").
- Textos: cada valor de texto de la referencia (nombres de producto, pasillo,
  departamento) de 3 caracteres o más, como palabra completa y sin distinguir
  mayúsculas.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from src.analytics import necesidades as nec
from src.llm.cifras_instacart import lecturas_numericas, numeros_en_texto
from src.llm.safety_instacart import LIMITE_FILAS_DEFAULT

MIN_ENTERO_PROTEGIDO = 10
MIN_LARGO_TEXTO_PROTEGIDO = 3
#: Tolerancia relativa de `coincide_con_referencia` para cifras (redondeos).
TOLERANCIA_RELATIVA = 0.005
#: Proporción de nombres del top que debe coincidir en una respuesta tipo lista.
MIN_COINCIDENCIA_LISTA = 0.8
#: Filas mínimas de un intento para una lista sin número fijo (N3/N4).
MIN_FILAS_LISTA_ABIERTA = 3

#: Reemplazo del diagnóstico cuando TODAS sus oraciones citaban la referencia.
DIAGNOSTICO_OMITIDO = ("(El evaluador justificó citando datos de la respuesta correcta; se omite para no "
                       "revelarla. Revisa el enfoque de tu consulta contra la pregunta.)")


# ---------------------------------------------------------------------------
# 1. Valores protegidos y fugas
# ---------------------------------------------------------------------------

def _es_numero(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _norm(texto: str) -> str:
    return " ".join(str(texto).casefold().split())


@dataclass(frozen=True)
class ValoresProtegidos:
    textos: tuple[str, ...]
    enteros: tuple[int, ...]
    decimales: tuple[float, ...]
    #: Cifras de la pregunta: nunca se consideran fuga.
    de_la_pregunta: tuple[float, ...]


def valores_protegidos(pregunta: str, filas_referencia: list[dict]) -> ValoresProtegidos:
    """Todo lo que no puede aparecer en la retroalimentación: cada celda del
    resultado de la consulta de referencia (de él salen también los valores
    clave y la redacción de referencia)."""
    textos, enteros, decimales = set(), set(), set()
    for fila in filas_referencia:
        for v in fila.values():
            if isinstance(v, str):
                if len(v.strip()) >= MIN_LARGO_TEXTO_PROTEGIDO:
                    textos.add(_norm(v))
            elif _es_numero(v):
                if float(v).is_integer():
                    if abs(v) >= MIN_ENTERO_PROTEGIDO:
                        enteros.add(int(v))
                else:
                    decimales.add(float(v))
    pregunta_nums = {lv for tok in numeros_en_texto(pregunta) for lv, _d in lecturas_numericas(tok)}
    return ValoresProtegidos(tuple(sorted(textos)), tuple(sorted(enteros)), tuple(sorted(decimales)),
                             tuple(sorted(pregunta_nums)))


def _cifra_protegida(valor: float, decimales: int, vp: ValoresProtegidos) -> bool:
    if valor in vp.de_la_pregunta:
        return False
    if decimales == 0 and valor.is_integer() and int(valor) in vp.enteros:
        return True
    for v in vp.decimales:
        for candidato in (v, v * 100):
            redondeado = round(candidato, decimales)
            if redondeado == valor and (decimales > 0 or abs(redondeado) >= MIN_ENTERO_PROTEGIDO):
                return True
    return False


def fugas(texto: str | None, vp: ValoresProtegidos) -> list[str]:
    """Fragmentos de `texto` que son valores de la referencia (lista vacía =
    no hay fuga)."""
    if not texto:
        return []
    encontrados = []
    for token in numeros_en_texto(texto):
        if any(_cifra_protegida(v, d, vp) for v, d in lecturas_numericas(token)):
            encontrados.append(token)
    normal = _norm(texto)
    for t in vp.textos:
        if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", normal):
            encontrados.append(t)
    return encontrados


_FIN_DE_ORACION = re.compile(r"(?<=[.!?;])\s+|\n+")


def sanear(texto: str | None, vp: ValoresProtegidos) -> tuple[str, int]:
    """Quita de `texto` cada oración que cite un valor de la referencia.
    Devuelve (texto saneado, oraciones quitadas). Si no queda nada útil,
    devuelve `DIAGNOSTICO_OMITIDO`."""
    if not texto or not texto.strip():
        return "", 0
    oraciones = [o.strip() for o in _FIN_DE_ORACION.split(texto.strip()) if o.strip()]
    limpias = [o for o in oraciones if not fugas(o, vp)]
    quitadas = len(oraciones) - len(limpias)
    if not limpias:
        return DIAGNOSTICO_OMITIDO, quitadas
    return " ".join(limpias), quitadas


# ---------------------------------------------------------------------------
# 2. Chequeo de forma (sin la referencia: solo lo que dice la pregunta)
# ---------------------------------------------------------------------------

def texto_n_filas(n: int) -> str:
    """'1,234', o '1,000 o más' si la guarda de filas cortó el resultado."""
    return f"{n:,} o más (la guarda corta en {LIMITE_FILAS_DEFAULT:,})" if n >= LIMITE_FILAS_DEFAULT else f"{n:,}"


def _hay_columna_texto(filas: list[list] | list[tuple]) -> bool:
    return any(isinstance(v, str) for fila in filas[:5] for v in fila)


def _ordenado_por_alguna_cifra(filas: list) -> bool:
    """¿Alguna columna numérica está ordenada (de mayor a menor o al revés)?"""
    for j in range(len(filas[0])):
        col = [fila[j] for fila in filas]
        if all(_es_numero(v) for v in col):
            if all(a >= b for a, b in zip(col, col[1:])) or all(a <= b for a, b in zip(col, col[1:])):
                return True
    return False


#: Pista ante una consulta cortada por el timeout de la guarda o sin memoria
#: (el mensaje de error no dice por qué fue lenta). Fase 29: además de las
#: subconsultas correlacionadas, nombra el JOIN que multiplica filas: llama3.1
#: unía products ANTES de agrupar los pares de N9 (63 s y sin memoria); agrupando
#: por ids tarda 10 s. Cabe en una lección de la bitácora (220 caracteres).
PISTA_TIMEOUT = ("La consulta fue demasiado lenta y se cortó: evita subconsultas correlacionadas y, si un JOIN "
                 "multiplica filas (p. ej. order_products consigo misma), agrupa primero por los ids y une "
                 "products al final.")


def chequeo_error(error: str | None) -> list[str]:
    """Pistas deterministas ante un intento que no ejecutó. El error de DuckDB
    se pasa completo aparte; esto solo agrega lo que el mensaje no dice."""
    if error and ("excedió el límite" in error or "Out of Memory" in error):
        return [PISTA_TIMEOUT]
    return []


def chequeo_forma(necesidad: str, columnas: list[str] | None, filas: list | None, n_filas: int | None) -> list[str]:
    """Pistas deterministas sobre la FORMA del resultado del intento, que se
    deducen de la pregunta (no de la respuesta correcta)."""
    if columnas is None or filas is None:
        return []
    d = nec.DEFINICIONES[necesidad]
    n = n_filas if n_filas is not None else len(filas)
    pistas = []
    if n > 1 and d.tipo in (nec.LISTA, nec.UNICO) and not _ordenado_por_alguna_cifra(filas):
        pistas.append(f"Tu resultado trae {texto_n_filas(n)} filas sin ordenar por ninguna columna numérica: la "
                      "pregunta pide el o los primeros según una métrica, así que ordena por ella (ORDER BY).")
    if n == 0:
        pistas.append("Tu consulta no devolvió ninguna fila.")
    elif d.tipo == nec.LISTA and d.filas_esperadas is not None and n != d.filas_esperadas:
        pistas.append(f"La pregunta pide {d.filas_esperadas} filas (un top {d.filas_esperadas}) y tu consulta "
                      f"devolvió {texto_n_filas(n)}.")
    elif d.tipo == nec.ESCALAR and n != 1:
        que = "una sola fila (con sus cifras)" if d.columnas_valor_extra else "un solo número (una fila)"
        pistas.append(f"La pregunta pide {que} y tu consulta devolvió {texto_n_filas(n)} filas.")
    # Fase 28: si la entidad se identifica con un número (user_id, la hora), no se exige texto.
    if n > 0 and d.columna_nombre and d.nombre_es_texto and not _hay_columna_texto(filas):
        pistas.append(f"Tu resultado no trae ninguna columna de texto: la pregunta pide identificar cada "
                      f"{d.entidad} por su nombre, no por su id.")
    return pistas


# ---------------------------------------------------------------------------
# 3. Coincidencia automática con la referencia (informativa, nunca al modelo)
# ---------------------------------------------------------------------------

def _cerca(x: float, v: float) -> bool:
    if v == 0:
        return abs(x) <= TOLERANCIA_RELATIVA
    return any(abs(c - v) <= TOLERANCIA_RELATIVA * abs(v) for c in (x, x * 100, x / 100))


def _clave(x: object) -> object | None:
    """Identificador comparable: texto normalizado, o el entero si es un
    número entero (user_id, la hora). Otra cosa no identifica nada."""
    if isinstance(x, str):
        return _norm(x)
    if _es_numero(x) and float(x).is_integer():
        return int(x)
    return None


def _claves_de_fila(fila) -> set:
    return {c for c in (_clave(x) for x in fila) if c is not None}


def coincide_con_referencia(
    necesidad: str, filas_referencia: list[dict], filas: list | None,
) -> tuple[bool, str]:
    """¿El resultado del intento contiene la respuesta de la referencia?
    - escalar: cada cifra de respuesta (`columna_valor` y, Fase 28,
      `columnas_valor_extra`) está entre las cifras de la primera fila (±0.5 %,
      acepta fracción vs. porcentaje);
    - único: la primera fila trae el identificador de la referencia (el nombre,
      o el número si la entidad es numérica; Fase 28: los dos nombres del par
      de N9) y, si además trae cifras, alguna coincide con la de la
      referencia (la pregunta pide el identificador: una fila con solo el
      identificador correcto cuenta). Compara resultados, no métodos;
    - lista: si la pregunta fija k filas, al menos el 80 % de los k
      identificadores de la referencia están en las primeras k filas del
      intento; si no la fija ("¿cuáles...?"), al menos el 80 % de las primeras
      m filas del intento (m = min(10, filas del intento), m >= 3) traen un
      identificador del top 10 de la referencia. El orden de los empates no se
      exige.
    Devuelve (coincide, detalle). El detalle puede citar la referencia: es
    para el panel y el log, no para el modelo."""
    return coincide_con_forma(nec.DEFINICIONES[necesidad], filas_referencia, filas)


def coincide_con_forma(d, filas_referencia: list[dict], filas: list | None) -> tuple[bool, str]:
    """El comparador de `coincide_con_referencia` para cualquier `d` con la
    forma de una `DefNecesidad` (tipo, columna_nombre, columnas_nombre_extra,
    columna_valor, columnas_valor_extra, filas_esperadas). Fase 30: lo usa la
    validación fuera de muestra con preguntas que no son de las 10 necesidades."""
    if not filas_referencia:
        return False, "la referencia no tiene filas"
    if not filas:
        return False, "el intento no tiene resultado"
    ref0 = filas_referencia[0]
    primera = list(filas[0])
    if d.tipo == nec.ESCALAR:
        columnas = (d.columna_valor, *d.columnas_valor_extra)
        presentes = [c for c in columnas
                     if any(_es_numero(x) and _cerca(float(x), float(ref0[c])) for x in primera)]
        ok = len(presentes) == len(columnas)
        detalle = ", ".join(f"{c}={ref0[c]} {'presente' if c in presentes else 'ausente'}" for c in columnas)
        return ok, f"primera fila: {detalle}"
    if d.tipo == nec.UNICO:
        celdas = list(primera)
        nombres = [ref0[c] for c in (d.columna_nombre, *d.columnas_nombre_extra)]
        ok_nombre = True
        for nombre in nombres:
            hallado = next((k for k, x in enumerate(celdas) if _clave(x) is not None and _clave(x) == _clave(nombre)), None)
            if hallado is None:
                ok_nombre = False
            else:
                celdas.pop(hallado)  # el identificador numérico (p. ej. la hora) no cuenta como cifra
        v = ref0[d.columna_valor]
        cifras = [x for x in celdas if _es_numero(x)]
        ok_valor = any(_cerca(float(x), float(v)) for x in cifras)
        rotulo = " + ".join(str(x) for x in nombres)
        if not cifras:
            # La pregunta pide el identificador ("¿cuál...?"): sin cifras en la fila, basta con él.
            return ok_nombre, f"primera fila: identificador {'sí' if ok_nombre else 'no'} ({rotulo}), sin cifras"
        return ok_nombre and ok_valor, (f"primera fila: identificador {'sí' if ok_nombre else 'no'} "
                                        f"({rotulo}), cifra {'sí' if ok_valor else 'no'} ({v})")
    referencia = {_clave(f[d.columna_nombre]) for f in filas_referencia}
    if d.filas_esperadas is not None:
        k = d.filas_esperadas
        obtenidos = set().union(*(_claves_de_fila(fila) for fila in filas[:k]))
        comunes = len(referencia & obtenidos)
        return (comunes >= MIN_COINCIDENCIA_LISTA * len(referencia),
                f"{comunes}/{len(referencia)} identificadores del top coinciden")
    m = min(len(filas_referencia), len(filas))
    comunes = sum(1 for fila in filas[:m] if _claves_de_fila(fila) & referencia)
    return (m >= MIN_FILAS_LISTA_ABIERTA and comunes >= MIN_COINCIDENCIA_LISTA * m,
            f"{comunes}/{m} de las primeras filas del intento traen un identificador del top "
            f"{len(filas_referencia)} de la referencia")


# ---------------------------------------------------------------------------
# Fase 31: «por qué» del veredicto, para el USUARIO (nunca para el modelo)
# ---------------------------------------------------------------------------

#: Una línea con la regla de «parcial». Con el árbitro automático (Fase 25) el
#: comparador es sí/no: no hay umbral de «parcial» que mostrar.
REGLA_PARCIAL = ("Regla de «parcial»: el chequeo automático (árbitro desde la Fase 25) es sí/no, así que nunca "
                 "da «parcial»; solo el árbitro juez-LLM puede darlo.")


def _fmt(v: object) -> str:
    if isinstance(v, float) and math.isfinite(v):
        return f"{v:.4g}" if abs(v) < 1 else f"{v:,.4g}" if abs(v) < 1e4 else f"{v:,.0f}"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    return str(v)


def explicacion_usuario(
    necesidad: str, filas_referencia: list[dict], columnas: list[str] | None, filas: list | None,
    coincide: bool | None, detalle: str | None,
) -> str:
    """Fase 31: por qué el intento coincide o no con la referencia, de forma
    DETERMINISTA y con los valores (los de la referencia incluidos). Es para
    la pantalla: NUNCA viaja al prompt (lo que viaja es `pista_modelo`, el
    texto saneado de `iteraciones_juez.texto_retroalimentacion`)."""
    d = nec.DEFINICIONES[necesidad]
    if not filas_referencia:
        return "No hay filas de referencia contra las que comparar."
    ref0 = filas_referencia[0]
    nombres = [c for c in (d.columna_nombre, *d.columnas_nombre_extra) if c]
    valores = [d.columna_valor, *d.columnas_valor_extra]
    esperado = " + ".join(_fmt(ref0.get(c, "?")) for c in nombres) if nombres else ""
    esperado = (esperado + " · " if esperado else "") + ", ".join(f"{c} = {_fmt(ref0.get(c, '?'))}" for c in valores)
    if not filas:
        obtenido = "sin resultado"
    else:
        obtenido = ", ".join(f"{c} = {_fmt(v)}" for c, v in list(zip(columnas or [], filas[0]))[:5])
    n = len(filas or [])
    pide = (f"la pregunta pide {d.filas_esperadas}" if d.filas_esperadas is not None
            else "la pregunta no fija cuántas")
    estado = "Coincide" if coincide else "No coincide"
    return (f"{estado} con la referencia. Obtuvo (primera fila): {obtenido}; esperado: {esperado}. "
            f"Forma: {n} fila(s) ({pide}; tipo «{d.tipo}»). Comparador: {detalle or '—'}.")
