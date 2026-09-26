"""Fase 10 (entregable de curso): la respuesta de la IA local a la pregunta
fija de cada necesidad seleccionada, empaquetada en un JSON por necesidad.

No es un camino nuevo: `generar_entrega_ia()` llama a
`responder_pregunta_instacart()` tal cual (mismas guardas de SQL, timeout,
reintento y validación de cifras que el chat), solo cambia la forma de la
salida. Tampoco compara contra la referencia determinista (fuera de alcance):
es formato de entrega, no evaluación de exactitud.

La pregunta NO se reescribe a mano: es la constante `PREGUNTA` del módulo de
`src.analytics`, la misma que cada `calcular()` pone en `r.pregunta`.

Estructura fija de cada JSON (`CAMPOS_ENTREGA`, en este orden):

    {"necesidad", "pregunta", "sql_generado", "resultado", "respuesta_llm",
     "modelo", "timestamp"}

Solo se empaqueta una respuesta completa (`res.ok`). Si el pipeline falla
(Ollama apagado, SQL rechazado, timeout...), no hay JSON: la UI muestra el
error, igual que el chat.

CLI (modelo real):
  python -m src.llm.formato_entrega N1 --modelo qwen2.5-coder:1.5b
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.analytics import n1_recompra, n2_canasta, n4_frecuencia, n9_tamano, n11_prediccion
from src.dashboard.servicio import NECESIDADES_ENTREGA
from src.llm import client
from src.llm import responder_instacart as ri

CAMPOS_ENTREGA: tuple[str, ...] = (
    "necesidad", "pregunta", "sql_generado", "resultado", "respuesta_llm", "modelo", "timestamp",
)

#: Pregunta fija de cada necesidad seleccionada, importada de src.analytics.
PREGUNTAS_ENTREGA: dict[str, str] = {
    m.ID: m.PREGUNTA for m in (n1_recompra, n2_canasta, n4_frecuencia, n9_tamano, n11_prediccion)
}
assert tuple(PREGUNTAS_ENTREGA) == NECESIDADES_ENTREGA


@dataclass
class EntregaIA:
    necesidad: str
    #: El resultado completo del pipeline (para mostrar el error si falló).
    res: ri.ResultadoInstacart
    #: El JSON de entrega, o None si el pipeline no terminó bien.
    documento: dict | None

    @property
    def ok(self) -> bool:
        return self.documento is not None


def _valor_json(v: object) -> object:
    """Valor de una celda de DuckDB -> valor serializable en JSON. NaN/Inf
    pasan a null (igual que `contrato._sanear`); fechas, Decimal y demás
    tipos no nativos pasan a texto."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    return str(v)


def empaquetar(necesidad: str, res: ri.ResultadoInstacart, modelo: str, timestamp: str) -> dict:
    """Arma el JSON de entrega a partir de un `ResultadoInstacart` completo."""
    if not res.ok:
        raise ValueError(f"No se empaqueta una respuesta con error: {res.error}")
    return {
        "necesidad": necesidad,
        "pregunta": PREGUNTAS_ENTREGA[necesidad],
        "sql_generado": res.sql,
        "resultado": [
            {col: _valor_json(v) for col, v in zip(res.columnas or [], fila)} for fila in res.filas or []
        ],
        "respuesta_llm": res.respuesta,
        "modelo": modelo,
        "timestamp": timestamp,
    }


def generar_entrega_ia(
    necesidad: str,
    *,
    modelo: str | None = None,
    timeout_sql_llm_s: float = ri.TIMEOUT_LLM_SQL_S,
    timeout_respuesta_llm_s: float = ri.TIMEOUT_LLM_RESPUESTA_S,
    db_path: Path | None = None,
) -> EntregaIA:
    """Corre el pipeline del chat con la pregunta fija de `necesidad` y
    empaqueta la salida. Nunca lanza por un fallo del pipeline: si
    `res.ok` es falso, devuelve `documento=None` con el error en `res`."""
    if necesidad not in PREGUNTAS_ENTREGA:
        raise ValueError(f"{necesidad} no es una de las necesidades de la entrega: {NECESIDADES_ENTREGA}")
    modelo = modelo or client.modelo_actual_instacart()
    res = ri.responder_pregunta_instacart(
        PREGUNTAS_ENTREGA[necesidad],
        db_path=db_path if db_path is not None else ri.DB_PATH_DEFAULT,
        redactar=True,
        modelo=modelo,
        timeout_sql_llm_s=timeout_sql_llm_s,
        timeout_respuesta_llm_s=timeout_respuesta_llm_s,
    )
    if not res.ok:
        return EntregaIA(necesidad=necesidad, res=res, documento=None)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    return EntregaIA(necesidad=necesidad, res=res, documento=empaquetar(necesidad, res, modelo, timestamp))


def a_json(documento: dict) -> str:
    """Serialización del JSON de entrega (orden de campos fijo, UTF-8 legible)."""
    return json.dumps(documento, indent=2, ensure_ascii=False) + "\n"


def nombre_archivo(necesidad: str) -> str:
    return f"{necesidad}_ia.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.llm.formato_entrega",
        description="JSON de la IA local para una de las necesidades de la entrega.",
    )
    parser.add_argument("necesidad", choices=NECESIDADES_ENTREGA)
    parser.add_argument("--modelo", default=None)
    parser.add_argument("--timeout", type=float, default=ri.TIMEOUT_LLM_SQL_S,
                        help="Timeout (s) de cada llamada al modelo")
    parser.add_argument("--out", default=None, help="Carpeta donde guardar {necesidad}_ia.json")
    args = parser.parse_args(argv)

    entrega = generar_entrega_ia(
        args.necesidad, modelo=args.modelo, timeout_sql_llm_s=args.timeout, timeout_respuesta_llm_s=args.timeout,
    )
    if not entrega.ok:
        print(f"ERROR: {entrega.res.error}", file=sys.stderr)
        if entrega.res.sql:
            print(f"SQL:   {entrega.res.sql}", file=sys.stderr)
        return 1
    texto = a_json(entrega.documento)
    if args.out:
        ruta = Path(args.out) / nombre_archivo(args.necesidad)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(texto, encoding="utf-8")
    print(texto, end="")
    if entrega.res.origen_respuesta != "modelo":
        print(f"# respuesta_llm de: {entrega.res.origen_respuesta} "
              f"(cifras rechazadas: {entrega.res.cifras_rechazadas})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
