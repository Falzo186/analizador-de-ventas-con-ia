"""Corre el banco de preguntas y produce un reporte de precisión.

Para cada caso:
  1. ejecuta `sql_referencia` -> la VERDAD
  2. llama a `responder_pregunta(pregunta)` -> lo que dice el MODELO
     (esto incluye: SQL del modelo, su ejecución, y la respuesta en español)
  3. compara (ver src/evaluator/comparar.py) y clasifica el resultado

Clasificación de cada caso:
  acierto              -> el modelo llegó al número/etiqueta correcto
  fallo "sql"          -> el SQL del modelo devolvió datos distintos a la verdad
  fallo "redaccion"    -> el SQL estaba bien pero la frase final perdió/cambió el dato
  fallo "sql_invalido" -> el SQL del modelo ni siquiera se pudo ejecutar
  fallo "sql_inseguro" -> el SQL del modelo fue bloqueado por seguridad
  fallo "sin_sql"      -> no se generó SQL (típicamente Ollama apagado)
  fallo "redaccion_fallida" -> la 2a llamada al modelo falló

CLI:
  python -m src.evaluator.evaluador
  python -m src.evaluator.evaluador --modelo qwen2.5-coder:latest
  python -m src.evaluator.evaluador --modelo llama3.1:8b --salida docs/reporte_evaluacion.md
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

from src.db.database import crear_base_de_datos, engine
from src.evaluator.banco import BANCO, MODO_TEXTO, Caso
from src.evaluator.comparar import (
    _tolerancia,
    coincide_numerico,
    coincide_texto,
    expresa_vacio_o_cero,
    numeros_de_filas,
    numeros_de_texto,
    textos_de_filas,
)

ETIQUETAS_FALLO = {
    "sql": "SQL mal generado",
    "redaccion": "redacción final",
    "sql_invalido": "SQL no ejecutable",
    "sql_inseguro": "SQL bloqueado por seguridad",
    "sin_sql": "no se generó SQL (¿Ollama apagado?)",
    "redaccion_fallida": "falló la 2a llamada al modelo",
}


@dataclass
class ResultadoCaso:
    caso: Caso
    verdad_filas: list = field(default_factory=list)
    sql_modelo: str | None = None
    filas_modelo: list | None = None
    respuesta_modelo: str | None = None
    acierto: bool = False
    motivo_fallo: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Ejecución de un caso
# ---------------------------------------------------------------------------

def _ejecutar(sql: str) -> list[tuple]:
    from sqlalchemy import text

    with engine.connect() as conn:
        return [tuple(f) for f in conn.execute(text(sql)).fetchall()]


def _mismos_numeros(a: list[float], b: list[float]) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(sorted(a), sorted(b)):
        if abs(x - y) > _tolerancia(x):
            return False
    return True


def evaluar_caso(caso: Caso) -> ResultadoCaso:
    # import local: así, al importar el banco para tests puros, no exigimos ollama
    from src.llm.responder import responder_pregunta

    r = ResultadoCaso(caso=caso)
    r.verdad_filas = _ejecutar(caso.sql_referencia)

    res = responder_pregunta(caso.pregunta)
    r.sql_modelo = res.sql
    r.filas_modelo = res.filas
    r.respuesta_modelo = res.respuesta
    r.error = res.error

    # --- casos donde no hay ni resultado del modelo ---
    if res.error and "seguridad" in res.error.lower():
        r.motivo_fallo = "sql_inseguro"
        return r
    if res.sql is None:
        r.motivo_fallo = "sin_sql"
        return r
    if res.filas is None:
        r.motivo_fallo = "sql_invalido"
        return r

    # --- tenemos resultado del modelo: ¿coincide con la verdad? ---
    verdad_nums = numeros_de_filas(r.verdad_filas)
    modelo_nums = numeros_de_filas(r.filas_modelo)
    verdad_vacia = not verdad_nums or all(n == 0 for n in verdad_nums)

    if caso.modo == MODO_TEXTO:
        if not r.verdad_filas:
            # La verdad no trae ninguna fila (p. ej. "qué clientes no
            # compraron..." y la respuesta es "ninguno"): el modelo debe
            # traer igualmente 0 filas, no inventar nombres.
            sql_ok = not r.filas_modelo
        else:
            esperado = set(textos_de_filas(r.verdad_filas))
            obtenido = set(textos_de_filas(r.filas_modelo))
            sql_ok = bool(esperado) and esperado.issubset(obtenido)
    elif verdad_vacia:
        # la verdad es NULL / 0: el modelo también debe traer NULL / 0
        sql_ok = not modelo_nums or all(abs(n) < 0.5 for n in modelo_nums)
    else:
        sql_ok = _mismos_numeros(verdad_nums, modelo_nums)

    if res.respuesta is None:
        r.motivo_fallo = "redaccion_fallida" if sql_ok else "sql"
        return r

    if caso.modo == MODO_TEXTO:
        if not r.verdad_filas:
            resp_ok = expresa_vacio_o_cero(res.respuesta)
        else:
            resp_ok = coincide_texto(textos_de_filas(r.verdad_filas), res.respuesta)
        # Para preguntas de ETIQUETA lo que cuenta es que la frase final nombre
        # lo correcto. El SQL de referencia puede devolver la etiqueta con otro
        # formato ('2026-08' vs 'agosto', una columna extra, un GROUP BY en vez
        # de un CASE...) y no por eso el modelo se equivocó.
        if resp_ok:
            r.acierto = True
        else:
            r.motivo_fallo = "redaccion" if sql_ok else "sql"
        return r

    if verdad_vacia:
        resp_ok = expresa_vacio_o_cero(res.respuesta) or 0.0 in numeros_de_texto(res.respuesta)
    else:
        resp_ok = coincide_numerico(verdad_nums, numeros_de_texto(res.respuesta))

    if sql_ok and resp_ok:
        r.acierto = True
    elif not sql_ok:
        r.motivo_fallo = "sql"
    else:
        r.motivo_fallo = "redaccion"
    return r


def evaluar_banco(
    casos: list[Caso] | None = None,
    progreso: bool = True,
    on_caso=None,
) -> list[ResultadoCaso]:
    """Evalúa todos los casos.

    `on_caso(i, total, resultado)` se llama tras cada caso: lo usa la UI para
    mover una barra de progreso.
    """
    casos = list(casos if casos is not None else BANCO)
    resultados: list[ResultadoCaso] = []
    for i, caso in enumerate(casos, 1):
        if progreso:
            print(f"[{i:>2}/{len(casos)}] {caso.pregunta[:70]}", flush=True)
        r = evaluar_caso(caso)
        if progreso:
            marca = "OK " if r.acierto else "x  "
            extra = "" if r.acierto else f"({ETIQUETAS_FALLO.get(r.motivo_fallo, r.motivo_fallo)})"
            print(f"        -> {marca}{extra}", flush=True)
        resultados.append(r)
        if on_caso is not None:
            on_caso(i, len(casos), r)
    return resultados


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------

def _fmt_filas(filas) -> str:
    if not filas:
        return "(vacío / NULL)"
    if len(filas) == 1 and len(filas[0]) == 1:
        return str(filas[0][0])
    return "; ".join("(" + ", ".join(str(v) for v in f) + ")" for f in filas)


def _pct(aciertos: int, total: int) -> str:
    return f"{100 * aciertos / total:.1f}%" if total else "-"


def resumen_por_categoria(resultados: list[ResultadoCaso]) -> dict[str, tuple[int, int]]:
    tabla: dict[str, list[int]] = {}
    for r in resultados:
        cat = r.caso.categoria_pregunta
        tabla.setdefault(cat, [0, 0])
        tabla[cat][1] += 1
        if r.acierto:
            tabla[cat][0] += 1
    return {k: (v[0], v[1]) for k, v in sorted(tabla.items())}


def construir_reporte_md(resultados: list[ResultadoCaso], modelo: str, segundos: float) -> str:
    total = len(resultados)
    aciertos = sum(r.acierto for r in resultados)

    L: list[str] = []
    L.append("# Reporte de evaluación\n")
    L.append(f"- **Modelo:** `{modelo}`")
    L.append(f"- **Fecha:** {datetime.now():%Y-%m-%d %H:%M}")
    L.append(f"- **Duración:** {segundos:.0f} s")
    L.append(f"- **Precisión global:** {aciertos}/{total} ({_pct(aciertos, total)})\n")

    L.append("## Precisión por tipo de pregunta\n")
    L.append("| Tipo | Aciertos | Total | Precisión |")
    L.append("|---|---|---|---|")
    for cat, (ok, tot) in resumen_por_categoria(resultados).items():
        L.append(f"| {cat} | {ok} | {tot} | {_pct(ok, tot)} |")
    L.append("")

    motivos: dict[str, int] = {}
    for r in resultados:
        if not r.acierto:
            motivos[r.motivo_fallo] = motivos.get(r.motivo_fallo, 0) + 1
    if motivos:
        L.append("## Motivos de fallo\n")
        L.append("| Motivo | Casos |")
        L.append("|---|---|")
        for m, n in sorted(motivos.items(), key=lambda x: -x[1]):
            L.append(f"| {ETIQUETAS_FALLO.get(m, m)} | {n} |")
        L.append("")

    L.append("## Detalle por caso\n")
    L.append("| # | Tipo | Pregunta | Acierto | Motivo | Verdad | Respuesta del modelo |")
    L.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(resultados, 1):
        motivo = "" if r.acierto else ETIQUETAS_FALLO.get(r.motivo_fallo, r.motivo_fallo or "")
        resp = (r.respuesta_modelo or r.error or "").replace("\n", " ").replace("|", "/")
        if len(resp) > 90:
            resp = resp[:87] + "..."
        preg = r.caso.pregunta.replace("|", "/")
        L.append(
            f"| {i} | {r.caso.categoria_pregunta} | {preg} | "
            f"{'sí' if r.acierto else 'no'} | {motivo} | "
            f"{_fmt_filas(r.verdad_filas)} | {resp} |"
        )
    L.append("")

    L.append("## SQL de referencia vs. SQL del modelo\n")
    for i, r in enumerate(resultados, 1):
        L.append(f"### {i}. {r.caso.pregunta}")
        if r.caso.nota:
            L.append(f"> _{r.caso.nota}_\n")
        L.append("```sql")
        L.append(f"-- referencia\n{r.caso.sql_referencia}")
        L.append(f"\n-- modelo\n{r.sql_modelo or '(no generado)'}")
        L.append("```\n")

    L.append("---\n")
    L.append(
        "**Cómo se decide un acierto:** no se compara el SQL carácter por "
        "carácter (hay muchas formas válidas de escribir la misma consulta). "
        "Se ejecutan ambos SQL y se comparan los NÚMEROS del resultado "
        "(exacto para conteos; 1% de tolerancia para dinero y promedios). "
        "Luego se verifica que esos números aparezcan en la frase final del "
        "modelo. Si el resultado coincide pero la frase no, el fallo se "
        "atribuye a la redacción; si el resultado ya difería, al SQL. "
        "Ver `docs/arquitectura.md`."
    )
    return "\n".join(L)


def construir_resumen_consola(resultados: list[ResultadoCaso], modelo: str) -> str:
    total = len(resultados)
    aciertos = sum(r.acierto for r in resultados)
    L = [
        "",
        "=" * 60,
        f"  Modelo: {modelo}",
        f"  Precisión global: {aciertos}/{total} ({_pct(aciertos, total)})",
        "-" * 60,
    ]
    for cat, (ok, tot) in resumen_por_categoria(resultados).items():
        L.append(f"  {cat:<18} {ok}/{tot:<3} {_pct(ok, tot)}")
    L.append("=" * 60)
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.evaluator.evaluador",
        description="Corre el banco de preguntas y genera un reporte de precisión.",
    )
    parser.add_argument("--modelo", default=None,
                        help="Modelo de Ollama a evaluar (ej. qwen2.5-coder:latest). "
                             "Por defecto usa VENTIA_LLM_MODELO o llama3.1:8b.")
    parser.add_argument("--salida", default="docs/reporte_evaluacion.md",
                        help="Ruta del reporte markdown a generar")
    parser.add_argument("--limite", type=int, default=None,
                        help="Correr solo los primeros N casos (para pruebas rápidas)")
    args = parser.parse_args(argv)

    if args.modelo:
        os.environ["VENTIA_LLM_MODELO"] = args.modelo

    from src.llm.client import modelo_actual

    modelo = modelo_actual()
    crear_base_de_datos()

    casos = BANCO[: args.limite] if args.limite else BANCO
    print(f"Evaluando '{modelo}' con {len(casos)} casos...\n")

    inicio = time.monotonic()
    resultados = evaluar_banco(casos)
    segundos = time.monotonic() - inicio

    print(construir_resumen_consola(resultados, modelo))

    reporte = construir_reporte_md(resultados, modelo, segundos)
    salida = args.salida
    with open(salida, "w", encoding="utf-8") as fh:
        fh.write(reporte)
    print(f"\nReporte guardado en {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
