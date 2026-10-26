"""Fase 27: bitácora de aprendizaje del chat libre (`src.llm.bitacora_chat`),
revisor de la sentencia SQL (`src.llm.revision_sql`) y su integración en
`responder_pregunta_instacart` y en la app. Contra la fixture chica y con un
modelo simulado (sin Ollama). La bitácora de cada test es un archivo
temporal (`tests/conftest.py`).
"""

from __future__ import annotations

import json

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from src.llm import bitacora_chat as bc
from src.llm import client
from src.llm import prompts_instacart as pi
from src.llm import responder_instacart as ri
from src.llm import revision_sql as rs
from src.llm.esquema_instacart import construir_descripcion_esquema
from tests.helpers_analytics import abrir, construir_fixture
from tests.test_app_instacart import REVISION_OK, ModeloSimulado, app, rutas  # noqa: F401 - fixtures

#: El error real de la revisión: `department_id` no está en `aisles`.
SQL_MALO = ("SELECT d.department, COUNT(DISTINCT a.aisle_id) AS pasillos FROM departments d "
            "JOIN aisles a ON a.department_id = d.department_id GROUP BY d.department")
SQL_BUENO = ("SELECT d.department, COUNT(DISTINCT p.aisle_id) AS pasillos FROM departments d "
             "JOIN products p ON p.department_id = d.department_id GROUP BY d.department")
SQL_OTRA = "SELECT COUNT(*) AS n FROM orders"


def _sospechoso(motivo: str, leccion: str) -> str:
    return json.dumps({"sospechoso": True, "motivo": motivo, "leccion": leccion}, ensure_ascii=False)


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return construir_fixture(tmp_path_factory.mktemp("fase27"))


@pytest.fixture(scope="module")
def mapa(db):
    con = abrir(db)
    yield bc.mapa_columnas(con)
    con.close()


def _error_real(db, sql: str) -> str:
    con = abrir(db)
    try:
        con.execute(sql)
    except duckdb.Error as exc:
        return str(exc)
    finally:
        con.close()
    raise AssertionError(f"se esperaba un error de DuckDB con: {sql}")


class Doble:
    """Doble de `client.chat_con_modelo` para el responder: distingue la
    llamada de SQL (y su reintento) de la del revisor por el prompt de
    sistema, y guarda los prompts."""

    def __init__(self, sql: list[str], revisiones: list[str] | None = None):
        self.sql, self.revisiones = list(sql), list(revisiones or [])
        self.sistemas_sql: list[str] = []
        self.revisados: list[str] = []

    def __call__(self, modelo, sistema, usuario, **k):
        if sistema.startswith("Eres un revisor de consultas SQL"):
            self.revisados.append(usuario)
            return self.revisiones.pop(0) if self.revisiones else REVISION_OK
        self.sistemas_sql.append(sistema)
        return self.sql.pop(0)


def _preguntar(db, monkeypatch, doble: Doble, pregunta: str, **k) -> ri.ResultadoInstacart:
    monkeypatch.setattr(client, "chat_con_modelo", doble)
    return ri.responder_pregunta_instacart(pregunta, db_path=db, redactar=False, modelo="m", **k)


# ---------------------------------------------------------------------------
# Lecciones deterministas a partir del error real de DuckDB
# ---------------------------------------------------------------------------

def test_leccion_del_error_de_esquema_conocido(db, mapa):
    error = _error_real(db, SQL_MALO)
    assert 'Table "a" does not have a column named "department_id"' in error
    leccion = bc.leccion_de_error(error, SQL_MALO, mapa, "¿cuántos pasillos tiene cada departamento?")
    assert leccion.firma == "columna_en_tabla:aisles.department_id"
    assert leccion.leccion == ("La tabla aisles NO tiene la columna department_id (sus columnas: aisle_id, aisle); "
                               "department_id está en: departments, products.")
    assert leccion.origen == bc.ORIGEN_ERROR and leccion.sql == SQL_MALO and "department_id" in leccion.detalle


