"""Un test por panel (N1-N11) del dashboard de Fase 3.

Dos capas de verificación, deliberadamente redundantes:

1. `CAMPOS_TABLA` documenta explícitamente qué tabla/columna necesita cada
   `graficos_nX()` de `src/ui/dashboard.py` (leído del propio código al
   escribir este test) y lo verifica contra un `ResultadoNecesidad` real
   (fixture chica, no el dataset completo). Si falta un campo, el mensaje
   dice exactamente cuál.
2. Además se corre el dashboard real de punta a punta con
   `streamlit.testing.v1.AppTest` (apuntado a la fixture chica vía
   monkeypatch de `src.dashboard.servicio`, sin tocar `data/*.duckdb`) y se
   verifica que seleccionar cada panel no lance una excepción. Esto atrapa
   cualquier campo que el código de la gráfica use y que `CAMPOS_TABLA` no
   haya anticipado -- la fuente de verdad es el código, no esta lista.

Si `src.analytics` renombra o quita un campo que la UI usa, AMBAS capas
deben fallar aquí, no en producción con el dashboard ya corriendo.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from src.analytics.registro import NECESIDADES, invocar
from src.dashboard import servicio
from tests.helpers_analytics import abrir, construir_fixture

RUTA_DASHBOARD = "src/ui/dashboard.py"

# nid -> {nombre_tabla: [columnas que graficos_nX() de dashboard.py lee]}
CAMPOS_TABLA: dict[str, dict[str, list[str]]] = {
    "N1": {
        "asegurar_inventario": ["product_id", "product_name", "lineas", "tasa_recompra", "tasa_abandono"],
        "revisar_retirar": ["product_id", "product_name", "lineas", "tasa_recompra", "tasa_abandono"],
    },
    "N2": {
        "top_50_lift": [
            "producto_a_nombre", "producto_b_nombre", "lift", "pedidos_con_par",
            "confianza_a_b", "confianza_b_a", "soporte",
        ],
    },
    "N3": {
        "matriz_dow_hora": ["order_hour_of_day", "order_dow", "n_pedidos", "indice", "pico", "valle"],
    },
    "N4": {
        "top_50_razon": ["razon"],
    },
    "N5": {
        "top_50_indice_ancla": [
            "product_name", "indice_ancla", "apariciones", "usuarios_distintos",
            "usuarios_primero", "tasa_primero",
        ],
    },
    "N6": {
        "departamentos": ["nombre", "lineas", "pct_lineas", "tasa_recompra", "cuadrante"],
    },
    "N7": {
        "por_numero_pedido": ["k", "tamano_medio", "tasa_recompra", "usuarios_n", "productos_nuevos_por_pedido"],
        "pareado_1_vs_10": [
            "diff_tamano_media", "diff_tasa_recompra_media",
            "diff_productos_nuevos_media", "diff_departamentos_distintos_media", "n_usuarios",
        ],
    },
    "N8": {
        "histograma_indice_novedad": ["bin_decimo", "n_usuarios"],
    },
    "N9": {
        "histograma_tamano": ["tamano_bin", "pedidos", "es_cubo_final"],
        "factor_hora": ["valor", "desviacion_intra_usuario", "tamano_medio_bruto", "pedidos"],
        "factor_dia": ["valor", "desviacion_intra_usuario", "tamano_medio_bruto", "pedidos"],
        "factor_intervalo": ["valor", "desviacion_intra_usuario", "tamano_medio_bruto", "pedidos"],
        "factor_numero_pedido": ["valor", "desviacion_intra_usuario", "tamano_medio_bruto", "pedidos"],
    },
    "N10": {
        "tasa_recompra_por_tramo": ["tramo", "lineas", "tasa_recompra"],
    },
    "N11": {
        "metricas": ["metodo", "k", "hit_rate", "recall_macro", "precision_macro", "n_usuarios"],
    },
}

# nid -> nombres de Parametro que graficos_nX() busca por nombre
CAMPOS_PARAMETRO: dict[str, list[str]] = {
    "N4": ["UMBRAL_RAZON"],
}

# nid -> prefijos de etiqueta de ValorClave que graficos_nX() busca
CAMPOS_VALOR_CLAVE_PREFIJO: dict[str, list[str]] = {
    "N8": ["Corte rutinario", "Corte explorador"],
}


@pytest.fixture(scope="module")
def resultados(tmp_path_factory):
    """Un `ResultadoNecesidad` real (fixture chica) por necesidad, calculado
    una sola vez y reusado por los tests de la capa 1."""
    tmp_path = tmp_path_factory.mktemp("dashboard_contrato")
    src, agg = construir_fixture(tmp_path)
    con = abrir(src)
    out = {nid: invocar(nid, con, agg) for nid in NECESIDADES}
    con.close()
    return out


@pytest.mark.parametrize("nid", sorted(CAMPOS_TABLA))
def test_panel_campos_de_tabla_existen(resultados, nid):
    r = resultados[nid]
    for nombre_tabla, columnas in CAMPOS_TABLA[nid].items():
        assert nombre_tabla in r.tablas, (
            f"{nid}: la tabla '{nombre_tabla}' que usa graficos_{nid.lower()}() "
            f"ya no está en ResultadoNecesidad.tablas (regresión de contrato "
            "analytics/dashboard)"
        )
        filas = r.tablas[nombre_tabla]
        if not filas:
            continue  # sin filas no hay columnas que verificar en esta fixture chica
        columnas_presentes = set(filas[0].keys())
        faltantes = [c for c in columnas if c not in columnas_presentes]
        assert not faltantes, (
            f"{nid}: la tabla '{nombre_tabla}' ya no tiene las columnas {faltantes} "
            f"que usa graficos_{nid.lower()}() (regresión de contrato analytics/dashboard)"
        )


@pytest.mark.parametrize("nid", sorted(CAMPOS_PARAMETRO))
def test_panel_parametros_existen(resultados, nid):
    r = resultados[nid]
    nombres_presentes = {p.nombre for p in r.parametros}
    for nombre in CAMPOS_PARAMETRO[nid]:
        assert nombre in nombres_presentes, (
            f"{nid}: el parámetro '{nombre}' que usa graficos_{nid.lower()}() ya no "
            "está en ResultadoNecesidad.parametros"
        )


@pytest.mark.parametrize("nid", sorted(CAMPOS_VALOR_CLAVE_PREFIJO))
def test_panel_valores_clave_existen(resultados, nid):
    r = resultados[nid]
    etiquetas = [v.etiqueta for v in r.valores_clave]
    for prefijo in CAMPOS_VALOR_CLAVE_PREFIJO[nid]:
        assert any(e.startswith(prefijo) for e in etiquetas), (
            f"{nid}: ningún valor_clave empieza con '{prefijo}' (lo busca "
            f"graficos_{nid.lower()}()); etiquetas disponibles: {etiquetas}"
        )


# ---------------------------------------------------------------------------
# Capa 2: el dashboard real, de punta a punta, contra la fixture chica.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rutas_fixture(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("dashboard_e2e")
    src, agg = construir_fixture(tmp_path)
    return src, agg, tmp_path / "referencia"


@pytest.fixture()
def app(rutas_fixture, monkeypatch):
    src, agg, out_dir = rutas_fixture
    monkeypatch.setattr(servicio, "DB_PATH_DEFAULT", src)
    monkeypatch.setattr(servicio, "AGG_PATH_DEFAULT", agg)
    monkeypatch.setattr(servicio, "OUT_DIR_DEFAULT", out_dir)
    at = AppTest.from_file(RUTA_DASHBOARD)
    at.run(timeout=90)
    assert not list(at.exception), f"el panel inicial (N1) ya falla: {list(at.exception)}"
    return at


@pytest.mark.parametrize("nid", sorted(NECESIDADES))
def test_panel_renderiza_sin_excepciones_end_to_end(app, nid):
    app.sidebar.radio[0].set_value(nid).run(timeout=90)
    excepciones = list(app.exception)
    assert not excepciones, (
        f"el panel {nid} lanzó una excepción al renderizar en el dashboard real: "
        f"{excepciones} -- regresión de contrato entre src.analytics y src.ui.dashboard"
    )
    assert app.title, f"el panel {nid} no generó ningún st.title"
    assert app.metric, f"el panel {nid} no generó ninguna métrica (valores_clave vacío?)"
