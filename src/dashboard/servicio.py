"""Capa de servicio del dashboard: lee `ResultadoNecesidad` ya calculados
desde disco (`data/referencia/{id}.json`), con caché validada contra la
huella de la BD fuente. Este módulo NO importa `streamlit` (decisión de
Fase 3: `src/analytics/` y esta capa de servicio siguen sin saber nada de
la UI) y `src/ui/dashboard.py` nunca debe llamar a
`src.analytics.*.calcular()` ni a `invocar()` directamente: siempre pasa
por aquí.

Regla de caché: cada JSON de `data/referencia/` ya incluye el campo
`huella_bd` (conteos de `orders`/`order_products__prior`/
`order_products__train` en el momento en que se calculó, ver
`src.analytics.contrato.HuellaBD`). `cargar_necesidad()` compara ese
`huella_bd` guardado contra `huella_bd(con)` recién medido sobre la BD
fuente actual (el mismo mecanismo que ya usa `agregados.abrir_agregado()`
para detectar un agregado "rancio", ver `docs/FASE2B_REPORTE.md`). Si
coinciden, el JSON es la fuente de verdad y no se ejecuta ninguna consulta
analítica pesada (N10/N11 miden hasta 6-8s en el peor caso medido en
Fase 2B: no pueden correr en cada render de Streamlit). Si no coinciden (o
el archivo no existe), se recalcula con `invocar()` y se reescribe el JSON.

Nota de nombres: el enunciado de esta fase describe la condición de caché
como "su huella_contenido coincide con la huella actual de la BD"; el
campo que en efecto se compara contra la BD es `huella_bd` (`huella_bd del
contrato`, como aclara el propio enunciado entre paréntesis) -- ver
"Desviaciones" en `docs/FASE3_REPORTE.md`. `huella_contenido` (el sha256
de Fase 2B/C4) fingerprints el CONTENIDO del resultado para comparar dos
corridas entre sí (determinismo); no sirve como chequeo barato de
vigencia de caché porque calcularlo de nuevo exigiría recalcular la
necesidad completa, justo lo que la caché quiere evitar.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import duckdb

from src.analytics.conexion import abrir_conexion, huella_bd
from src.analytics.contrato import ResultadoNecesidad
from src.analytics.registro import NECESIDADES, invocar

DB_PATH_DEFAULT = Path("data/instacart.duckdb")
AGG_PATH_DEFAULT = Path("data/instacart_agg.duckdb")
OUT_DIR_DEFAULT = Path("data/referencia")

CAMPOS_HUELLA_BD = ("orders_total", "orders_test", "order_products_prior", "order_products_train")


@dataclass
class CargaNecesidad:
    """Resultado de `cargar_necesidad()`: el `ResultadoNecesidad` más los
    metadatos de caché que la UI necesita mostrar (punto 2 del brief:
    "un aviso si el dato viene de caché vs. recién calculado")."""

    resultado: ResultadoNecesidad
    desde_cache: bool
    duracion_carga_s: float


@dataclass
class EstadoNecesidad:
    id: str
    recalculada: bool
    duracion_s: float
    error: str | None = None


@dataclass
class ResumenRefresco:
    estados: list[EstadoNecesidad] = field(default_factory=list)
    duracion_total_s: float = 0.0

    @property
    def n_recalculadas(self) -> int:
        return sum(1 for e in self.estados if e.recalculada)

    @property
    def n_errores(self) -> int:
        return sum(1 for e in self.estados if e.error is not None)


def _ruta_json(nid: str, out_dir: Path) -> Path:
    return out_dir / f"{nid}.json"


def _leer_json_si_existe(ruta: Path) -> dict | None:
    if not ruta.exists():
        return None
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # JSON corrupto/incompleto (p. ej. un guardado interrumpido a
        # mitad de escritura): se trata como "no hay caché", no como error.
        return None


def _huella_de_json(data: dict) -> dict[str, int] | None:
    hb = data.get("huella_bd")
    if not hb:
        return None
    try:
        return {campo: hb[campo] for campo in CAMPOS_HUELLA_BD}
    except KeyError:
        return None


def _cache_vigente(data: dict | None, huella_actual: dict[str, int]) -> bool:
    if data is None:
        return False
    return _huella_de_json(data) == huella_actual


def cargar_necesidad(
    necesidad_id: str,
    *,
    db_path: Path | None = None,
    agg_path: Path | None = None,
    out_dir: Path | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> CargaNecesidad:
    """Carga una necesidad, sirviendo desde `data/referencia/{id}.json`
    cuando su `huella_bd` coincide con la de la BD fuente actual.

    `db_path`/`agg_path`/`out_dir` en `None` (el default) se resuelven en
    el momento de la llamada contra `DB_PATH_DEFAULT`/`AGG_PATH_DEFAULT`/
    `OUT_DIR_DEFAULT` del módulo -- a propósito, para que un test pueda
    monkeypatchear esos globales (p. ej. apuntando a una fixture chica en
    `tmp_path`) y que tanto llamadas directas como las que hace
    `src/ui/dashboard.py` (que nunca pasa estos argumentos) usen la ruta
    parcheada, sin depender de un valor por default ya congelado al
    definir la función.

    `con`: conexión ya abierta opcional (para pruebas, o para que
    `refrescar_todas()` reuse una sola conexión en vez de abrir 11). Si no
    se pasa, se abre una de solo lectura y se cierra al terminar.
    """
    if necesidad_id not in NECESIDADES:
        raise ValueError(f"Necesidad desconocida: {necesidad_id}. Disponibles: {sorted(NECESIDADES)}")
    db_path = db_path if db_path is not None else DB_PATH_DEFAULT
    agg_path = agg_path if agg_path is not None else AGG_PATH_DEFAULT
    out_dir = out_dir if out_dir is not None else OUT_DIR_DEFAULT

    t_inicio = time.perf_counter()
    ruta = _ruta_json(necesidad_id, out_dir)
    data_en_disco = _leer_json_si_existe(ruta)

    con_propia = con is None
    if con_propia:
        con = abrir_conexion(db_path)
    try:
        huella_actual = huella_bd(con)
        if _cache_vigente(data_en_disco, huella_actual):
            resultado = ResultadoNecesidad.desde_dict(data_en_disco)
            return CargaNecesidad(
                resultado=resultado,
                desde_cache=True,
                duracion_carga_s=time.perf_counter() - t_inicio,
            )

        resultado = invocar(necesidad_id, con, agg_path)
        resultado.guardar_json(ruta)
        return CargaNecesidad(
            resultado=resultado,
            desde_cache=False,
            duracion_carga_s=time.perf_counter() - t_inicio,
        )
    finally:
        if con_propia:
            con.close()


def refrescar_todas(
    *,
    forzar: bool = False,
    db_path: Path | None = None,
    agg_path: Path | None = None,
    out_dir: Path | None = None,
    al_terminar_cada_una: Callable[[EstadoNecesidad], None] | None = None,
) -> ResumenRefresco:
    """Recorre las 11 necesidades con UNA sola conexión. `forzar=True`
    ignora la caché y recalcula las 11 (usado por la prueba de
    concurrencia y por el botón "Actualizar datos" de la UI cuando el
    usuario pide un refresco explícito); `forzar=False` solo recalcula
    las que estén ausentes o rancias. `al_terminar_cada_una`, si se pasa,
    se invoca después de cada necesidad (recalculada o no) para que la UI
    pueda mostrar progreso incremental sin esperar a que terminen las 11.

    `db_path`/`agg_path`/`out_dir` en `None` se resuelven en el momento de
    la llamada (mismo motivo que en `cargar_necesidad`)."""
    db_path = db_path if db_path is not None else DB_PATH_DEFAULT
    agg_path = agg_path if agg_path is not None else AGG_PATH_DEFAULT
    out_dir = out_dir if out_dir is not None else OUT_DIR_DEFAULT
    t_inicio_total = time.perf_counter()
    resumen = ResumenRefresco()

    con = abrir_conexion(db_path)
    try:
        huella_actual = huella_bd(con)
        for nid in sorted(NECESIDADES):
            t_inicio = time.perf_counter()
            ruta = _ruta_json(nid, out_dir)
            try:
                data_en_disco = None if forzar else _leer_json_si_existe(ruta)
                if not forzar and _cache_vigente(data_en_disco, huella_actual):
                    estado = EstadoNecesidad(id=nid, recalculada=False, duracion_s=time.perf_counter() - t_inicio)
                else:
                    resultado = invocar(nid, con, agg_path)
                    resultado.guardar_json(ruta)
                    estado = EstadoNecesidad(id=nid, recalculada=True, duracion_s=time.perf_counter() - t_inicio)
            except Exception as exc:  # noqa: BLE001 - una necesidad no debe tumbar el refresco de las demás
                estado = EstadoNecesidad(
                    id=nid, recalculada=False, duracion_s=time.perf_counter() - t_inicio, error=str(exc),
                )
            resumen.estados.append(estado)
            if al_terminar_cada_una is not None:
                al_terminar_cada_una(estado)
    finally:
        con.close()

    resumen.duracion_total_s = time.perf_counter() - t_inicio_total
    return resumen