@pytest.mark.parametrize("sql,firma,fragmento", [
    ("SELECT department FROM order_products", "columna_no_encontrada:department",
     "La columna department no está en las tablas de tu FROM/JOIN (order_products); está en: departments"),
    ("SELECT d.department FROM aisles a GROUP BY d.department", "alias_no_declarado",
     "Usaste el alias d sin declarar su tabla"),
    ("SELECT AVG(COUNT(*)) FROM order_products GROUP BY order_id", "agregacion_anidada", "No anides agregaciones"),
    ("SELECT product_name, COUNT(*) FROM products", "group_by_incompleto", "debe ir también en el GROUP BY"),
    ("SELECT * FROM clientes", "tabla_inexistente:clientes", "La tabla clientes no existe; las tablas y vistas reales"),
])
def test_lecciones_de_otros_errores_de_esquema(db, mapa, sql, firma, fragmento):
    leccion = bc.leccion_de_error(_error_real(db, sql), sql, mapa)
    assert leccion.firma == firma and fragmento in leccion.leccion


def test_timeout_y_errores_sin_sql():
    assert bc.leccion_de_error("La consulta excedió el límite de 10s y se interrumpió.", "SELECT 1", {}).firma == "timeout"
    assert bc.leccion_de_error("Binder Error: x", "", {}) is None


def test_alias_de_tablas():
    assert bc.alias_de_tablas(SQL_MALO) == {"departments": "departments", "d": "departments",
                                            "aisles": "aisles", "a": "aisles"}
    assert bc.alias_de_tablas("SELECT * FROM orders WHERE x = 1") == {"orders": "orders"}


# ---------------------------------------------------------------------------
# Bitácora en disco: dedup, límites, persistencia
# ---------------------------------------------------------------------------

def _leccion(i: int, texto: str | None = None) -> bc.Leccion:
    # Fase 31: textos distintos (e{i}q{i}); «lección 1» y «lección 2» ya cuentan como la misma (similitud).
    return bc.Leccion(f"f{i}", texto or f"lección e{i}q{i}", bc.ORIGEN_ERROR, f"p{i}", "SELECT 1", "err")


def test_persiste_entre_instancias_y_deduplica(tmp_path):
    ruta = tmp_path / "b.json"
    bc.Bitacora(ruta).agregar(_leccion(1))
    bc.Bitacora(ruta).agregar(_leccion(1, "lección 1 reformulada"))
    (x,) = bc.Bitacora(ruta).cargar()  # otra instancia, mismo archivo: como otra sesión de Streamlit
    assert (x.firma, x.veces, x.leccion) == ("f1", 2, "lección 1 reformulada")


def test_archivo_corrupto_o_ausente_es_bitacora_vacia(tmp_path):
    assert bc.Bitacora(tmp_path / "no_existe.json").cargar() == []
    (tmp_path / "roto.json").write_text("{no es json", encoding="utf-8")
    assert bc.Bitacora(tmp_path / "roto.json").cargar() == []
    assert bc.Bitacora(tmp_path / "roto.json").bloque_prompt() is None


def test_limite_de_guardadas_descarta_la_menos_repetida_y_mas_vieja(tmp_path):
    b = bc.Bitacora(tmp_path / "b.json")
    b.agregar(_leccion(0))
    b.agregar(_leccion(0))  # f0: 2 veces, la más vieja pero repetida
    for i in range(1, bc.MAX_LECCIONES_GUARDADAS + 1):
        b.agregar(_leccion(i))
    firmas = {x.firma for x in b.cargar()}
    assert len(firmas) == bc.MAX_LECCIONES_GUARDADAS
    assert "f0" in firmas, "la repetida se queda"
    assert "f1" not in firmas, "sale la de menos veces y más antigua"


def test_prompt_lleva_como_maximo_3_lecciones_y_1900_caracteres(tmp_path):
    # Fase 31: el máximo bajó de 8 a 3 (las más relevantes a la pregunta).
    b = bc.Bitacora(tmp_path / "b.json")
    for i in range(12):
        b.agregar(_leccion(i, f"lección e{i}q{i} " + "x" * 150))
    elegidas = b.para_prompt()
    assert len(elegidas) == bc.MAX_LECCIONES_PROMPT
    bloque = b.bloque_prompt()
    assert bloque.startswith(bc.ENCABEZADO_PROMPT) and len(bloque) <= bc.MAX_CARACTERES_BLOQUE
    larga = b.agregar(_leccion(99, "y" * 500))
    assert len(larga.leccion) <= bc.MAX_CARACTERES_LECCION
    b.borrar()
    assert b.cargar() == [] and b.bloque_prompt() is None


