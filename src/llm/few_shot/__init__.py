"""Fase 30: técnica «few-shot» con necesidades negativas.

Una necesidad negativa es una pregunta MUY parecida a una de las 10 necesidades
(misma estructura o misma trampa) pero con otra métrica, dimensión o filtro, de
modo que su respuesta es distinta. Las escribió y resolvió un modelo grande, y
se verificaron contra la BD real (`scripts/verificar_few_shot.py`). Viven en
`ejemplos.json`, junto a este módulo.

Cuando la técnica activa es «few_shot», `contexto_few_shot(pregunta)` busca los
ejemplos más parecidos a la pregunta (TF-IDF coseno sobre la pregunta y las
palabras clave de cada ejemplo, en Python puro) y arma un bloque de texto con
su pregunta, SQL y patrón. Ese bloque va al prompt de sistema de la llamada de
SQL, después del esquema y las reglas de negocio y antes de las lecciones de la
bitácora (`prompts_instacart._sistema_sql`). Si ningún ejemplo supera
`UMBRAL_SIMILITUD`, no se inyecta nada y la pregunta se comporta como «base».

El resumen del resultado de cada ejemplo NO va al prompt: solo sirve para
auditar (y para mostrarlo en la UI).
"""
from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

__all__ = [
    "RUTA_EJEMPLOS", "K_DEFAULT", "UMBRAL_SIMILITUD", "Ejemplo", "ContextoFewShot",
    "cargar_ejemplos", "ejemplos", "normalizar", "recuperar", "formatear_bloque", "contexto_few_shot", "k_efectivo",
]

RUTA_EJEMPLOS = Path(__file__).with_name("ejemplos.json")

#: Ejemplos por pregunta (override con la variable de entorno VENTIA_FEWSHOT_K).
K_DEFAULT = 2
#: Similitud coseno mínima para inyectar un ejemplo. Calibrada (Fase 30) con
#: las 10 preguntas de las necesidades, cuya hermana sale top-1 con 0.45 a 0.85,
#: y con preguntas libres: las que solo comparten palabras sueltas («pedidos»,
#: «producto») quedan entre 0.14 y 0.35. Ver `docs/FEW_SHOT.md`. Debajo del
#: umbral la técnica no inyecta nada.
UMBRAL_SIMILITUD = 0.4

CAMPOS_OBLIGATORIOS: dict[str, type | tuple[type, ...]] = {
    "id": str, "necesidad_origen": str, "pregunta": str, "palabras_clave": list, "sql": str, "patron": str,
}
CAMPOS_VERIFICACION: dict[str, type | tuple[type, ...]] = {
    "resumen_resultado": list, "tipo": str, "modelo_grande": str, "verificada": bool, "fecha": str,
}

ENCABEZADO_BLOQUE = ("EJEMPLOS RESUELTOS DE PREGUNTAS PARECIDAS (son de OTRAS preguntas: no copies su resultado "
                     "ni sus filtros; adapta el patrón a la pregunta del usuario)")


@dataclass(frozen=True, eq=False)  # hash por identidad: el índice se cachea por catálogo
class Ejemplo:
    id: str
    necesidad_origen: str
    pregunta: str
    palabras_clave: tuple[str, ...]
    sql: str
    patron: str
    resumen_resultado: tuple[dict, ...] = ()
    tipo: str = "negativa"
    modelo_grande: str = ""
    verificada: bool = False
    fecha: str = ""


@dataclass
class ContextoFewShot:
    """Lo que la técnica few-shot agregó al prompt de una pregunta."""
    bloque: str | None = None
    #: (id del ejemplo, similitud) de cada ejemplo inyectado, de mayor a menor.
    usados: list[tuple[str, float]] = field(default_factory=list)


def cargar_ejemplos(crudos: list[dict] | None = None, *, exigir_verificacion: bool = True) -> list[Ejemplo]:
    """Valida el esquema (campos y tipos), que los ids sean únicos y, con
    `exigir_verificacion`, que cada ejemplo esté verificado contra la BD real."""
    if crudos is None:
        crudos = json.loads(RUTA_EJEMPLOS.read_text(encoding="utf-8"))
    if not isinstance(crudos, list) or not crudos:
        raise ValueError(f"{RUTA_EJEMPLOS.name} debe ser una lista no vacía de ejemplos")
    campos = dict(CAMPOS_OBLIGATORIOS, **(CAMPOS_VERIFICACION if exigir_verificacion else {}))
    vistos: set[str] = set()
    salida = []
    for i, ej in enumerate(crudos):
        for nombre, tipo in campos.items():
            if not isinstance(ej.get(nombre), tipo):
                raise ValueError(f"Ejemplo #{i} ({ej.get('id')}): falta '{nombre}' o no es {tipo}")
        if ej["id"] in vistos:
            raise ValueError(f"id repetido en {RUTA_EJEMPLOS.name}: {ej['id']}")
        vistos.add(ej["id"])
        if not re.fullmatch(r"N\d+", ej["necesidad_origen"]) or not ej["id"].startswith(ej["necesidad_origen"] + "-"):
            raise ValueError(f"{ej['id']}: necesidad_origen inválida ({ej['necesidad_origen']})")
        if exigir_verificacion and (not ej["verificada"] or ej["tipo"] != "negativa" or not ej["resumen_resultado"]):
            raise ValueError(f"{ej['id']}: no está verificado contra la BD (corre scripts/verificar_few_shot.py)")
        salida.append(Ejemplo(
            id=ej["id"], necesidad_origen=ej["necesidad_origen"], pregunta=ej["pregunta"],
            palabras_clave=tuple(ej["palabras_clave"]), sql=ej["sql"], patron=ej["patron"],
            resumen_resultado=tuple(ej.get("resumen_resultado") or ()), tipo=ej.get("tipo", "negativa"),
            modelo_grande=ej.get("modelo_grande", ""), verificada=bool(ej.get("verificada")),
            fecha=ej.get("fecha", ""),
        ))
    return salida


