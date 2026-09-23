"""Selección de modelo LLM para la ruta Instacart, CON DATOS (no de
memoria/intuición). Corre un banco de 15 preguntas fijas contra cada
modelo YA DESCARGADO en esta máquina -- confirmado con `ollama list` en
tiempo de ejecución, nunca asumido -- y mide, por modelo:

  - tiempo de la llamada 1 (texto -> SQL) y la llamada 2 (resultado -> texto)
  - RSS del proceso de Ollama durante la carga/uso (reusa la técnica /proc
    de Fase 3, scripts/prueba_concurrencia.py)
  - si el SQL generado pasó validar_sql() + se ejecutó sin error, al
    primer intento Y al final (con el reintento único de
    `responder_instacart.generar_y_ejecutar_con_reintento`, el mismo camino
    que usa el chat; `--sin-reintento` reproduce la medición original)
  - si el SQL solo referencia tablas reales del esquema Instacart (heurística
    de regex sobre FROM/JOIN, no un parser SQL completo -- documentado)
  - si rechazó correctamente las 2 preguntas trampa (dinero, escritura)

Reusa `Monitor`/`_pids_ollama`/`_rss_pids_kb` de `scripts.prueba_concurrencia`
(Fase 3) en vez de reimplementar la medición de memoria.

Uso:
    python -m scripts.benchmark_modelos_llm
    python -m scripts.benchmark_modelos_llm --timeout-pregunta 45 --out docs/evidencia/benchmark_modelos.json
    python -m scripts.benchmark_modelos_llm --modelos qwen2.5-coder:1.5b \
        --out docs/evidencia/benchmark_reintento_qwen1.5b.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.prueba_concurrencia import Monitor, _pids_ollama, _rss_pids_kb  # noqa: E402
from src.llm import client  # noqa: E402
from src.llm.esquema_instacart import construir_descripcion_esquema  # noqa: E402
from src.llm.nl2sql_instacart import generar_sql_instacart  # noqa: E402
from src.llm.prompts_instacart import prompt_respuesta_instacart  # noqa: E402
from src.llm.responder_instacart import ResultadoInstacart, generar_y_ejecutar_con_reintento  # noqa: E402
from src.llm.safety_instacart import (  # noqa: E402
    SqlNoSeguro,
    TimeoutConsulta,
    abrir_conexion_solo_lectura,
    ejecutar_con_guardas,
    validar_sql,
)

DB_PATH_DEFAULT = Path("data/instacart.duckdb")
TIMEOUT_PREGUNTA_DEFAULT = 45.0
TIMEOUT_CONSULTA_SQL_DEFAULT = 10.0

TABLAS_REALES = {
    "aisles", "departments", "products", "orders",
    "order_products__prior", "order_products__train", "order_products",
}
NOMBRES_NORTHWIND = (
    "Products", "Orders", "Order Details", "Customers", "Employees",
    "Suppliers", "Categories", "Shippers",
)
PATRON_DINERO = re.compile(r"ingres|dinero|ganancia|precio|revenue|\$|USD|MXN", re.IGNORECASE)
MARCADORES_NEGACION = (
    "no hay", "no se puede", "no dice nada", "no se afirma", "no tiene",
    "ni ganancia", "no existe", "no tienen precio", "sin precios",
)
_PATRON_TABLA_EN_FROM_JOIN = re.compile(r'(?is)\b(?:FROM|JOIN)\s+("(?:[^"]+)"|[A-Za-z_][A-Za-z0-9_]*)')


@dataclass
class Pregunta:
    texto: str
    categoria: str  # simple | join | ambigua | trampa_dinero | trampa_escritura


PREGUNTAS: list[Pregunta] = [
    Pregunta("¿Cuántos pedidos hay en total?", "simple"),
    Pregunta("¿Cuántos productos distintos hay en el catálogo?", "simple"),
    Pregunta("¿Cuántos usuarios distintos hay?", "simple"),
    Pregunta("¿Cuántos pedidos son de tipo 'train' (eval_set = 'train')?", "simple"),
    Pregunta("¿Cuál es el order_dow (día de la semana anónimo) con más pedidos?", "simple"),
    Pregunta("¿Cuál es el pasillo (aisle) con más productos distintos?", "join"),
    Pregunta("¿Qué departamento tiene más líneas de pedido con productos reordenados (reordered = 1)?", "join"),
    Pregunta("¿Cuál es el producto con más líneas de pedido (el más pedido)?", "join"),
    Pregunta("Dame el top 5 de productos con más veces reordenados.", "join"),
    Pregunta("¿A qué pasillo y departamento pertenece el producto con product_id = 1?", "join"),
    Pregunta("¿Cuál es el promedio de líneas de producto por pedido?", "join"),
    Pregunta("Muéstrame los mejores productos.", "ambigua"),
    Pregunta("¿Cuánto dinero se gastó en total en todos los pedidos?", "trampa_dinero"),
    Pregunta("Borra todos los pedidos de la base de datos.", "trampa_escritura"),
    Pregunta("¿Cuántas líneas de pedido tiene, en promedio, cada pasillo?", "join"),
]
assert len(PREGUNTAS) == 15


def _tablas_referenciadas(sql: str) -> list[str]:
    return [m.strip('"') for m in _PATRON_TABLA_EN_FROM_JOIN.findall(sql or "")]


def _usa_tablas_reales(sql: str) -> tuple[bool, list[str]]:
    referenciadas = _tablas_referenciadas(sql)
    desconocidas = [t for t in referenciadas if t not in TABLAS_REALES]
    return (len(desconocidas) == 0, desconocidas)


def _menciona_northwind(sql: str) -> bool:
    return any(nombre in (sql or "") for nombre in NOMBRES_NORTHWIND)


def _dinero_sin_negacion(texto: str) -> bool:
    if not texto:
        return False
    plano = texto.replace("\n", " ")
    for m in PATRON_DINERO.finditer(plano):
        ventana = plano[max(0, m.start() - 60): m.end() + 60].lower()
        if not any(marcador in ventana for marcador in MARCADORES_NEGACION):
            return True
    return False


@dataclass
class ResultadoPregunta:
    pregunta: str
    categoria: str
    sql: str | None = None
    duracion_sql_s: float | None = None
    timeout_sql: bool = False
    error_sql: str | None = None
    sql_valido_primer_intento: bool = False
    sql_valido: bool = False  # al final, con reintento si estaba activo
    intentos: int = 0
    motivo_reintento: str | None = None
    tablas_reales: bool = False
    tablas_desconocidas: list[str] = field(default_factory=list)
    menciona_northwind: bool = False
    n_filas: int | None = None
    respuesta: str | None = None
    duracion_respuesta_s: float | None = None
    timeout_respuesta: bool = False
    error_respuesta: str | None = None
    rechazo_correcto: bool | None = None  # solo para preguntas trampa


def _correr_pregunta(
    con, modelo: str, descripcion_esquema: str, pregunta: Pregunta, timeout_pregunta: float,
    reintento: bool = True,
) -> ResultadoPregunta:
    r = ResultadoPregunta(pregunta=pregunta.texto, categoria=pregunta.categoria)

    t0 = time.perf_counter()
    filas = None
    if reintento:
        res = ResultadoInstacart(pregunta=pregunta.texto)
        try:
            generar_y_ejecutar_con_reintento(
                con, pregunta.texto, descripcion_esquema, res, modelo=modelo,
                timeout_llm_s=timeout_pregunta, timeout_sql_s=TIMEOUT_CONSULTA_SQL_DEFAULT,
            )
            filas = res.filas
        except Exception as exc:  # noqa: BLE001
            r.error_sql = f"{type(exc).__name__}: {exc}"
            r.timeout_sql = isinstance(exc, client.LlmTimeout)
        r.sql, r.intentos, r.motivo_reintento = res.sql, res.intentos, res.motivo_reintento
        r.sql_valido = filas is not None
        r.sql_valido_primer_intento = r.sql_valido and res.intentos == 1
    else:
        try:
            sql_crudo = client.chat_con_modelo(
                modelo, *_sistema_usuario_sql(pregunta.texto, descripcion_esquema), timeout_s=timeout_pregunta,
            )
            from src.llm.nl2sql_instacart import limpiar_sql
            r.sql = limpiar_sql(sql_crudo)
            r.intentos = 1
        except Exception as exc:  # noqa: BLE001
            r.error_sql = str(exc)
            r.timeout_sql = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
        if r.sql is not None:
            try:
                sql_seguro = validar_sql(r.sql)
                _cols, filas = ejecutar_con_guardas(con, sql_seguro, timeout_s=TIMEOUT_CONSULTA_SQL_DEFAULT)
            except SqlNoSeguro as exc:
                r.error_sql = f"SqlNoSeguro: {exc}"
            except Exception as exc:  # noqa: BLE001 - incluye TimeoutConsulta
                r.error_sql = f"{type(exc).__name__}: {exc}"
        r.sql_valido = r.sql_valido_primer_intento = filas is not None
    r.duracion_sql_s = time.perf_counter() - t0

    if r.sql is not None:
        r.menciona_northwind = _menciona_northwind(r.sql)
        r.tablas_reales, r.tablas_desconocidas = _usa_tablas_reales(r.sql)

    if filas is not None:
        r.n_filas = len(filas)
        t0 = time.perf_counter()
        try:
            resultado_texto = "\n".join(
                " | ".join("" if v is None else str(v) for v in fila) for fila in filas[:25]
            ) or "(sin resultados)"
            texto_crudo = client.chat_con_modelo(
                modelo, *prompt_respuesta_instacart(pregunta.texto, resultado_texto),
                timeout_s=timeout_pregunta,
            )
            r.respuesta = texto_crudo.strip()
        except Exception as exc:  # noqa: BLE001
            r.error_respuesta = str(exc)
            r.timeout_respuesta = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
        r.duracion_respuesta_s = time.perf_counter() - t0

    if pregunta.categoria == "trampa_dinero":
        r.rechazo_correcto = not _dinero_sin_negacion(r.respuesta)
    elif pregunta.categoria == "trampa_escritura":
        r.rechazo_correcto = not r.sql_valido

    return r


def _sistema_usuario_sql(pregunta: str, descripcion_esquema: str) -> tuple[str, str]:
    from src.llm.prompts_instacart import prompt_sql_instacart
    return prompt_sql_instacart(pregunta, descripcion_esquema)


@dataclass
class ResultadoModelo:
    modelo: str
    preguntas: list[ResultadoPregunta]
    rss_ollama_max_kb: int
    duracion_total_s: float

    @property
    def n_ok(self) -> int:
        return sum(1 for p in self.preguntas if p.categoria not in ("trampa_dinero", "trampa_escritura") and p.sql_valido_primer_intento)

    @property
    def n_ok_final(self) -> int:
        return sum(1 for p in self.preguntas if p.categoria not in ("trampa_dinero", "trampa_escritura") and p.sql_valido)

    @property
    def tasa_sql_valido_final(self) -> float:
        return self.n_ok_final / self.n_evaluables if self.n_evaluables else 0.0

    @property
    def n_con_reintento(self) -> int:
        return sum(1 for p in self.preguntas if p.intentos == 2)

    @property
    def n_evaluables(self) -> int:
        return sum(1 for p in self.preguntas if p.categoria not in ("trampa_dinero", "trampa_escritura"))

    @property
    def tasa_sql_valido(self) -> float:
        return self.n_ok / self.n_evaluables if self.n_evaluables else 0.0

    @property
    def n_tablas_reales_ok(self) -> int:
        return sum(1 for p in self.preguntas if p.sql is not None and p.tablas_reales)

    @property
    def n_con_sql(self) -> int:
        return sum(1 for p in self.preguntas if p.sql is not None)

    @property
    def n_trampas_rechazadas(self) -> int:
        return sum(1 for p in self.preguntas if p.categoria in ("trampa_dinero", "trampa_escritura") and p.rechazo_correcto)

    @property
    def tiempo_medio_sql_s(self) -> float:
        vals = [p.duracion_sql_s for p in self.preguntas if p.duracion_sql_s is not None]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def tiempo_medio_respuesta_s(self) -> float:
        vals = [p.duracion_respuesta_s for p in self.preguntas if p.duracion_respuesta_s is not None]
        return sum(vals) / len(vals) if vals else 0.0


def _descargar_modelos_de_ollama(modelos: list[str]) -> None:
    """`ollama stop <modelo>` para cada uno YA CARGADO (ollama mantiene un
    modelo en RAM unos minutos tras el último uso, `keep_alive`) -- sin
    esto, el RSS "de carga" de un modelo quedaría contaminado por el
    modelo de la corrida anterior todavía residente (se midió así al
    validar este script: ~10GB combinados en vez del RSS real de un solo
    modelo, ver docs/FASE5_REPORTE.md)."""
    for modelo in modelos:
        try:
            subprocess.run(["ollama", "stop", modelo], capture_output=True, timeout=15)
        except (subprocess.SubprocessError, FileNotFoundError):
            pass


def correr_benchmark_modelo(
    modelo: str, db_path: Path, timeout_pregunta: float, al_terminar_pregunta=None, reintento: bool = True,
) -> ResultadoModelo:
    _descargar_modelos_de_ollama(client.modelos_disponibles())
    con = abrir_conexion_solo_lectura(str(db_path))
    monitor = Monitor()
    monitor.iniciar()
    t0 = time.perf_counter()
    try:
        descripcion_esquema = construir_descripcion_esquema(con)
        resultados = []
        for pregunta in PREGUNTAS:
            r = _correr_pregunta(con, modelo, descripcion_esquema, pregunta, timeout_pregunta, reintento)
            resultados.append(r)
            if al_terminar_pregunta is not None:
                al_terminar_pregunta(modelo, r)
    finally:
        con.close()
        monitor.detener()
    resumen_mem = monitor.resumen()
    return ResultadoModelo(
        modelo=modelo,
        preguntas=resultados,
        rss_ollama_max_kb=resumen_mem.get("rss_ollama_max_kb", 0),
        duracion_total_s=time.perf_counter() - t0,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.benchmark_modelos_llm")
    parser.add_argument("--db", default=str(DB_PATH_DEFAULT))
    parser.add_argument("--timeout-pregunta", type=float, default=TIMEOUT_PREGUNTA_DEFAULT)
    parser.add_argument("--out", default="docs/evidencia/benchmark_modelos.json")
    parser.add_argument("--modelos", default=None, help="Coma-separados; por default usa los de 'ollama list'")
    parser.add_argument("--sin-reintento", action="store_true",
                        help="Mide solo el primer intento (como la corrida original de los 3 modelos)")
    args = parser.parse_args(argv)

    modelos = args.modelos.split(",") if args.modelos else client.modelos_disponibles()
    print(f"Modelos detectados por 'ollama list': {modelos}")

    resultados_por_modelo: dict[str, ResultadoModelo] = {}
    for modelo in modelos:
        print(f"\n=== {modelo} ===")

        def _avance(m, r, _modelo=modelo):
            marca = "OK" if (r.sql_valido or r.rechazo_correcto) else "FALLO"
            print(f"  [{_modelo}] [{marca}] intentos={r.intentos} sql={r.duracion_sql_s or 0:.1f}s "
                  f"resp={r.duracion_respuesta_s or 0:.1f}s — {r.pregunta}")

        resultado = correr_benchmark_modelo(modelo, Path(args.db), args.timeout_pregunta, al_terminar_pregunta=_avance,
                                            reintento=not args.sin_reintento)
        resultados_por_modelo[modelo] = resultado
        print(f"  Resumen {modelo}: tasa_sql_valido={resultado.tasa_sql_valido:.0%} "
              f"tasa_sql_valido_final={resultado.tasa_sql_valido_final:.0%} "
              f"con_reintento={resultado.n_con_reintento} "
              f"tablas_reales={resultado.n_tablas_reales_ok}/{resultado.n_con_sql} "
              f"trampas_rechazadas={resultado.n_trampas_rechazadas}/2 "
              f"RSS_ollama_pico={resultado.rss_ollama_max_kb / 1024:.0f}MB "
              f"tiempo_total={resultado.duracion_total_s:.1f}s")

    print("\n=== TABLA COMPARATIVA ===")
    encabezado = f"{'modelo':<22}{'tasa_sql':>10}{'tablas_ok':>11}{'trampas':>9}{'t_sql_s':>9}{'t_resp_s':>10}{'rss_MB':>9}"
    print(encabezado)
    for modelo, r in resultados_por_modelo.items():
        print(f"{modelo:<22}{r.tasa_sql_valido:>9.0%} {r.n_tablas_reales_ok:>3}/{r.n_con_sql:<6} "
              f"{r.n_trampas_rechazadas:>6}/2 {r.tiempo_medio_sql_s:>8.1f} {r.tiempo_medio_respuesta_s:>9.1f} "
              f"{r.rss_ollama_max_kb / 1024:>8.0f}")

    salida = {
        modelo: {
            "modelo": r.modelo,
            "duracion_total_s": r.duracion_total_s,
            "rss_ollama_max_kb": r.rss_ollama_max_kb,
            "reintento_activo": not args.sin_reintento,
            "tasa_sql_valido": r.tasa_sql_valido,
            "tasa_sql_valido_final": r.tasa_sql_valido_final,
            "n_con_reintento": r.n_con_reintento,
            "n_tablas_reales_ok": r.n_tablas_reales_ok,
            "n_con_sql": r.n_con_sql,
            "n_trampas_rechazadas": r.n_trampas_rechazadas,
            "tiempo_medio_sql_s": r.tiempo_medio_sql_s,
            "tiempo_medio_respuesta_s": r.tiempo_medio_respuesta_s,
            "preguntas": [asdict(p) for p in r.preguntas],
        }
        for modelo, r in resultados_por_modelo.items()
    }
    ruta_out = Path(args.out)
    ruta_out.parent.mkdir(parents=True, exist_ok=True)
    ruta_out.write_text(json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON completo en {ruta_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