def test_sin_lecciones_el_prompt_de_sql_no_cambia():
    assert pi.prompt_sql_instacart("p", "esq") == pi.prompt_sql_instacart("p", "esq", lecciones=None)
    con_lecciones, _ = pi.prompt_sql_instacart("p", "esq", lecciones="LECCIONES:\n- no unas aisles por department_id")
    sin, _ = pi.prompt_sql_instacart("p", "esq")
    assert con_lecciones.startswith(sin) and con_lecciones.rstrip().endswith("- no unas aisles por department_id")
    s1, _ = pi.prompt_sql_reintento_instacart("p", "esq", "SELECT 1", "motivo", lecciones="L")
    assert s1.rstrip().endswith("L")


# ---------------------------------------------------------------------------
# Responder: la lección de una pregunta llega a la siguiente (distinta)
# ---------------------------------------------------------------------------

def test_leccion_de_una_pregunta_llega_a_otra_distinta(db, monkeypatch):
    bitacora = bc.Bitacora()
    primera = _preguntar(db, monkeypatch, Doble([SQL_MALO, SQL_MALO]),
                         "¿Cuántos pasillos tiene cada departamento?", bitacora=bitacora, revisar_sql=True)
    assert not primera.ok and "department_id" in primera.error and primera.lecciones_en_prompt == 0
    (guardada,) = bitacora.cargar()
    assert guardada.firma == "columna_en_tabla:aisles.department_id"
    assert guardada.pregunta == "¿Cuántos pasillos tiene cada departamento?" and guardada.sql == SQL_MALO
    assert "department_id" in guardada.detalle
    assert primera.lecciones_nuevas == [guardada.leccion]
    # Ambos intentos fallaron por lo mismo: una sola lección, sin duplicar en la misma pregunta.
    assert guardada.veces == 1

    # Fase 31: llega a otra pregunta distinta si es RELEVANTE (comparte tema: pasillos/departamento)...
    doble = Doble([SQL_OTRA])
    segunda = _preguntar(db, monkeypatch, doble, "¿Qué departamento tiene más pasillos?", bitacora=bitacora,
                         revisar_sql=True)
    assert segunda.ok and segunda.lecciones_en_prompt == 1
    (sistema,) = doble.sistemas_sql
    assert bc.ENCABEZADO_PROMPT in sistema and guardada.leccion in sistema
    # ...y no a una que no tiene nada que ver.
    ajena = Doble([SQL_OTRA])
    tercera = _preguntar(db, monkeypatch, ajena, "¿Cuántos pedidos hay?", bitacora=bitacora, revisar_sql=True)
    assert tercera.lecciones_en_prompt == 0 and bc.ENCABEZADO_PROMPT not in ajena.sistemas_sql[0]


def test_la_misma_leccion_repetida_suma_veces(db, monkeypatch):
    bitacora = bc.Bitacora()
    for _ in range(2):
        _preguntar(db, monkeypatch, Doble([SQL_MALO, SQL_BUENO]), "¿Pasillos por departamento?",
                   bitacora=bitacora, revisar_sql=False)
    (x,) = bitacora.cargar()
    assert x.veces == 2


