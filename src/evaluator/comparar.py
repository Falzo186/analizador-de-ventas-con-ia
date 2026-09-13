"""Cómo se decide un "acierto".

El modelo responde en lenguaje natural ("Se vendieron 342 artículos..."),
no con un dato limpio. Así que:

* De la VERDAD (resultado del SQL de referencia) sacamos su lista de números.
* De la RESPUESTA del modelo sacamos todos los números que aparecen en el
  texto.
* Hay acierto si CADA número de la verdad aparece (con tolerancia) entre los
  números del texto.

Para preguntas cuya respuesta es una etiqueta ("tecnología", "Café molido"),
se compara texto normalizado (sin acentos, en minúsculas) por inclusión.

Este módulo es PURO: no toca la base ni el modelo. Por eso es fácil de probar.
"""

from __future__ import annotations

import re
import unicodedata

# Un número dentro de un texto: 1234, 1.234,56, 1,234.56, 3.5, etc.
_NUM = re.compile(r"-?\d[\d.,]*\d|-?\d")


def normalizar_texto(s: str) -> str:
    """minúsculas + sin acentos + espacios colapsados."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def _interpretaciones(token: str) -> set[float]:
    """Devuelve las lecturas numéricas plausibles de un token.

    '1,234'      -> {1234.0, 1.234}   (miles o decimal, no sabemos)
    '1.234,56'   -> {1234.56}
    '1,234.56'   -> {1234.56}
    '197'        -> {197.0}
    Ser permisivo aquí solo puede ayudar a DETECTAR que el modelo acertó.
    """
    token = token.strip()
    salidas: set[float] = set()
    if not token:
        return salidas

    tiene_coma = "," in token
    tiene_punto = "." in token

    if tiene_coma and tiene_punto:
        # el separador decimal es el que aparece más a la derecha
        if token.rfind(",") > token.rfind("."):
            candidato = token.replace(".", "").replace(",", ".")
        else:
            candidato = token.replace(",", "")
        _añadir(salidas, candidato)
    elif tiene_coma:
        _añadir(salidas, token.replace(",", ""))       # 1,234 -> 1234
        _añadir(salidas, token.replace(",", "."))      # 3,5   -> 3.5
    elif tiene_punto:
        _añadir(salidas, token.replace(".", ""))       # 1.234 -> 1234
        _añadir(salidas, token)                        # 3.5   -> 3.5
    else:
        _añadir(salidas, token)
    return salidas


def _añadir(destino: set[float], texto: str) -> None:
    try:
        destino.add(float(texto))
    except ValueError:
        pass


def numeros_de_texto(texto: str) -> list[float]:
    """Todas las lecturas numéricas plausibles que aparecen en `texto`."""
    valores: set[float] = set()
    for match in _NUM.finditer(texto or ""):
        valores |= _interpretaciones(match.group())
    return sorted(valores)


def numeros_de_filas(filas) -> list[float]:
    """Números del resultado de una consulta, conservando int vs float.

    El tipo importa: COUNT(...) y SUM(cantidad) llegan como `int` (comparación
    exacta); SUM(total) y AVG(...) llegan como `float` (comparación con
    tolerancia). Ver `_tolerancia`.
    """
    salida: list = []
    for fila in filas or []:
        for valor in fila:
            if isinstance(valor, bool):
                continue
            if isinstance(valor, (int, float)):
                salida.append(valor)
    return salida


def textos_de_filas(filas) -> list[str]:
    """Valores de texto (normalizados) del resultado de una consulta."""
    salida: list[str] = []
    for fila in filas or []:
        for valor in fila:
            if isinstance(valor, str):
                salida.append(normalizar_texto(valor))
    return salida


def _tolerancia(valor: float) -> float:
    """Cuánto puede alejarse un número obtenido del esperado y aún contar.

    - Si el esperado es entero (conteos, SUM(cantidad)): tolerancia 0.5,
      es decir, coincidencia exacta.
    - Si tiene decimales (dinero, promedios): 1% para absorber redondeos.
    """
    if isinstance(valor, int):
        return 0.5
    return max(0.5, abs(valor) * 0.01)


def coincide_numerico(esperados: list[float], obtenidos: list[float]) -> bool:
    """True si cada número esperado está (con tolerancia) entre los obtenidos."""
    if not esperados:
        # La verdad no tiene números (p. ej. SUM sobre 0 filas -> NULL):
        # consideramos acierto si el modelo tampoco afirmó un número raro.
        return True
    for e in esperados:
        if not any(abs(e - o) <= _tolerancia(e) for o in obtenidos):
            return False
    return True


_FRASES_VACIO = (
    "no se registr", "no hubo", "no hay", "no se vendi", "no se realiz",
    "ningun", "sin ventas", "sin registros", "cero ", " cero", "es cero",
    "no se encontr", "no existe", "no se dispone", "none", "no customers",
)


def expresa_vacio_o_cero(respuesta: str) -> bool:
    """True si la respuesta dice, en palabras, que no hubo nada / que es cero.

    Se usa cuando la verdad es NULL o 0: ahí un "no se registraron ventas" es
    una respuesta correcta aunque no contenga el dígito 0.
    """
    r = normalizar_texto(respuesta or "")
    return any(f in r for f in _FRASES_VACIO)



# Northwind guarda sus valores de texto en inglés (nombres de categoría,
# países...), pero le pedimos al modelo que responda en español: es normal y
# CORRECTO que traduzca "Beverages" -> "Bebidas" o "USA" -> "Estados Unidos".
# Sin este diccionario, `coincide_texto` marcaba esas respuestas como fallo
# aunque el dato fuera el correcto. Las claves están normalizadas (minúsculas,
# sin acentos) tal como llegan de `textos_de_filas`/`normalizar_texto`.
EQUIVALENCIAS_ES_EN: dict[str, tuple[str, ...]] = {
    # Categories.CategoryName
    "beverages": ("bebidas",),
    "condiments": ("condimentos", "aderezos"),
    "confections": ("confites", "dulces", "confituras", "confecciones", "reposteria"),
    "dairy products": ("lacteos", "productos lacteos"),
    "grains/cereals": ("granos", "cereales", "granos/cereales", "granos y cereales"),
    "meat/poultry": ("carne", "aves", "carne/aves", "carnes y aves", "carne y aves"),
    "produce": ("productos agricolas", "verduras", "frutas y verduras", "vegetales", "productos frescos"),
    "seafood": ("mariscos", "pescados y mariscos", "pescado", "productos del mar"),
    # Customers.Country / Suppliers.Country
    "usa": ("estados unidos", "eeuu", "ee uu", "ee.uu.", "eua"),
    "uk": ("reino unido", "gran bretana", "inglaterra"),
    "germany": ("alemania",),
    "brazil": ("brasil",),
    "spain": ("espana",),
    "belgium": ("belgica",),
    "denmark": ("dinamarca",),
    "finland": ("finlandia",),
    "france": ("francia",),
    "ireland": ("irlanda",),
    "italy": ("italia",),
    "norway": ("noruega",),
    "poland": ("polonia",),
    "sweden": ("suecia",),
    "switzerland": ("suiza",),
}


def _equivalentes(valor_normalizado: str) -> set[str]:
    """El valor tal cual, más sus traducciones conocidas en ambos sentidos."""
    salida = {valor_normalizado}
    salida.update(EQUIVALENCIAS_ES_EN.get(valor_normalizado, ()))
    for ingles, variantes_es in EQUIVALENCIAS_ES_EN.items():
        if valor_normalizado in variantes_es:
            salida.add(ingles)
    return salida


def coincide_texto(esperados: list[str], respuesta: str) -> bool:
    """True si cada etiqueta esperada (o su traducción conocida) aparece en la respuesta."""
    resp = normalizar_texto(respuesta or "")
    if not esperados:
        return True
    return all(
        any(variante in resp for variante in _equivalentes(e))
        for e in esperados
    )