@lru_cache(maxsize=1)
def ejemplos() -> tuple[Ejemplo, ...]:
    return tuple(cargar_ejemplos())


# ---------------------------------------------------------------------------
# Similitud léxica: TF-IDF coseno en Python puro
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset("""
a al algo como con cual cuales cuanto cuantos cuanta cuantas de del donde e el ella ellos en entre es esta este
esto estos frente hay la las le les lo los mas mi muy no o para pero por que quien quienes se sea segun ser si sin
sobre son su sus tiene tienen todo todos tu un una unas uno unos y ya
""".split())


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalizar(texto: str) -> list[str]:
    """Minúsculas, sin acentos, sin stopwords y con plurales simples
    («pedidos» -> «pedido»). Conserva los guiones bajos (user_id)."""
    tokens = re.findall(r"[a-z0-9_%]+", _sin_acentos(texto.casefold()))
    salida = []
    for t in tokens:
        if t in _STOPWORDS or len(t) < 2:  # «1» de «1,000» no dice nada del tema
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        salida.append(t)
    return salida


def _texto_indexable(ej: Ejemplo) -> str:
    return " ".join((ej.pregunta, *ej.palabras_clave))


@lru_cache(maxsize=8)
def _indice(ejs: tuple[Ejemplo, ...]) -> tuple[dict[str, float], tuple[dict[str, float], ...]]:
    # TF binario: repetir una palabra en `palabras_clave` no le da más peso
    # (con TF crudo, «pedido» ×4 hacía que cualquier pregunta sobre pedidos cayera ahí).
    docs = [Counter(set(normalizar(_texto_indexable(e)))) for e in ejs]
    n = len(docs)
    df = Counter(t for d in docs for t in d)
    idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
    vectores = tuple(_vector(d, idf) for d in docs)
    return idf, vectores


def _vector(tf: Counter, idf: dict[str, float]) -> dict[str, float]:
    v = {t: c * idf[t] for t, c in tf.items() if t in idf}
    norma = math.sqrt(sum(x * x for x in v.values()))
    return {t: x / norma for t, x in v.items()} if norma else {}


def k_efectivo(k: int | None = None) -> int:
    if k is not None:
        return k
    try:
        return max(0, int(os.environ.get("VENTIA_FEWSHOT_K", K_DEFAULT)))
    except ValueError:
        return K_DEFAULT


def recuperar(
    pregunta: str, *, k: int | None = None, umbral: float = UMBRAL_SIMILITUD, catalogo: tuple[Ejemplo, ...] | None = None,
) -> list[tuple[Ejemplo, float]]:
    """Los k ejemplos más parecidos a `pregunta` con similitud >= `umbral`
    (de mayor a menor; empates por id). Lista vacía si ninguno lo supera."""
    ejs = catalogo if catalogo is not None else ejemplos()
    idf, vectores = _indice(ejs)
    q = _vector(Counter(set(normalizar(pregunta))), idf)
    if not q:
        return []
    puntajes = [(e, sum(peso * v.get(t, 0.0) for t, peso in q.items())) for e, v in zip(ejs, vectores)]
    puntajes = [(e, s) for e, s in puntajes if s >= umbral]
    puntajes.sort(key=lambda par: (-par[1], par[0].id))
    return puntajes[:k_efectivo(k)]


def formatear_bloque(recuperados: list[tuple[Ejemplo, float]]) -> str | None:
    """Bloque para el prompt: pregunta, SQL y patrón de cada ejemplo (sin su
    resultado). None si no hay ejemplos."""
    if not recuperados:
        return None
    partes = [ENCABEZADO_BLOQUE + ":"]
    for i, (e, _s) in enumerate(recuperados, 1):
        partes.append(f"Ejemplo {i}. Pregunta: {e.pregunta}\nSQL: {e.sql}\nPatrón: {e.patron}")
    return "\n\n".join(partes)


def contexto_few_shot(pregunta: str, *, k: int | None = None) -> ContextoFewShot:
    recuperados = recuperar(pregunta, k=k)
    return ContextoFewShot(
        bloque=formatear_bloque(recuperados),
        usados=[(e.id, round(s, 3)) for e, s in recuperados],
    )
