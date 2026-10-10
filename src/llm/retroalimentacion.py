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


#: Pista ante una consulta cortada por el timeout de la guarda (el mensaje
#: de error no dice por qué fue lenta).
PISTA_TIMEOUT = ("La consulta fue demasiado lenta y se cortó: evita subconsultas correlacionadas (IN, ANY o "
                 "EXISTS que se recalculan por cada fila) y une las tablas con JOIN directo por sus claves.")


def chequeo_error(error: str | None) -> list[str]:
    """Pistas deterministas ante un intento que no ejecutó. El error de DuckDB
    se pasa completo aparte; esto solo agrega lo que el mensaje no dice."""
    if error and "excedió el límite" in error:
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
        pistas.append(f"La pregunta pide un solo número (una fila) y tu consulta devolvió {texto_n_filas(n)} filas.")
    if n > 0 and d.columna_nombre and not _hay_columna_texto(filas):
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


def coincide_con_referencia(
    necesidad: str, filas_referencia: list[dict], filas: list | None,
) -> tuple[bool, str]:
    """¿El resultado del intento contiene la respuesta de la referencia?
    - escalar: alguna cifra de la primera fila coincide con el valor (±0.5 %,
      acepta fracción vs. porcentaje);
    - único: la primera fila trae el nombre de la referencia y, si trae
      cifras, alguna coincide con la de la referencia (la pregunta pide el
      nombre: una fila con solo el nombre correcto cuenta). Compara
      resultados, no métodos: un SQL con otra métrica que acierta el nombre
      y no devuelve la cifra cuenta como coincidencia;
    - lista: si la pregunta fija k filas (N1: 10), al menos el 80 % de los k
      nombres de la referencia están en las primeras k filas del intento; si no
      la fija (N3/N4, "¿cuáles...?"), al menos el 80 % de los primeros m
      nombres del intento (m = min(10, filas del intento), m >= 3) están en el
      top 10 de la referencia. El orden de los empates no se exige.
    Devuelve (coincide, detalle). El detalle puede citar la referencia: es
    para el panel y el log, no para el modelo."""
    d = nec.DEFINICIONES[necesidad]
    if not filas_referencia:
        return False, "la referencia no tiene filas"
    if not filas:
        return False, "el intento no tiene resultado"
    ref0 = filas_referencia[0]
    primera = list(filas[0])
    if d.tipo == nec.ESCALAR:
        v = ref0[d.columna_valor]
        ok = any(_es_numero(x) and _cerca(float(x), float(v)) for x in primera)
        return ok, f"valor de referencia {v} {'presente' if ok else 'ausente'} en la primera fila"
    if d.tipo == nec.UNICO:
        nombre, v = _norm(ref0[d.columna_nombre]), ref0[d.columna_valor]
        ok_nombre = any(isinstance(x, str) and _norm(x) == nombre for x in primera)
        cifras = [x for x in primera if _es_numero(x)]
        ok_valor = any(_cerca(float(x), float(v)) for x in cifras)
        if not cifras:
            # La pregunta pide el nombre ("¿cuál...?"): sin cifras en la fila, basta el nombre.
            return ok_nombre, f"primera fila: nombre {'sí' if ok_nombre else 'no'} ({ref0[d.columna_nombre]}), sin cifras"
        return ok_nombre and ok_valor, (f"primera fila: nombre {'sí' if ok_nombre else 'no'} "
                                        f"({ref0[d.columna_nombre]}), cifra {'sí' if ok_valor else 'no'} ({v})")
    referencia = {_norm(f[d.columna_nombre]) for f in filas_referencia}
    if d.filas_esperadas is not None:
        k = d.filas_esperadas
        obtenidos = {_norm(x) for fila in filas[:k] for x in fila if isinstance(x, str)}
        comunes = len(referencia & obtenidos)
        return (comunes >= MIN_COINCIDENCIA_LISTA * len(referencia),
                f"{comunes}/{len(referencia)} nombres del top coinciden")
    m = min(len(filas_referencia), len(filas))
    nombres = [next((_norm(x) for x in fila if isinstance(x, str)), None) for fila in filas[:m]]
    comunes = sum(1 for nombre in nombres if nombre in referencia)
    return (m >= MIN_FILAS_LISTA_ABIERTA and comunes >= MIN_COINCIDENCIA_LISTA * m,
            f"{comunes}/{m} de los primeros nombres del intento están en el top {len(filas_referencia)} de la referencia")
