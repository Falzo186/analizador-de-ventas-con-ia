"""Fase 30: tamaño del prompt de SQL, base vs few_shot, en caracteres y en
tokens REALES (`prompt_eval_count` de Ollama, con `num_predict=1`).

Para detectar truncamiento, cada prompt se mide dos veces: con el contexto que
usa el proyecto (el default de Ollama, sin `num_ctx`) y con `num_ctx=16384`
solo para esta medición. Si el primero cuenta menos tokens que el segundo,
Ollama recortó el prompt (empieza a descartar por el inicio: el esquema).

    python -m scripts.medir_prompt_fase30 --modelos llama3.1:8b qwen2.5-coder:latest
"""
from __future__ import annotations

import argparse
import json

import ollama

from src.analytics.necesidades import DEFINICIONES
from src.llm import bitacora_chat, tecnicas
from src.llm.esquema_instacart import construir_descripcion_esquema
from src.llm.prompts_instacart import prompt_sql_instacart
from src.llm.safety_instacart import abrir_conexion_solo_lectura

CTX_GRANDE = 16384


def _tokens(modelo: str, sistema: str, usuario: str, num_ctx: int | None) -> int:
    opciones = {"num_predict": 1, "temperature": 0.0, **({"num_ctx": num_ctx} if num_ctx else {})}
    r = ollama.chat(model=modelo, messages=[{"role": "system", "content": sistema},
                                            {"role": "user", "content": usuario}], options=opciones)
    return int(r["prompt_eval_count"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modelos", nargs="+", default=["llama3.1:8b"])
    parser.add_argument("--necesidades", nargs="+", default=["N2", "N10"])
    args = parser.parse_args()
    con = abrir_conexion_solo_lectura("data/instacart.duckdb")
    esquema = construir_descripcion_esquema(con)
    con.close()
    # Bitácora llena: el peor caso del chat (8 lecciones, hasta 1,900 caracteres).
    lecciones = "\n".join(["LECCIONES APRENDIDAS"] + ["- " + "x" * 200] * bitacora_chat.MAX_LECCIONES_PROMPT)
    lecciones = lecciones[:bitacora_chat.MAX_CARACTERES_BLOQUE]
    filas = []
    for modelo in args.modelos:
        prompts = []
        for nid in args.necesidades:
            pregunta = DEFINICIONES[nid].pregunta
            for tecnica in (tecnicas.BASE, tecnicas.FEW_SHOT):
                bloque = tecnicas.contexto_de_tecnica(tecnica, pregunta).bloque
                for con_bitacora in (False, True):
                    sistema, usuario = prompt_sql_instacart(pregunta, esquema, None, None,
                                                            lecciones if con_bitacora else None, bloque)
                    prompts.append(({"modelo": modelo, "necesidad": nid, "tecnica": tecnica,
                                     "bitacora_llena": con_bitacora, "caracteres": len(sistema) + len(usuario)},
                                    sistema, usuario))
        # Primero todos con el contexto del proyecto (una sola carga del modelo)...
        usados = [_tokens(modelo, s_, u_, None) for _, s_, u_ in prompts]
        ctx = next((m.get("context_length") for m in ollama.ps()["models"] if m["name"] == modelo), None)
        # ...y luego todos con 16k (otra carga), solo para medir el tamaño real.
        reales = [_tokens(modelo, s_, u_, CTX_GRANDE) for _, s_, u_ in prompts]
        for (fila, _, _), u, r in zip(prompts, usados, reales):
            fila.update(contexto_del_proyecto=ctx, tokens_reales=r, tokens_con_contexto_del_proyecto=u,
                        truncado=u < r)
            filas.append(fila)
            print(json.dumps(fila, ensure_ascii=False), flush=True)
        ollama.generate(model=modelo, prompt="", keep_alive=0)  # no dejarlo cargado con 16k
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
