"""Tests de `src.dashboard.servicio`: caché fresca no recalcula, caché
rancia sí, `refrescar_todas()` reporta lo esperado, y el módulo no importa
`streamlit` (Fase 3, decisión ya tomada: `src/dashboard/` no sabe nada de
la UI).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from src.analytics.registro import invocar
from src.dashboard import servicio
from tests.helpers_analytics import construir_fixture


class _ConexionEspia:
    """Envoltura de una conexión DuckDB real que cuenta cuántas veces se
    llama a `execute()`, para verificar que la ruta de caché fresca no
    ejecuta consultas analíticas pesadas (solo las 3 de `huella_bd()`)."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con
        self.llamadas_execute = 0

    def execute(self, *args, **kwargs):
        self.llamadas_execute += 1
        return self._con.execute(*args, **kwargs)

    def __getattr__(self, nombre):
        return getattr(self._con, nombre)


def test_servicio_no_importa_streamlit_ast():
    """No basta con que la palabra 'streamlit' no aparezca en el archivo (el
    propio docstring del módulo la menciona en prosa): se parsea el AST y se
    verifica que ningún `import`/`from ... import ...` real la traiga."""
    ruta_modulo = Path(servicio.__file__)
    arbol = ast.parse(ruta_modulo.read_text(encoding="utf-8"))
    modulos_importados = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            modulos_importados.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            modulos_importados.add(nodo.module)
    assert not any(m == "streamlit" or m.startswith("streamlit.") for m in modulos_importados), (
        f"src/dashboard/servicio.py importa streamlit: {modulos_importados}"
    )


