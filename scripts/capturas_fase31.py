"""Fase 31: capturas de evidencia con Playwright (1440 px, página completa).

Requiere la app corriendo (por defecto en http://localhost:8501), arrancada con
VENTIA_LLM_MODELO_INSTACART=llama3.1:8b para que el modelo del chat sea el 8B.

    python -m scripts.capturas_fase31 --salida docs/evidencia/fase31/capturas

1. 01_botones.png: los 10 botones de acceso rápido.
2. 02_n2_boton_3_iteraciones.png: N2 lanzada con botón, 3 iteraciones en
   autocorrección, con la línea de tiempo y el veredicto final.
3. 03_pregunta_libre_sql_sano.png: una pregunta libre con su evaluación.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ESPERA_LLM_MS = 600_000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--salida", default="docs/evidencia/fase31/capturas")
    parser.add_argument("--pregunta", default="¿Cuántos pedidos hay en total?")
    args = parser.parse_args()
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pagina = nav.new_page(viewport={"width": 1440, "height": 900})
        pagina.goto(args.url)
        pagina.get_by_text("Las 10 necesidades").wait_for(timeout=60_000)
        pagina.wait_for_timeout(1500)
        pagina.screenshot(path=str(salida / "01_botones.png"), full_page=True)
        print("01 ok")

        # ⚙️ -> 3 iteraciones, modo autocorrección (el default).
        pagina.get_by_role("button", name="⚙️").click()
        campo = pagina.get_by_label("Cantidad de iteraciones")
        campo.fill("3")
        campo.press("Enter")
        pagina.wait_for_timeout(1500)
        pagina.keyboard.press("Escape")
        pagina.wait_for_timeout(1000)
        pagina.get_by_text("3 intento(s) en modo").wait_for(timeout=30_000)

        pagina.get_by_role("button", name="N2 · ").click()
        pagina.get_by_text("Resultado final:").first.wait_for(timeout=ESPERA_LLM_MS)
        pagina.wait_for_timeout(2000)
        pagina.screenshot(path=str(salida / "02_n2_boton_3_iteraciones.png"), full_page=True)
        print("02 ok")

        pagina.get_by_placeholder("Escribe tu pregunta").fill(args.pregunta)
        pagina.keyboard.press("Enter")
        pagina.get_by_text("Detalles técnicos").last.wait_for(timeout=ESPERA_LLM_MS)
        pagina.wait_for_timeout(3000)
        pagina.get_by_text("Detalles técnicos").last.click()
        pagina.get_by_text("Sin respuesta de referencia").last.wait_for(timeout=30_000)
        pagina.wait_for_timeout(1500)
        pagina.screenshot(path=str(salida / "03_pregunta_libre_sql_sano.png"), full_page=True)
        print("03 ok")
        nav.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
