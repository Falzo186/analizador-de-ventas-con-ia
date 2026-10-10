# Fase 11b — Cierre de pendientes de la verificación de `clasificar_fallo()`

Fecha: 2026-10-03 · Rama: `migracion-instacart`

## 1. Fallos preexistentes: confirmado

Se comparó con `git stash` (solo revierte `scripts/benchmark_modelos_llm.py`; los
dos archivos nuevos son untracked y no afectan a estas pruebas) y se restauró con
`git stash pop`. Árbol idéntico antes y después.

Comando (con y sin los cambios):

```
pytest tests/test_pdf_generador.py tests/test_dashboard_pdf_boton.py \
       tests/test_app_instacart.py::test_modelos_cargados_lee_api_ps
```

| Resultado | Sin los cambios de la Fase 11 | Con los cambios |
|---|---|---|
| Fallos | 5 | 5 (los mismos) |
| Errores | 14 (`FileNotFoundError: data/instacart/raw/orders.csv`) | 14 (los mismos) |

Los 5 fallos, idénticos en ambos casos:

- `test_pdf_generador.py::test_portada_cifras_coinciden_con_ficha`
- `test_pdf_generador.py::test_determinismo_de_contenido_dos_corridas`
- `test_dashboard_pdf_boton.py::test_boton_genera_pdf_y_ofrece_descarga`
- `test_dashboard_pdf_boton.py::test_boton_respeta_forzar_pdf_solo_si_se_marca`
- `test_app_instacart.py::test_modelos_cargados_lee_api_ps`

Causa común probable: este equipo no tiene `data/instacart/raw/` ni
`data/instacart.duckdb`. **Conclusión: son preexistentes; no hay nada que arreglar
en esta fase.** (No se diagnosticó cada uno por separado.)

### Ruido de `--help`

El traceback de `socketserver` / "Fatal Python error ... daemon threads" visto en
el reporte anterior **no se reproduce**: 3 corridas de
`python -m scripts.benchmark_modelos_llm --help` con los cambios y una sin ellos,
todas limpias. Se produjo justo después de `pytest` (hilos de Streamlit), no por
el script. Sin acción.

## 2. Corrida real con Ollama: PENDIENTE (bloqueada por datos)

- Comando para construir la base (README, "Cargar los datos"):
  `python -m src.db.instacart --raw data/instacart/raw --out data/instacart.duckdb`
- Requiere los 6 CSV de Instacart (Kaggle, usuario *yasserh*): `aisles`,
  `departments`, `products`, `orders`, `order_products__prior`,
  `order_products__train`.
- Estado en este equipo: no existe `data/instacart/raw/`, no hay CLI de Kaggle ni
  `~/.kaggle/kaggle.json`, y no hay copia de los CSV en `Downloads` ni en el
  árbol del usuario. La descarga requiere iniciar sesión en Kaggle (credenciales
  del usuario), por lo que no se forzó.
- Sin la base, `benchmark_modelos_llm` aborta antes de la primera pregunta
  (`IOException: database does not exist`), así que **aún no hay evidencia de
  `resumen_fallos` con datos reales** (solo la de los 15 tests unitarios).
- Modelos: `qwen2.5-coder:1.5b` no está instalado; sí `qwen2.5:1.5b`,
  `qwen2.5-coder:1.5b-base`, `qwen2.5-coder:latest`, `llama3.1:8b`. Si se quiere
  ese modelo exacto: `ollama pull qwen2.5-coder:1.5b` (~1 GB).

### Para cerrarlo

```
python -m src.db.instacart --raw data/instacart/raw --out data/instacart.duckdb
python -m scripts.benchmark_modelos_llm --modelos qwen2.5:1.5b --out docs/evidencia/benchmark_fase11_prueba.json
```

Entonces pegar aquí el bloque "Fallos por categoría" de la consola y el campo
`resumen_fallos` del JSON.