def test_servicio_no_carga_streamlit_en_tiempo_de_ejecucion():
    """Prueba en un intérprete NUEVO (subprocess): importar y usar
    `src.dashboard.servicio` en aislamiento no debe cargar `streamlit` en
    `sys.modules`, ni siquiera transitivamente. En el proceso de este test
    NO se puede verificar `sys.modules` de forma confiable (otros archivos
    de la suite, como `test_dashboard_paneles.py`, sí importan streamlit
    para `AppTest`, y el orden de recolección de pytest no está garantizado)."""
    codigo = (
        "import sys\n"
        "import src.dashboard.servicio\n"
        "assert 'streamlit' not in sys.modules, "
        "'importar src.dashboard.servicio cargó streamlit: ' + str(sorted(sys.modules))\n"
        "print('OK')\n"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert resultado.returncode == 0, (
        f"stdout={resultado.stdout!r} stderr={resultado.stderr!r}"
    )
    assert resultado.stdout.strip() == "OK"


def test_cargar_necesidad_con_cache_vigente_no_recalcula(tmp_path):
    src = construir_fixture(tmp_path)
    out_dir = tmp_path / "referencia"

    # Precalcula N3 "a mano" y la deja en disco,
    # como si un `refrescar_todas()` anterior ya la hubiera generado.
    con_prep = duckdb.connect(str(src), read_only=True)
    con_prep.execute("SET memory_limit='4GB'"); con_prep.execute("SET threads=1")
    resultado_previo = invocar("N3", con_prep)
    resultado_previo.guardar_json(out_dir / "N3.json")
    con_prep.close()

    con_real = duckdb.connect(str(src), read_only=True)
    con_real.execute("SET memory_limit='4GB'"); con_real.execute("SET threads=1")
    espia = _ConexionEspia(con_real)
    try:
        carga = servicio.cargar_necesidad("N3", db_path=src, out_dir=out_dir, con=espia)
    finally:
        con_real.close()

    assert carga.desde_cache is True
    assert carga.resultado.id == "N3"
    # huella_bd() hace exactamente 3 `execute()` (orders, prior, train);
    # una caché vigente no debe ejecutar nada más (ninguna consulta de
    # `calcular()`).
    assert espia.llamadas_execute == 3, (
        f"se esperaban exactamente 3 execute() (los de huella_bd), hubo {espia.llamadas_execute}: "
        "una caché vigente no debería tocar más la BD que eso"
    )


def test_cargar_necesidad_con_cache_rancia_recalcula(tmp_path):
    src = construir_fixture(tmp_path)
    out_dir = tmp_path / "referencia"

    con_prep = duckdb.connect(str(src), read_only=True)
    con_prep.execute("SET memory_limit='4GB'"); con_prep.execute("SET threads=1")
    resultado_previo = invocar("N3", con_prep)
    resultado_previo.guardar_json(out_dir / "N3.json")
    con_prep.close()

    # "Cambia un byte del archivo de huella simulado": se edita directamente
    # el campo huella_bd.order_products_prior dentro del JSON en disco, sin
    # tocar la BD fuente -- el archivo deja de coincidir con la BD real.
    ruta = out_dir / "N3.json"
    data = json.loads(ruta.read_text(encoding="utf-8"))
    valor_original = data["huella_bd"]["order_products_prior"]
    data["huella_bd"]["order_products_prior"] = valor_original + 1
    ruta.write_text(json.dumps(data), encoding="utf-8")

    con_real = duckdb.connect(str(src), read_only=True)
    con_real.execute("SET memory_limit='4GB'"); con_real.execute("SET threads=1")
    espia = _ConexionEspia(con_real)
    try:
        carga = servicio.cargar_necesidad("N3", db_path=src, out_dir=out_dir, con=espia)
    finally:
        con_real.close()

    assert carga.desde_cache is False
    assert carga.resultado.id == "N3"
    # Recalcular N3 exige más de 3 execute() (huella_bd + la consulta de
    # referencia + la huella que guarda calcular()).
    assert espia.llamadas_execute > 3

    # El JSON en disco se reescribió con la huella correcta (vigente de nuevo).
    data_reescrito = json.loads(ruta.read_text(encoding="utf-8"))
    assert data_reescrito["huella_bd"]["order_products_prior"] == valor_original


def test_cargar_necesidad_sin_json_previo_calcula_y_escribe(tmp_path):
    src = construir_fixture(tmp_path)
    out_dir = tmp_path / "referencia"
    assert not (out_dir / "N4.json").exists()

    carga = servicio.cargar_necesidad("N4", db_path=src, out_dir=out_dir)

    assert carga.desde_cache is False
    assert carga.resultado.id == "N4"
    assert (out_dir / "N4.json").exists()

    # Una segunda carga, ahora sí, debe venir de caché.
    carga2 = servicio.cargar_necesidad("N4", db_path=src, out_dir=out_dir)
    assert carga2.desde_cache is True
    assert carga2.resultado.valores_clave == carga.resultado.valores_clave


def test_cargar_necesidad_id_desconocido_lanza_valueerror(tmp_path):
    src = construir_fixture(tmp_path)
    with pytest.raises(ValueError):
        servicio.cargar_necesidad("N99", db_path=src, out_dir=tmp_path / "referencia")


def test_refrescar_todas_sin_forzar_solo_recalcula_faltantes(tmp_path):
    src = construir_fixture(tmp_path)
    out_dir = tmp_path / "referencia"

    # Precalcula solo N1 y N3; el resto debe recalcularse en el refresco.
    con_prep = duckdb.connect(str(src), read_only=True)
    con_prep.execute("SET memory_limit='4GB'"); con_prep.execute("SET threads=1")
    invocar("N1", con_prep).guardar_json(out_dir / "N1.json")
    invocar("N3", con_prep).guardar_json(out_dir / "N3.json")
    con_prep.close()

    resumen = servicio.refrescar_todas(forzar=False, db_path=src, out_dir=out_dir)

    assert {e.id for e in resumen.estados} == set("N" + str(i) for i in range(1, 11))
    assert resumen.n_errores == 0
    por_id = {e.id: e for e in resumen.estados}
    assert por_id["N1"].recalculada is False
    assert por_id["N3"].recalculada is False
    for nid in ("N2", "N4", "N5", "N6", "N7", "N8", "N9", "N10"):
        assert por_id[nid].recalculada is True, f"{nid} debía recalcularse (sin JSON previo)"
    assert resumen.n_recalculadas == 8
    assert resumen.duracion_total_s > 0
    for e in resumen.estados:
        assert e.duracion_s >= 0

    # Las 10 quedaron escritas en disco.
    for i in range(1, 11):
        assert (out_dir / f"N{i}.json").exists()


def test_refrescar_todas_forzar_recalcula_todo_aunque_haya_cache(tmp_path):
    src = construir_fixture(tmp_path)
    out_dir = tmp_path / "referencia"

    primero = servicio.refrescar_todas(forzar=True, db_path=src, out_dir=out_dir)
    assert primero.n_recalculadas == 10

    segundo = servicio.refrescar_todas(forzar=True, db_path=src, out_dir=out_dir)
    assert segundo.n_recalculadas == 10, "forzar=True debe recalcular todo aunque la caché ya esté vigente"

    tercero = servicio.refrescar_todas(forzar=False, db_path=src, out_dir=out_dir)
    assert tercero.n_recalculadas == 0, "sin forzar, con caché ya vigente de la corrida anterior, no debe recalcular nada"


def test_json_de_otra_definicion_se_recalcula_aunque_la_huella_coincida(tmp_path):
    """Fase 24: al reemplazar las 11 necesidades viejas por las 10 nuevas la BD
    no cambió; un `N1.json` viejo tiene la huella correcta pero otra pregunta y
    otro SQL, y NO debe servirse como el N1 nuevo."""
    src = construir_fixture(tmp_path)
    out_dir = tmp_path / "referencia"
    servicio.cargar_necesidad("N1", db_path=src, out_dir=out_dir)
    ruta = out_dir / "N1.json"
    data = json.loads(ruta.read_text(encoding="utf-8"))
    data["pregunta"] = "¿Qué productos se recompran de forma consistente y cuáles se prueban y se abandonan?"
    data["consultas"] = [{"nombre": "agg_producto", "sql": "SELECT * FROM agg_producto"}]
    ruta.write_text(json.dumps(data), encoding="utf-8")

    carga = servicio.cargar_necesidad("N1", db_path=src, out_dir=out_dir)

    assert carga.desde_cache is False
    reescrito = json.loads(ruta.read_text(encoding="utf-8"))
    assert reescrito["pregunta"] == "¿Cuáles son los 10 productos más vendidos por número de unidades?"
