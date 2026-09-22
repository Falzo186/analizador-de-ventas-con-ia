"""Prueba de concurrencia: dashboard (refrescar_todas) + chat (Ollama) a la
vez. Pendiente desde Fase 1/2A/2B, cerrada en Fase 3.

Mide, con el caso más pesado posible (`refrescar_todas(forzar=True)`, que
recalcula las 11 necesidades incluidas N10/N11), si correr eso AL MISMO
TIEMPO que alguien usa el chat de Ollama causa timeouts, lentitud extrema,
o RSS combinado por encima de 2.5GB / agotamiento de la RAM libre
(~10GB medidos en Fase 1, ver `docs/FASE1_REPORTE.md`).

No reabre el diseño de N10/N11 (Fase 2B ya lo dejó cerrado): esto es una
MEDICIÓN, para decidir en Fase 5 (LLM) cuánta RAM reservarle al modelo.

Uso:
    python -m scripts.prueba_concurrencia
    python -m scripts.prueba_concurrencia --n-preguntas 5 --out docs/evidencia_concurrencia.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturoTimeoutError
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dashboard import servicio  # noqa: E402
from src.db.database import crear_base_de_datos  # noqa: E402
from src.llm.client import modelo_actual  # noqa: E402
from src.llm.responder import responder_pregunta  # noqa: E402

PREGUNTAS_FIJAS = [
    "¿Cuántos pedidos hay en total?",
    "¿Cuál es el producto más vendido?",
    "¿Cuántos clientes distintos hay?",
    "¿Qué categoría de producto tiene más unidades vendidas?",
    "¿Cuál es el pedido con más artículos distintos?",
]

TIMEOUT_PREGUNTA_S = 90.0
INTERVALO_MUESTREO_S = 0.5


# ---------------------------------------------------------------------------
# Medición de memoria vía /proc (Linux; sin agregar psutil como dependencia
# nueva solo para esta prueba puntual)
# ---------------------------------------------------------------------------

def _leer_meminfo_kb() -> dict[str, int]:
    datos: dict[str, int] = {}
    for linea in Path("/proc/meminfo").read_text().splitlines():
        clave, _, resto = linea.partition(":")
        partes = resto.strip().split()
        if partes and partes[0].isdigit():
            datos[clave] = int(partes[0])
    return datos


def _rss_propio_kb() -> int:
    for linea in Path("/proc/self/status").read_text().splitlines():
        if linea.startswith("VmRSS:"):
            return int(linea.split()[1])
    return 0


def _pids_ollama() -> list[int]:
    try:
        salida = subprocess.run(
            ["pgrep", "-f", "ollama"], capture_output=True, text=True, timeout=5,
        )
        return [int(p) for p in salida.stdout.split() if p.strip()]
    except (subprocess.SubprocessError, FileNotFoundError):
        return []


def _rss_pids_kb(pids: list[int]) -> int:
    total = 0
    for pid in pids:
        try:
            for linea in Path(f"/proc/{pid}/status").read_text().splitlines():
                if linea.startswith("VmRSS:"):
                    total += int(linea.split()[1])
                    break
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return total


@dataclass
class Muestra:
    t_s: float
    mem_total_kb: int
    mem_disponible_kb: int
    rss_propio_kb: int
    rss_ollama_kb: int

    @property
    def mem_usada_kb(self) -> int:
        return self.mem_total_kb - self.mem_disponible_kb

    @property
    def rss_combinado_kb(self) -> int:
        """RSS propio (este proceso, donde corre refrescar_todas) + RSS de
        los procesos de Ollama -- el "combinado" que el brief pide comparar
        contra 2.5GB."""
        return self.rss_propio_kb + self.rss_ollama_kb


class Monitor:
    def __init__(self, intervalo: float = INTERVALO_MUESTREO_S) -> None:
        self.intervalo = intervalo
        self.muestras: list[Muestra] = []
        self._detener = threading.Event()
        self._hilo: threading.Thread | None = None

    def _bucle(self) -> None:
        while not self._detener.is_set():
            mem = _leer_meminfo_kb()
            self.muestras.append(Muestra(
                t_s=time.perf_counter(),
                mem_total_kb=mem.get("MemTotal", 0),
                mem_disponible_kb=mem.get("MemAvailable", 0),
                rss_propio_kb=_rss_propio_kb(),
                rss_ollama_kb=_rss_pids_kb(_pids_ollama()),
            ))
            self._detener.wait(self.intervalo)

    def iniciar(self) -> None:
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()
        if self._hilo is not None:
            self._hilo.join(timeout=5)

    def resumen(self) -> dict:
        if not self.muestras:
            return {}
        return {
            "n_muestras": len(self.muestras),
            "mem_disponible_min_kb": min(m.mem_disponible_kb for m in self.muestras),
            "mem_usada_max_kb": max(m.mem_usada_kb for m in self.muestras),
            "rss_propio_max_kb": max(m.rss_propio_kb for m in self.muestras),
            "rss_ollama_max_kb": max(m.rss_ollama_kb for m in self.muestras),
            "rss_combinado_max_kb": max(m.rss_combinado_kb for m in self.muestras),
        }


# ---------------------------------------------------------------------------
# Las dos cargas de trabajo
# ---------------------------------------------------------------------------

def correr_analytics(forzar: bool = True, etiqueta: str = "analytics") -> dict:
    t0 = time.perf_counter()

    def _avance(estado: servicio.EstadoNecesidad) -> None:
        marca = "recalculada" if estado.recalculada else "vigente"
        if estado.error:
            marca = f"ERROR: {estado.error}"
        print(f"  [{etiqueta}] {estado.id}: {marca} ({estado.duracion_s:.2f}s, "
              f"t+{time.perf_counter() - t0:.1f}s)", flush=True)

    resumen = servicio.refrescar_todas(forzar=forzar, al_terminar_cada_una=_avance)
    duracion_total = time.perf_counter() - t0
    return {
        "duracion_total_s": duracion_total,
        "n_recalculadas": resumen.n_recalculadas,
        "n_errores": resumen.n_errores,
        "por_necesidad": {e.id: {"duracion_s": e.duracion_s, "recalculada": e.recalculada, "error": e.error}
                           for e in resumen.estados},
    }


def _responder_con_timeout(pregunta: str, timeout: float) -> dict:
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as ex:
        futuro = ex.submit(responder_pregunta, pregunta, True)
        try:
            res = futuro.result(timeout=timeout)
            duracion = time.perf_counter() - t0
            return {
                "pregunta": pregunta, "ok": res.ok, "duracion_s": duracion,
                "error": res.error, "timeout": False,
            }
        except FuturoTimeoutError:
            duracion = time.perf_counter() - t0
            return {
                "pregunta": pregunta, "ok": False, "duracion_s": duracion,
                "error": f"timeout tras {timeout:.0f}s sin respuesta", "timeout": True,
            }
        except Exception as exc:  # noqa: BLE001
            duracion = time.perf_counter() - t0
            return {
                "pregunta": pregunta, "ok": False, "duracion_s": duracion,
                "error": str(exc), "timeout": False,
            }


def correr_ollama(preguntas: list[str], timeout: float = TIMEOUT_PREGUNTA_S, etiqueta: str = "ollama") -> dict:
    crear_base_de_datos()
    t0 = time.perf_counter()
    respuestas = []
    for p in preguntas:
        r = _responder_con_timeout(p, timeout)
        respuestas.append(r)
        marca = "TIMEOUT" if r["timeout"] else ("OK" if r["ok"] else "ERROR")
        print(f"  [{etiqueta}] [{marca}] {r['duracion_s']:.2f}s (t+{time.perf_counter() - t0:.1f}s) — {p}",
              flush=True)
    duracion_total = time.perf_counter() - t0
    return {
        "modelo": modelo_actual(),
        "duracion_total_s": duracion_total,
        "n_preguntas": len(preguntas),
        "n_ok": sum(1 for r in respuestas if r["ok"]),
        "n_timeouts": sum(1 for r in respuestas if r["timeout"]),
        "respuestas": respuestas,
    }


# ---------------------------------------------------------------------------
# Orquestación: aislado (uno a la vez) y concurrente (los dos juntos)
# ---------------------------------------------------------------------------

def correr_aislado(preguntas: list[str], timeout_pregunta: float) -> dict:
    mon_analytics = Monitor()
    mon_analytics.iniciar()
    analytics = correr_analytics(forzar=True, etiqueta="aislado-analytics")
    mon_analytics.detener()

    mon_ollama = Monitor()
    mon_ollama.iniciar()
    ollama = correr_ollama(preguntas, timeout_pregunta, etiqueta="aislado-ollama")
    mon_ollama.detener()

    return {
        "analytics": {**analytics, "memoria": mon_analytics.resumen()},
        "ollama": {**ollama, "memoria": mon_ollama.resumen()},
    }


def correr_concurrente(preguntas: list[str], timeout_pregunta: float) -> dict:
    monitor = Monitor()
    monitor.iniciar()

    resultado_analytics: dict = {}
    resultado_ollama: dict = {}

    def _hilo_analytics() -> None:
        resultado_analytics.update(correr_analytics(forzar=True, etiqueta="concurrente-analytics"))

    def _hilo_ollama() -> None:
        resultado_ollama.update(correr_ollama(preguntas, timeout_pregunta, etiqueta="concurrente-ollama"))

    t0 = time.perf_counter()
    h1 = threading.Thread(target=_hilo_analytics)
    h2 = threading.Thread(target=_hilo_ollama)
    h1.start()
    h2.start()
    h1.join()
    h2.join()
    duracion_pared = time.perf_counter() - t0

    monitor.detener()

    return {
        "duracion_pared_s": duracion_pared,
        "analytics": resultado_analytics,
        "ollama": resultado_ollama,
        "memoria_combinada": monitor.resumen(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.prueba_concurrencia")
    parser.add_argument("--n-preguntas", type=int, default=len(PREGUNTAS_FIJAS))
    parser.add_argument("--timeout-pregunta", type=float, default=TIMEOUT_PREGUNTA_S)
    parser.add_argument("--out", default=None, help="Ruta para volcar el JSON completo")
    parser.add_argument("--solo-concurrente", action="store_true",
                        help="Salta la corrida aislada (más rápido, para iterar)")
    args = parser.parse_args(argv)

    preguntas = PREGUNTAS_FIJAS[: args.n_preguntas]

    resultado: dict = {"modelo": modelo_actual(), "n_preguntas": len(preguntas), "preguntas": preguntas}

    if not args.solo_concurrente:
        print("== Corrida AISLADA (analytics, luego Ollama, uno a la vez) ==")
        aislado = correr_aislado(preguntas, args.timeout_pregunta)
        resultado["aislado"] = aislado
        print(f"  analytics: {aislado['analytics']['duracion_total_s']:.2f}s total, "
              f"{aislado['analytics']['n_recalculadas']} recalculadas, "
              f"{aislado['analytics']['n_errores']} errores")
        print(f"  ollama: {aislado['ollama']['duracion_total_s']:.2f}s total, "
              f"{aislado['ollama']['n_ok']}/{aislado['ollama']['n_preguntas']} ok, "
              f"{aislado['ollama']['n_timeouts']} timeouts")

    print("\n== Corrida CONCURRENTE (analytics + Ollama al mismo tiempo) ==")
    concurrente = correr_concurrente(preguntas, args.timeout_pregunta)
    resultado["concurrente"] = concurrente
    print(f"  duración de pared: {concurrente['duracion_pared_s']:.2f}s")
    print(f"  analytics: {concurrente['analytics']['duracion_total_s']:.2f}s total, "
          f"{concurrente['analytics']['n_recalculadas']} recalculadas, "
          f"{concurrente['analytics']['n_errores']} errores")
    for nid, info in sorted(concurrente["analytics"]["por_necesidad"].items()):
        print(f"    {nid}: {info['duracion_s']:.2f}s" + (f" ERROR: {info['error']}" if info["error"] else ""))
    print(f"  ollama: {concurrente['ollama']['duracion_total_s']:.2f}s total, "
          f"{concurrente['ollama']['n_ok']}/{concurrente['ollama']['n_preguntas']} ok, "
          f"{concurrente['ollama']['n_timeouts']} timeouts")
    for r in concurrente["ollama"]["respuestas"]:
        marca = "TIMEOUT" if r["timeout"] else ("OK" if r["ok"] else "ERROR")
        print(f"    [{marca}] {r['duracion_s']:.2f}s — {r['pregunta']}" + (f" ({r['error']})" if r["error"] else ""))

    mem = concurrente["memoria_combinada"]
    if mem:
        print("\n== Memoria durante la corrida concurrente ==")
        print(f"  RSS combinado (este proceso + ollama) pico: {mem['rss_combinado_max_kb'] / 1024:.0f} MB")
        print(f"  RSS propio (analytics) pico: {mem['rss_propio_max_kb'] / 1024:.0f} MB")
        print(f"  RSS ollama pico: {mem['rss_ollama_max_kb'] / 1024:.0f} MB")
        print(f"  Memoria usada del sistema, pico: {mem['mem_usada_max_kb'] / 1024:.0f} MB")
        print(f"  Memoria disponible del sistema, mínima: {mem['mem_disponible_min_kb'] / 1024:.0f} MB")
        cruza_2_5gb = mem["rss_combinado_max_kb"] > 2.5 * 1024 * 1024
        print(f"  ¿RSS combinado cruza 2.5GB?: {'SÍ' if cruza_2_5gb else 'no'}")

    if args.out:
        Path(args.out).write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON completo en {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
