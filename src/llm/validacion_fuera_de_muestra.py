"""Fase 30: validación fuera de muestra de la técnica few-shot.

8 preguntas libres que NO son de las 10 necesidades (`tests/data/
preguntas_fuera_de_muestra.json`), cada una con un SQL de referencia verificado
en la BD real. Cada pregunta se responde con «base» y con «few_shot» (mismo
modelo, temperatura 0.0 como el chat, sin redacción: el comparador solo mira
las filas) y se compara con el MISMO comparador automático de las necesidades
(`retroalimentacion.coincide_con_forma`, con un adaptador `FormaRespuesta`).

Responde dos preguntas: (a) ¿few_shot empeora alguna que base acertaba? y
(b) ¿cuántas reciben ejemplos? (las no relacionadas no deberían recibir ninguno
y se comportan igual que base).

    python -m src.llm.validacion_fuera_de_muestra --modelo llama3.1:8b
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from src.analytics import necesidades as nec
from src.llm import responder_instacart as ri
from src.llm import retroalimentacion as rf
from src.llm import tecnicas
from src.llm.safety_instacart import abrir_conexion_solo_lectura, ejecutar_con_guardas, validar_sql

RUTA_PREGUNTAS = Path("tests/data/preguntas_fuera_de_muestra.json")
RUTA_SALIDA = Path("docs/evidencia/fase30/fuera_de_muestra.json")


@dataclass(frozen=True)
class FormaRespuesta:
    """Adaptador: la forma de la respuesta que necesita el comparador."""
    tipo: str
    columna_nombre: str | None
    columna_valor: str
    columnas_nombre_extra: tuple[str, ...] = ()
    columnas_valor_extra: tuple[str, ...] = ()
    filas_esperadas: int | None = 1


def cargar_preguntas(ruta: Path = RUTA_PREGUNTAS) -> list[dict]:
    return json.loads(ruta.read_text(encoding="utf-8"))


def forma_de(p: dict) -> FormaRespuesta:
    tipo = {"unico": nec.UNICO, "escalar": nec.ESCALAR, "lista": nec.LISTA}[p["tipo"]]
    return FormaRespuesta(tipo=tipo, columna_nombre=p.get("columna_nombre"), columna_valor=p["columna_valor"])


def filas_referencia(p: dict, db_path: Path = ri.DB_PATH_DEFAULT) -> list[dict]:
    con = abrir_conexion_solo_lectura(str(db_path))
    try:
        columnas, filas = ejecutar_con_guardas(con, validar_sql(p["sql_referencia"]))
    finally:
        con.close()
    return [dict(zip(columnas, f)) for f in filas]


def calificar(p: dict, referencia: list[dict], filas: list | None) -> tuple[bool, str]:
    """El comparador de siempre y, Fase 30b, `exigir_cifra`: en las preguntas
    donde el identificador solo puede coincidir por casualidad (X8), la primera
    fila debe traer además la cifra de la referencia."""
    acierta, detalle = rf.coincide_con_forma(forma_de(p), referencia, filas)
    if acierta and p.get("exigir_cifra"):
        v = referencia[0][p["columna_valor"]]
        if not any(rf._es_numero(x) and rf._cerca(float(x), float(v)) for x in (filas or [[]])[0]):
            return False, (f"{detalle}; falta la cifra ({v}): con el nombre solo puede coincidir por casualidad "
                           "(exigir_cifra)")
    return acierta, detalle


def evaluar(p: dict, tecnica: str, modelo: str, referencia: list[dict], timeout_s: float) -> dict:
    res = ri.responder_pregunta_instacart(p["pregunta"], redactar=False, modelo=modelo, tecnica=tecnica,
                                          timeout_sql_llm_s=timeout_s, timeout_respuesta_llm_s=timeout_s)
    acierta, detalle = calificar(p, referencia, res.filas) if res.ok else (False, res.error)
    return {"acierta": acierta, "detalle": detalle, "sql": res.sql, "error": res.error,
            "ejemplos_usados": [list(u) for u in res.ejemplos_usados]}


def resumir(filas: list[dict]) -> dict:
    empeoradas = [f["id"] for f in filas if f["base"]["acierta"] and not f["few_shot"]["acierta"]]
    mejoradas = [f["id"] for f in filas if not f["base"]["acierta"] and f["few_shot"]["acierta"]]
    return {
        "preguntas": len(filas),
        "base_aciertos": sum(f["base"]["acierta"] for f in filas),
        "few_shot_aciertos": sum(f["few_shot"]["acierta"] for f in filas),
        "empeoradas_por_few_shot": empeoradas,
        "mejoradas_por_few_shot": mejoradas,
        "reciben_ejemplos": [f["id"] for f in filas if f["few_shot"]["ejemplos_usados"]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.llm.validacion_fuera_de_muestra")
    parser.add_argument("--modelo", default="llama3.1:8b")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    filas = []
    for p in cargar_preguntas():
        ref = filas_referencia(p)
        fila = {"id": p["id"], "pregunta": p["pregunta"], "respuesta_esperada": p["respuesta_esperada"]}
        for t in (tecnicas.BASE, tecnicas.FEW_SHOT):
            fila[t] = evaluar(p, t, args.modelo, ref, args.timeout)
        filas.append(fila)
        print(f"{p['id']} · base {'✓' if fila['base']['acierta'] else '✗'} · few_shot "
              f"{'✓' if fila['few_shot']['acierta'] else '✗'} · ejemplos: "
              f"{fila['few_shot']['ejemplos_usados'] or 'ninguno'} · {p['pregunta']}", flush=True)
    salida = {"modelo": args.modelo, "temperatura": 0.0, "resumen": resumir(filas), "preguntas": filas}
    RUTA_SALIDA.parent.mkdir(parents=True, exist_ok=True)
    RUTA_SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(salida["resumen"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