def test_revisor_sospechoso_reintenta_y_deja_leccion(db, monkeypatch):
    bitacora = bc.Bitacora()
    doble = Doble([SQL_OTRA, SQL_BUENO], [_sospechoso(
        "La pregunta pide pasillos por departamento y el SQL cuenta pedidos.",
        "Cuenta lo que la pregunta pide (pasillos), no pedidos.")])
    res = _preguntar(db, monkeypatch, doble, "¿Cuántos pasillos tiene cada departamento?", bitacora=bitacora,
                     revisar_sql=True)
    assert res.ok and res.reintento_por_revision and res.intentos == 2
    assert res.sql.startswith(SQL_BUENO) and res.sql_rechazado == SQL_OTRA
    assert res.motivo_reintento.startswith("Un revisor marcó tu SQL como sospechoso: La pregunta pide pasillos")
    assert res.revision_sql["sospechoso"] and "SQL A REVISAR" in doble.revisados[0]
    (x,) = bitacora.cargar()
    assert x.origen == bc.ORIGEN_REVISOR and x.leccion == "Cuenta lo que la pregunta pide (pasillos), no pedidos."
    assert x.sql == SQL_OTRA, "la lección guarda el SQL que se marcó, no el corregido"


def test_revisor_sin_problemas_no_reintenta_ni_aprende(db, monkeypatch):
    bitacora = bc.Bitacora()
    res = _preguntar(db, monkeypatch, Doble([SQL_BUENO]), "¿Pasillos por departamento?", bitacora=bitacora,
                     revisar_sql=True)
    assert res.ok and res.intentos == 1 and not res.reintento_por_revision
    assert res.revision_sql["sospechoso"] is False and bitacora.cargar() == []


def test_si_el_reintento_del_revisor_falla_se_conserva_el_original(db, monkeypatch):
    bitacora = bc.Bitacora()
    res = _preguntar(db, monkeypatch, Doble([SQL_BUENO, SQL_MALO], [_sospechoso("Algo raro.", "Lección rara.")]),
                     "¿Pasillos por departamento?", bitacora=bitacora, revisar_sql=True)
    assert res.ok and res.sql.startswith(SQL_BUENO) and res.intentos == 1 and not res.reintento_por_revision
    assert "department_id" in res.revision_sql["reintento_error"]
    # Fase 31: el resultado final es el original (el reintento no ejecutó): la alarma no cambió el resultado, así
    # que no hay lección del revisor; sí la del error real de DuckDB del reintento.
    firmas = {x.firma for x in bitacora.cargar()}
    assert firmas == {"columna_en_tabla:aisles.department_id"}


def test_el_revisor_no_reintenta_si_ya_se_uso_el_reintento(db, monkeypatch):
    doble = Doble([SQL_MALO, SQL_BUENO], [_sospechoso("Raro.", "Lección.")])
    res = _preguntar(db, monkeypatch, doble, "¿Pasillos?", bitacora=bc.Bitacora(), revisar_sql=True)
    assert res.ok and res.intentos == 2 and not res.reintento_por_revision and len(doble.sistemas_sql) == 2


def test_no_se_aprende_de_la_guarda_ni_del_modelo_caido(db, monkeypatch):
    bitacora = bc.Bitacora()
    res = _preguntar(db, monkeypatch, Doble(["DELETE FROM orders"]), "borra todo", bitacora=bitacora, revisar_sql=True)
    assert not res.ok and bitacora.cargar() == []

    def _caido(*a, **k):
        raise client.LlmNoDisponible("sin conexión")

    monkeypatch.setattr(client, "chat_con_modelo", _caido)
    res = ri.responder_pregunta_instacart("¿pedidos?", db_path=db, redactar=False, bitacora=bitacora, revisar_sql=True)
    assert not res.ok and bitacora.cargar() == []


def test_sin_bitacora_ni_revisor_nada_cambia(db, monkeypatch):
    """Las necesidades (iteraciones) y la entrega no pasan bitácora ni revisor."""
    bc.Bitacora().agregar(bc.Leccion("f", "una lección", bc.ORIGEN_ERROR, "p", "s", "d"))
    doble = Doble([SQL_OTRA])
    res = _preguntar(db, monkeypatch, doble, "¿Cuántos pedidos hay?")
    assert res.ok and res.revision_sql is None and res.lecciones_en_prompt == 0 and not doble.revisados
    assert bc.ENCABEZADO_PROMPT not in doble.sistemas_sql[0]


def test_revision_ilegible_no_es_sospechosa():
    assert rs.parsear_revision("no es json").sospechoso is False
    assert rs.parsear_revision('{"sospechoso": true, "motivo": ""}').sospechoso is False, "sin motivo no cuenta"
    r = rs.parsear_revision('{"sospechoso": "true", "motivo": "x", "leccion": "y"}')
    assert (r.sospechoso, r.motivo, r.leccion, r.origen) == (True, "x", "y", rs.ORIGEN_REVISOR)


