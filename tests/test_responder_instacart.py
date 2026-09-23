"""Orquestador de la ruta Instacart, SIN Ollama real: se reemplaza
`client.chat_con_modelo` por un doble que devuelve texto fijo (mismo
patrón que `tests/test_responder.py` para Northwind).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import duckdb
import pytest

from src.db.instacart import construir_duckdb
from src.llm import client
from src.llm import responder_instacart as ri
from tests.helpers_analytics import FIXTURE_DIR


@pytest.fixture()
def db_path(tmp_path) -> Path:
    ruta = tmp_path / "needs.duckdb"
    construir_duckdb(FIXTURE_DIR, ruta)
    return ruta


def test_sql_de_escritura_del_modelo_no_tumba_el_programa(db_path, monkeypatch):
    monkeypatch.setattr(client, "chat_con_modelo", lambda *a, **k: "DELETE FROM orders")

    res = ri.responder_pregunta_instacart("borra todo", db_path=db_path)

    assert res.ok is False
    assert "no puedo ejecutar" in res.error.lower()
    assert res.filas is None


def test_flujo_feliz_con_modelo_simulado(db_path, monkeypatch):
    respuestas = iter([
        "SELECT COUNT(*) AS n FROM orders",           # llamada 1: SQL
        "Hay 21 pedidos en total.",                    # llamada 2: redacción
    ])
    monkeypatch.setattr(client, "chat_con_modelo", lambda *a, **k: next(respuestas))

    res = ri.responder_pregunta_instacart("¿cuántos pedidos hay?", db_path=db_path)

    assert res.ok, res.error
    assert res.sql == "SELECT COUNT(*) AS n FROM orders"
    assert res.filas[0][0] > 0
    assert res.respuesta == "Hay 21 pedidos en total."


def test_sql_con_markdown_se_limpia_antes_de_ejecutar(db_path, monkeypatch):
    respuestas = iter([
        "```sql\nSELECT COUNT(*) AS n FROM orders\n```",
        "Listo.",
    ])
    monkeypatch.setattr(client, "chat_con_modelo", lambda *a, **k: next(respuestas))

    res = ri.responder_pregunta_instacart("¿cuántos pedidos hay?", db_path=db_path)
    assert res.ok, res.error
    assert res.sql == "SELECT COUNT(*) AS n FROM orders"


def test_tabla_inexistente_reporta_error_controlado_no_stacktrace(db_path, monkeypatch):
    monkeypatch.setattr(client, "chat_con_modelo", lambda *a, **k: "SELECT * FROM tabla_que_no_existe")

    res = ri.responder_pregunta_instacart("pregunta rara", db_path=db_path)

    assert res.ok is False
    assert "Traceback" not in res.error
    assert "no se pudo ejecutar" in res.error.lower()


def test_limit_forzado_se_aplica_en_el_flujo_completo(db_path, monkeypatch):
    monkeypatch.setattr(client, "chat_con_modelo", lambda *a, **k: "SELECT * FROM order_products")

    res = ri.responder_pregunta_instacart("dame todas las líneas", db_path=db_path, redactar=False, limite_filas=3)

    assert res.ok, res.error
    assert len(res.filas) <= 3


def test_solo_sql_no_hace_segunda_llamada(db_path, monkeypatch):
    llamadas = []

    def _doble(*a, **k):
        llamadas.append(a)
        return "SELECT COUNT(*) AS n FROM orders"

    monkeypatch.setattr(client, "chat_con_modelo", _doble)
    res = ri.responder_pregunta_instacart("¿cuántos pedidos hay?", db_path=db_path, redactar=False)

    assert res.ok, res.error
    assert res.respuesta is None
    assert len(llamadas) == 1  # solo la llamada de SQL, no la de redacción


def test_no_usa_esquema_de_northwind_en_el_esquema_pasado_al_modelo(db_path, monkeypatch):
    esquemas_vistos = []

    def _doble(modelo, sistema, usuario, **k):
        esquemas_vistos.append(sistema)
        return "SELECT COUNT(*) AS n FROM orders"

    monkeypatch.setattr(client, "chat_con_modelo", _doble)
    ri.responder_pregunta_instacart("¿cuántos pedidos hay?", db_path=db_path, redactar=False)

    assert esquemas_vistos
    assert "orders" in esquemas_vistos[0]
    assert "Order Details" not in esquemas_vistos[0].split("NADA de eso existe aquí")[-1]


# ---------------------------------------------------------------------------
# Reintento único ante SQL inválido
# ---------------------------------------------------------------------------

def _doble_con_registro(respuestas: list[str]):
    """Doble de `chat_con_modelo` que devuelve `respuestas` en orden y
    registra (sistema, usuario, kwargs) de cada llamada."""
    pendientes = iter(respuestas)
    llamadas: list[tuple[str, str, dict]] = []

    def _doble(modelo, sistema, usuario, **k):
        llamadas.append((sistema, usuario, k))
        return next(pendientes)

    return _doble, llamadas


def test_reintento_tras_rechazo_de_validar_sql_usa_el_segundo_sql(db_path, monkeypatch):
    doble, llamadas = _doble_con_registro([
        "No puedo darte una consulta para eso",   # intento 1: validar_sql lo rechaza
        "SELECT COUNT(*) AS n FROM orders",       # intento 2: válido
        "Hay 21 pedidos.",                        # redacción
    ])
    monkeypatch.setattr(client, "chat_con_modelo", doble)

    res = ri.responder_pregunta_instacart("¿cuántos pedidos hay?", db_path=db_path)

    assert res.ok, res.error
    assert res.sql == "SELECT COUNT(*) AS n FROM orders"
    assert res.intentos == 2
    assert "SELECT" in res.motivo_reintento
    # El segundo prompt lleva la pregunta original + el motivo EXACTO.
    _sistema, usuario_reintento, _k = llamadas[1]
    assert "¿cuántos pedidos hay?" in usuario_reintento
    assert f"rechazada porque: {res.motivo_reintento}" in usuario_reintento
    assert "Genera solo una sentencia SELECT válida sobre las tablas reales." in usuario_reintento


def test_reintento_tras_error_del_motor_usa_el_segundo_sql(db_path, monkeypatch):
    doble, llamadas = _doble_con_registro([
        "SELECT aisle FROM products",             # BinderException: products no tiene 'aisle'
        "SELECT COUNT(*) AS n FROM products",
    ])
    monkeypatch.setattr(client, "chat_con_modelo", doble)

    res = ri.responder_pregunta_instacart("¿cuántos productos?", db_path=db_path, redactar=False)

    assert res.ok, res.error
    assert res.sql == "SELECT COUNT(*) AS n FROM products"
    assert res.intentos == 2
    assert "BinderException" in res.motivo_reintento
    assert "aisle" in llamadas[1][1]


def test_si_ambos_intentos_fallan_error_controlado_y_sin_tercer_intento(db_path, monkeypatch):
    doble, llamadas = _doble_con_registro([
        "SELECT * FROM tabla_que_no_existe",
        "SELECT * FROM otra_tabla_que_no_existe",
        "SELECT COUNT(*) FROM orders",            # un tercer intento lo usaría: no debe ocurrir
    ])
    monkeypatch.setattr(client, "chat_con_modelo", doble)

    res = ri.responder_pregunta_instacart("pregunta rara", db_path=db_path)

    assert res.ok is False
    assert "no se pudo ejecutar" in res.error.lower()
    assert "Traceback" not in res.error
    assert res.intentos == 2
    assert len(llamadas) == 2
    assert res.filas is None


def test_sql_de_escritura_no_se_reintenta(db_path, monkeypatch):
    """Reintentar pidiendo "solo SELECT" convertiría "borra los pedidos" en
    una lectura que nadie pidió: se rechaza a la primera."""
    doble, llamadas = _doble_con_registro(["DELETE FROM orders", "SELECT * FROM orders"])
    monkeypatch.setattr(client, "chat_con_modelo", doble)

    res = ri.responder_pregunta_instacart("borra todo", db_path=db_path)

    assert res.ok is False
    assert "no puedo ejecutar" in res.error.lower()
    assert len(llamadas) == 1
    assert res.intentos == 1


# ---------------------------------------------------------------------------
# Timeout del modelo en el uso real (no solo en el benchmark)
# ---------------------------------------------------------------------------

def test_toda_llamada_al_modelo_lleva_timeout_por_defecto(db_path, monkeypatch):
    doble, llamadas = _doble_con_registro(["SELECT COUNT(*) AS n FROM orders", "Listo."])
    monkeypatch.setattr(client, "chat_con_modelo", doble)

    res = ri.responder_pregunta_instacart("¿cuántos pedidos hay?", db_path=db_path)

    assert res.ok, res.error
    assert [k.get("timeout_s") for _s, _u, k in llamadas] == [ri.TIMEOUT_LLM_SQL_S, ri.TIMEOUT_LLM_RESPUESTA_S]
    assert ri.TIMEOUT_LLM_SQL_S == 30.0 and ri.TIMEOUT_LLM_RESPUESTA_S == 30.0


@pytest.fixture()
def ollama_lento(monkeypatch):
    """Servidor HTTP local que imita `/api/chat` de Ollama. Las llamadas
    cuyo índice (0, 1, ...) está en `lentas` tardan `DEMORA_S` en
    responder; el resto responde al instante con `SQL_RAPIDO`. Se apunta
    el cliente `ollama` REAL a él con OLLAMA_HOST: el timeout que se prueba
    es el de httpx, no un doble."""
    estado = {"lentas": set(), "n": 0}
    DEMORA_S = 3.0

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            indice = estado["n"]
            estado["n"] += 1
            if indice in estado["lentas"]:
                time.sleep(DEMORA_S)
            cuerpo = json.dumps({
                "model": "falso", "done": True,
                "message": {"role": "assistant", "content": "SELECT COUNT(*) AS n FROM orders"},
            }).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)
            except (BrokenPipeError, ConnectionResetError):
                pass  # el cliente ya se fue por timeout: es justo lo esperado

        def log_message(self, *a):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    servidor.daemon_threads = True
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{servidor.server_address[1]}")
    yield estado, DEMORA_S
    servidor.shutdown()
    servidor.server_close()


def test_timeout_real_en_la_llamada_de_sql_devuelve_error_controlado(db_path, ollama_lento):
    estado, demora_s = ollama_lento
    estado["lentas"] = {0}

    t0 = time.perf_counter()
    res = ri.responder_pregunta_instacart("¿cuántos pedidos hay?", db_path=db_path, timeout_sql_llm_s=0.5)
    duracion = time.perf_counter() - t0

    assert res.ok is False
    assert "tardó demasiado" in res.error
    assert "Traceback" not in res.error
    assert duracion < demora_s, f"esperó {duracion:.1f}s: el timeout de 0.5s no cortó la llamada lenta"


def test_timeout_real_en_la_llamada_de_respuesta_devuelve_error_controlado(db_path, ollama_lento):
    estado, demora_s = ollama_lento
    estado["lentas"] = {1}  # la de SQL responde al instante; la de redacción cuelga

    t0 = time.perf_counter()
    res = ri.responder_pregunta_instacart("¿cuántos pedidos hay?", db_path=db_path, timeout_respuesta_llm_s=0.5)
    duracion = time.perf_counter() - t0

    assert res.filas is not None  # el SQL sí se ejecutó
    assert res.respuesta is None
    assert res.ok is False
    assert "no se pudo redactar" in res.error.lower()
    assert "no respondió en" in res.error
    assert duracion < demora_s, f"esperó {duracion:.1f}s: el timeout de 0.5s no cortó la llamada lenta"
