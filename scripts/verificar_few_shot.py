"""Fase 30: verifica las «necesidades negativas» de `src/llm/few_shot/ejemplos.json`
contra la BD real, con la misma guarda y conexión read_only que el chat.

Por cada ejemplo:
  1. `validar_sql` (una sola sentencia que empieza con SELECT) y
     `ejecutar_con_guardas` con el límite de su necesidad de origen (10 s;
     25 s en la familia de N9);
  2. que devuelva filas;
  3. que su resultado NO coincida con el de referencia de su necesidad de
     origen (`data/referencia/Nx.json`): ni según el comparador automático
     (`retroalimentacion.coincide_con_referencia`) ni porque la fila top-1
     contenga un valor clave de esa necesidad.

Con `--escribir`, guarda en el JSON el resumen REAL (primeras filas), la fecha
y `verificada: true`. Sin `--escribir`, solo comprueba (sale con código 1 si
algo falla).

    python -m scripts.verificar_few_shot [--escribir] [--modelo-grande claude-opus-5-5]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date
from pathlib import Path

from src.llm import retroalimentacion as rf
from src.llm.few_shot import RUTA_EJEMPLOS, cargar_ejemplos
from src.llm.iteraciones_juez import timeout_consulta
from src.llm.safety_instacart import abrir_conexion_solo_lectura, ejecutar_con_guardas, validar_sql

DB_PATH = Path("data/instacart.duckdb")
REFERENCIA_DIR = Path("data/referencia")
FILAS_RESUMEN = 3
CAMPOS_ORDEN = ("id", "necesidad_origen", "pregunta", "palabras_clave", "sql", "patron", "resumen_resultado",
                "tipo", "modelo_grande", "verificada", "fecha")


def _corto(v: object) -> object:
    if isinstance(v, float) and math.isfinite(v):
        return round(v, 4)
    return v


def _referencia(nid: str) -> dict:
    return json.loads((REFERENCIA_DIR / f"{nid}.json").read_text(encoding="utf-8"))


def _toca_valor_clave(fila: tuple, valores_clave: list[dict]) -> str | None:
    """Valor clave de la necesidad que aparece en la fila top-1 del ejemplo."""
    for vc in valores_clave:
        v = vc["valor"]
        for celda in fila:
            if isinstance(v, str) and isinstance(celda, str) and rf._clave(celda) == rf._clave(v):
                return str(v)
            if (rf._es_numero(v) and rf._es_numero(celda) and not float(v).is_integer()
                    and rf._cerca(float(celda), float(v))):
                return str(v)
            if rf._es_numero(v) and rf._es_numero(celda) and float(v).is_integer() and abs(v) >= 10 \
                    and float(celda) == float(v):
                return str(v)
    return None


def verificar(ejemplo: dict) -> dict:
    """Ejecuta un ejemplo y devuelve {ok, motivo, segundos, columnas, filas}."""
    nid = ejemplo["necesidad_origen"]
    con = abrir_conexion_solo_lectura(str(DB_PATH))
    try:
        t0 = time.perf_counter()
        columnas, filas = ejecutar_con_guardas(con, validar_sql(ejemplo["sql"]), timeout_s=timeout_consulta(nid))
        segundos = time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001 - se reporta tal cual
        return {"ok": False, "motivo": f"no ejecutó: {exc}", "segundos": None, "columnas": [], "filas": []}
    finally:
        con.close()
    base = {"segundos": segundos, "columnas": columnas, "filas": filas}
    if not filas:
        return {"ok": False, "motivo": "no devolvió filas", **base}
    ref = _referencia(nid)
    coincide, detalle = rf.coincide_con_referencia(nid, ref["tablas"]["respuesta"], filas)
    if coincide:
        return {"ok": False, "motivo": f"coincide con la referencia de {nid}: {detalle}", **base}
    tocado = _toca_valor_clave(filas[0], ref["valores_clave"])
    if tocado:
        return {"ok": False, "motivo": f"la fila top-1 contiene el valor clave de {nid}: {tocado}", **base}
    return {"ok": True, "motivo": "ok", **base}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--escribir", action="store_true", help="Guardar el resumen real en el JSON")
    parser.add_argument("--modelo-grande", default="claude-opus-5-5",
                        help="Modelo grande que escribió y resolvió los ejemplos")
    args = parser.parse_args(argv)

    crudos = json.loads(RUTA_EJEMPLOS.read_text(encoding="utf-8"))
    cargar_ejemplos(crudos, exigir_verificacion=False)  # esquema e ids únicos
    fallas = 0
    for ej in crudos:
        r = verificar(ej)
        seg = f"{r['segundos']:.1f} s" if r["segundos"] is not None else "-"
        top = tuple(_corto(v) for v in r["filas"][0]) if r["filas"] else None
        print(f"{'OK ' if r['ok'] else 'MAL'} {ej['id']:<10} {seg:>7}  top-1 {top}  [{r['motivo']}]")
        if not r["ok"]:
            fallas += 1
            continue
        if args.escribir:
            ej["resumen_resultado"] = [{c: _corto(v) for c, v in zip(r["columnas"], fila)}
                                       for fila in r["filas"][:FILAS_RESUMEN]]
            ej["tipo"] = "negativa"
            ej["modelo_grande"] = args.modelo_grande
            ej["verificada"] = True
            ej["fecha"] = date.today().isoformat()
    if args.escribir and not fallas:
        ordenados = [{k: ej[k] for k in CAMPOS_ORDEN} for ej in crudos]
        RUTA_EJEMPLOS.write_text(json.dumps(ordenados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Escrito: {RUTA_EJEMPLOS}")
    print(f"{len(crudos) - fallas} de {len(crudos)} ejemplos verificados contra la BD real.")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