def test_el_revisor_ve_el_esquema_real_la_pregunta_el_sql_y_el_resultado(db, monkeypatch):
    doble = Doble([SQL_BUENO])
    _preguntar(db, monkeypatch, doble, "¿Pasillos por departamento?", bitacora=bc.Bitacora(), revisar_sql=True)
    (usuario,) = doble.revisados
    assert "- aisles: aisle_id" in usuario and "¿Pasillos por departamento?" in usuario
    assert "SQL A REVISAR:\n" + " ".join(SQL_BUENO.split()) in usuario and "department | pasillos" in usuario


# ---------------------------------------------------------------------------
# App: dos preguntas en la misma sesión, y bitácora cargada al iniciar
# ---------------------------------------------------------------------------

def _preguntar_en_app(at: AppTest, pregunta: str) -> AppTest:
    at.chat_input(key="chat_input").set_value(pregunta).run()
    assert not list(at.exception), list(at.exception)
    return at


def test_app_la_segunda_pregunta_recibe_la_leccion_de_la_primera(app):
    modelo = ModeloSimulado([SQL_MALO, SQL_MALO, SQL_OTRA], ["Hay 21 pedidos."])
    at = app(modelo)
    _preguntar_en_app(at, "¿Cuántos pasillos tiene cada departamento?")
    meta = at.session_state["historial"][-1]["metadatos"]
    assert not meta["ok"] and len(meta["lecciones_nuevas"]) == 1
    leccion = meta["lecciones_nuevas"][0]
    assert any(c.value == f"📓 Lección agregada a la bitácora: {leccion}" for c in at.caption)

    _preguntar_en_app(at, "¿Qué departamento tiene más pasillos?")  # Fase 31: relevante a la lección
    assert leccion in modelo.sistemas_sql[-1] and leccion not in modelo.sistemas_sql[0]
    assert at.session_state["historial"][-1]["metadatos"]["lecciones_en_prompt"] == 1
    assert any(c.value == "📓 El prompt de esta pregunta llevaba 1 lección(es) de la bitácora." for c in at.caption)


def test_app_carga_la_bitacora_guardada_al_iniciar(app):
    bc.Bitacora().agregar(bc.Leccion("columna_en_tabla:aisles.department_id", "La tabla aisles NO tiene la columna "
                                     "department_id.", bc.ORIGEN_ERROR, "¿Qué departamento tiene más pasillos?",
                                     "s", "d"))
    modelo = ModeloSimulado([SQL_OTRA], ["Hay 21 pedidos."])
    at = app(modelo)  # sesión nueva: la lección viene del disco
    assert any("📓 Bitácora: 1 lección(es) guardada(s)" in c.value for c in at.caption)
    _preguntar_en_app(at, "¿Cuántos pasillos tiene cada departamento?")  # Fase 31: relevante a la lección
    assert "La tabla aisles NO tiene la columna department_id." in modelo.sistemas_sql[0]


def test_app_boton_borrar_bitacora(app):
    bc.Bitacora().agregar(bc.Leccion("f", "lección", bc.ORIGEN_ERROR, "p", "s", "d"))
    at = app(ModeloSimulado([], []))
    at.button(key="borrar_bitacora").click().run()
    assert bc.Bitacora().cargar() == []


def test_app_revisor_desactivable(app):
    modelo = ModeloSimulado([SQL_OTRA], ["Hay 21 pedidos."])
    at = app(modelo)
    at.checkbox(key="revisar_sql_sel").uncheck().run()
    _preguntar_en_app(at, "¿Cuántos pedidos hay?")
    assert not modelo.revisiones_hechas


def test_descripcion_del_esquema_no_cambia(db):
    """Las lecciones van aparte: la descripción del esquema (y su caché) no se toca."""
    con = abrir(db)
    try:
        assert bc.ENCABEZADO_PROMPT not in construir_descripcion_esquema(con)
    finally:
        con.close()
