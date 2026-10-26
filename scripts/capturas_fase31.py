"""Fase 31: capturas de evidencia con Playwright (1440 px, página completa).

Requiere la app corriendo (por defecto en http://localhost:8501). El script elige
el modelo (--modelo, por defecto llama3.1:8b) y 3 iteraciones en ⚙️.

    python -m scripts.capturas_fase31 --salida docs/evidencia/fase31/capturas

1. 01_botones.png: los 10 botones de acceso rápido.
2. 02_n2_boton_3_iteraciones.png: N2 lanzada con botón, 3 iteraciones en
   autocorrección, con la línea de tiempo y el veredicto final.
3. 03_pregunta_libre_sql_sano.png: una pregunta libre con su evaluación.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ESPERA_LLM_MS = 600_000


def captura_completa(pagina, ruta: str) -> None:
    """Streamlit desplaza el contenido dentro de su contenedor principal, así que
    `full_page` solo ve la ventana: se agranda la ventana al alto del contenido."""
    alto = pagina.evaluate(
        "() => Math.max(...[...document.querySelectorAll('[data-testid=stAppScrollToBottomContainer], .stMain, [data-testid=stAppViewContainer], "
        "section.main, body')].map(e => e.scrollHeight))")
    pagina.set_viewport_size({"width": 1440, "height": max(900, int(alto) + 80)})
    pagina.wait_for_timeout(1500)
    pagina.screenshot(path=ruta, full_page=True)
    pagina.set_viewport_size({"width": 1440, "height": 900})
    pagina.wait_for_timeout(500)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--salida", default="docs/evidencia/fase31/capturas")
    parser.add_argument("--pregunta", default="¿Cuántos pedidos hay en total?")
    parser.add_argument("--modelo", default="llama3.1:8b")
    parser.add_argument("--canal", default="msedge", help="Navegador instalado: msedge o chrome")
    args = parser.parse_args()
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        # El Edge instalado en Windows (sin descargar los navegadores de Playwright).
        nav = p.chromium.launch(channel=args.canal)
        pagina = nav.new_page(viewport={"width": 1440, "height": 900})
        pagina.goto(args.url)
        pagina.get_by_text("Las 10 necesidades").wait_for(timeout=60_000)
        pagina.wait_for_timeout(1500)
        pagina.screenshot(path=str(salida / "01_botones.png"), full_page=True)
        print("01 ok")

        # ⚙️ -> modelo llama3.1:8b, 3 iteraciones, modo autocorrección (el default).
        pagina.get_by_role("button", name="⚙️").click()
        pagina.get_by_role("combobox", name=re.compile("Modelo de Ollama")).click()
        pagina.get_by_role("option", name=args.modelo).click()
        pagina.wait_for_timeout(1500)
        campo = pagina.get_by_role("spinbutton", name=re.compile("Cantidad de iteraciones"))
        campo.fill("3")
        campo.press("Enter")
        pagina.wait_for_timeout(1500)
        pagina.keyboard.press("Escape")
        pagina.wait_for_timeout(1000)
        pagina.get_by_text("3 intento(s) en modo").wait_for(timeout=30_000)

        # El botón del área principal (la barra lateral tiene otro con el mismo texto).
        pagina.get_by_test_id("stMainBlockContainer").get_by_role("button", name="N2 · ").click()
        pagina.get_by_text("Resultado final:").first.wait_for(timeout=ESPERA_LLM_MS)
        pagina.wait_for_timeout(2000)
        captura_completa(pagina, str(salida / "02_n2_boton_3_iteraciones.png"))
        print("02 ok")

        pagina.get_by_placeholder("Escribe tu pregunta").fill(args.pregunta)
        pagina.keyboard.press("Enter")
        pagina.get_by_text("Detalles técnicos").last.wait_for(timeout=ESPERA_LLM_MS)
        pagina.wait_for_timeout(3000)
        pagina.get_by_text("Detalles técnicos").last.click()
        pagina.get_by_text("Sin respuesta de referencia").last.wait_for(timeout=30_000)
        pagina.wait_for_timeout(1500)
        captura_completa(pagina, str(salida / "03_pregunta_libre_sql_sano.png"))
        print("03 ok")
        nav.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
