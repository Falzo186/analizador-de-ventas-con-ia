"""Un test por panel (N1-N10) del dashboard (Fase 3; Fase 24).

Dos capas de verificación, deliberadamente redundantes:

1. La gráfica genérica de `src/ui/dashboard.py` (`graficos`, vía
   `src.dashboard.graficas_datos.datos_grafica`) lee de la tabla
   `respuesta` las columnas `columna_nombre` y `columna_valor` de la
   definición de cada necesidad (`src.analytics.necesidades`). Se verifica
   contra un `ResultadoNecesidad` real (fixture chica): si falta una, el
   mensaje dice cuál.
2. Además se corre el dashboard real de punta a punta con
   `streamlit.testing.v1.AppTest` (apuntado a la fixture chica vía
   monkeypatch de `src.dashboard.servicio`, sin tocar `data/*.duckdb`) y se
   verifica que seleccionar cada panel no lance una excepción.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from src.analytics.necesidades import CONSULTA, DEFINICIONES
from src.analytics.registro import NECESIDADES, invocar
from src.dashboard import servicio
from tests.helpers_analytics import abrir, construir_fixture

RUTA_DASHBOARD = "src/ui/dashboard.py"


@pytest.fixture(scope="module")
def resultados(tmp_path_factory):
    """Un `ResultadoNecesidad` real (fixture chica) por necesidad, calculado
    una sola vez y reusado por los tests de la capa 1."""
    tmp_path = tmp_path_factory.mktemp("dashboard_contrato")
    src = construir_fixture(tmp_path)
    con = abrir(src)
    out = {nid: invocar(nid, con) for nid in NECESIDADES}
    con.close()
    return out


@pytest.mark.parametrize("nid", list(NECESIDADES))
def test_panel_columnas_de_la_grafica_existen(resultados, nid):
    r, d = resultados[nid], DEFINICIONES[nid]
    assert CONSULTA in r.tablas, f"{nid}: falta la tabla '{CONSULTA}' que lee la gráfica"
    filas = r.tablas[CONSULTA]
    if not filas:
        return  # sin filas no hay columnas que verificar en esta fixture chica
    esperadas = [c for c in (d.columna_nombre, d.columna_valor) if c]
    faltantes = [c for c in esperadas if c not in filas[0]]
    assert not faltantes, f"{nid}: la tabla '{CONSULTA}' no tiene las columnas {faltantes} de su definición"


@pytest.mark.parametrize("nid", list(NECESIDADES))
def test_panel_tiene_redaccion_y_sql(resultados, nid):
    r = resultados[nid]
    assert r.hallazgos, f"{nid}: sin redacción (hallazgos)"
    assert [c.sql for c in r.consultas] == [DEFINICIONES[nid].sql()]


# ---------------------------------------------------------------------------
# Capa 2: el dashboard real, de punta a punta, contra la fixture chica.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rutas_fixture(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("dashboard_e2e")
    src = construir_fixture(tmp_path)
    return src, tmp_path / "referencia"


@pytest.fixture()
def app(rutas_fixture, monkeypatch):
    src, out_dir = rutas_fixture
    monkeypatch.setattr(servicio, "DB_PATH_DEFAULT", src)
    monkeypatch.setattr(servicio, "OUT_DIR_DEFAULT", out_dir)
    at = AppTest.from_file(RUTA_DASHBOARD)
    at.run(timeout=90)
    assert not list(at.exception), f"el panel inicial (N1) ya falla: {list(at.exception)}"
    return at


@pytest.mark.parametrize("nid", list(NECESIDADES))
def test_panel_renderiza_sin_excepciones_end_to_end(app, resultados, nid):
    app.sidebar.radio[0].set_value(nid).run(timeout=90)
    excepciones = list(app.exception)
    assert not excepciones, (
        f"el panel {nid} lanzó una excepción al renderizar en el dashboard real: "
        f"{excepciones} -- regresión de contrato entre src.analytics y src.ui.dashboard"
    )
    assert app.title, f"el panel {nid} no generó ningún st.title"
    if any(not isinstance(v.valor, list) for v in resultados[nid].valores_clave):
        assert app.metric, f"el panel {nid} no generó ninguna métrica (valores_clave vacío?)"
    assert any("SELECT" in c.value for c in app.code), f"el panel {nid} no muestra el SQL de referencia"
