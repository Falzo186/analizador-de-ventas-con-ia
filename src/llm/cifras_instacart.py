"""Validación de las CIFRAS de una respuesta redactada por el modelo contra
el resultado tabular real (Fase 6, paso 0.5).

Motivo (docs/FASE5_REPORTE.md, sección 8): la base devolvió 3421083 y
qwen2.5-coder:1.5b escribió "34,210,83". Eso no es un número mal
formateado sino un número ALTERADO, y la regla del prompt ("usa el número
EXACTO") no bastó para evitarlo.

Regla: cada número que aparece en la respuesta debe corresponder a un valor
permitido:
  - una celda numérica del resultado (o un número dentro de una celda de
    texto, p. ej. "2% milk");
  - un número que ya estaba en la pregunta ("product_id = 1", "top 5");
  - la cantidad de filas devueltas.

"Ignorar separadores" NO significa quitar todas las comas y puntos: con esa
regla "34,210,83" se volvería 3421083 y pasaría. Aquí se ignoran solo los
separadores BIEN FORMADOS: grupos de miles de exactamente 3 dígitos
("3,421,083", "3.421.083", "3 421 083" con espacio fino) y una parte
decimal ("4.25", "4,25"). Un token con agrupación inválida ("34,210,83") no
tiene ninguna lectura numérica válida y se rechaza siempre.

Decimales: una cifra con d decimales coincide con un valor v si
round(v, d) == cifra. También se acepta como porcentaje (v * 100), porque
las tasas vienen como fracción (0.593) y el modelo las redacta como "59.3%".
"""

from __future__ import annotations

import math
import re

# Dígitos con separadores internos (coma, punto, espacio duro o fino). Un
# punto o coma FINAL (fin de frase) no entra: exige un dígito después.
_PATRON_TOKEN = re.compile(r"\d+(?:[.,  ]\d+)*")
_SEP_MILES = r"[.,  ]"
_ENTERO_AGRUPADO = re.compile(rf"^\d{{1,3}}(?:({_SEP_MILES})\d{{3}})(?:\1\d{{3}})*$")
_DECIMAL_SIMPLE = re.compile(r"^\d+[.,]\d+$")
_AGRUPADO_CON_DECIMALES = re.compile(r"^\d{1,3}(?:([,.  ])\d{3})(?:\1\d{3})*([.,])\d+$")

#: Cuántas filas como máximo muestra la respuesta de plantilla.
MAX_FILAS_PLANTILLA = 5


def lecturas_numericas(token: str) -> list[tuple[float, int]]:
    """Todas las lecturas numéricas VÁLIDAS de `token`, como pares (valor,
    decimales mostrados). Una lista vacía significa que el token está mal
    formado ("34,210,83")."""
    lecturas: list[tuple[float, int]] = []
    if token.isdigit():
        lecturas.append((float(token), 0))
    if _ENTERO_AGRUPADO.match(token):
        lecturas.append((float(re.sub(_SEP_MILES, "", token)), 0))
    if _DECIMAL_SIMPLE.match(token):
        entero, dec = re.split(r"[.,]", token)
        lecturas.append((float(f"{entero}.{dec}"), len(dec)))
    m = _AGRUPADO_CON_DECIMALES.match(token)
    if m and m.group(1) != m.group(2):  # "1,234.5" o "1.234,5", nunca "1,234,5"
        cuerpo, dec = token[: m.start(2)], token[m.end(2):]
        lecturas.append((float(re.sub(_SEP_MILES, "", cuerpo) + "." + dec), len(dec)))
    return lecturas


def numeros_en_texto(texto: str) -> list[str]:
    return _PATRON_TOKEN.findall(texto or "")


def _es_numero(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def valores_permitidos(pregunta: str, columnas: list[str], filas: list[tuple]) -> list[float]:
    """Valores contra los que se valida una respuesta (ver docstring del módulo)."""
    permitidos: list[float] = [float(len(filas))]
    for fila in filas:
        for celda in fila:
            if _es_numero(celda):
                permitidos.append(float(celda))
            elif celda is not None and not isinstance(celda, bool):
                for token in numeros_en_texto(str(celda)):
                    permitidos.extend(v for v, _d in lecturas_numericas(token))
    for texto in [pregunta, *columnas]:
        for token in numeros_en_texto(texto):
            permitidos.extend(v for v, _d in lecturas_numericas(token))
    return permitidos


def _coincide(cifra: float, decimales: int, permitidos: list[float]) -> bool:
    for v in permitidos:
        for candidato in (v, v * 100):
            if round(candidato, decimales) == cifra:
                return True
    return False


def cifras_invalidas(texto: str, permitidos: list[float]) -> list[str]:
    """Tokens numéricos de `texto` que no corresponden a ningún valor
    permitido, o que están mal formados. Lista vacía = redacción válida."""
    invalidas = []
    for token in numeros_en_texto(texto):
        lecturas = lecturas_numericas(token)
        if not any(_coincide(v, d, permitidos) for v, d in lecturas):
            invalidas.append(token)
    return invalidas


def fmt_valor(v: object) -> str:
    """Formato preformateado de un valor real: separador de miles en
    enteros y 4 cifras significativas como máximo en decimales, sin redondear
    enteros ni inventar precisión."""
    if v is None:
        return "sin dato"
    if isinstance(v, bool):
        return "sí" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return f"{int(v):,}"
        return f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 1 else f"{v:,.2f}"
    return str(v)


def respuesta_plantilla(columnas: list[str], filas: list[tuple]) -> str:
    """Respuesta SIN modelo: inserta los valores reales preformateados. Se
    usa cuando la redacción del modelo alteró una cifra dos veces seguidas."""
    if not filas:
        return "La consulta no devolvió resultados."
    if len(filas) == 1 and len(columnas) == 1:
        return f"Resultado ({columnas[0]}): {fmt_valor(filas[0][0])}."
    lineas = []
    for fila in filas[:MAX_FILAS_PLANTILLA]:
        lineas.append(", ".join(f"{c}: {fmt_valor(v)}" for c, v in zip(columnas, fila)))
    texto = "Resultado: " + "; ".join(lineas) + "."
    if len(filas) > MAX_FILAS_PLANTILLA:
        texto += f" ({len(filas) - MAX_FILAS_PLANTILLA:,} filas más en la tabla.)"
    return texto
