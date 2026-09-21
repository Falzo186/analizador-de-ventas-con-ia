"""Contrato común que devuelve cada necesidad (N1..N11) de la capa analítica.

`ResultadoNecesidad` es la única forma en que una necesidad se comunica con
el resto del sistema (CLI, futuro dashboard, futuro PDF, futura comparación
con la IA): todos consumen el mismo JSON, nunca un DataFrame ni un objeto
Python a medida. Por eso el contrato es deliberadamente rígido.

Saneamiento de NaN/Infinity: JSON no tiene esos valores. Cualquier float no
finito (puede aparecer si una consulta divide 0/0 sin `NULLIF`, aunque las
consultas de este proyecto ya se escriben para devolver NULL en ese caso, no
para producir NaN) se convierte a `None` (`null` en JSON) antes de serializar
— ver `_sanear`. Se prefiere que la propia consulta SQL evite el NaN con
`NULLIF`/`CASE`, pero este saneamiento es la segunda barrera, no la primera.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Parametro:
    nombre: str
    valor: Any
    justificacion: str


@dataclass(frozen=True)
class Consulta:
    nombre: str
    sql: str


@dataclass(frozen=True)
class ValorClave:
    etiqueta: str
    valor: Any
    unidad: str
    consulta_origen: str  # debe ser el `nombre` de una Consulta en `consultas`


@dataclass(frozen=True)
class Universo:
    texto: str
    filtros: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HuellaBD:
    orders_total: int
    orders_test: int
    order_products_prior: int
    order_products_train: int

    @property
    def order_products_total(self) -> int:
        return self.order_products_prior + self.order_products_train


LIMITE_FILAS_TABLA = 500


@dataclass
class ResultadoNecesidad:
    id: str
    titulo: str
    pregunta: str
    decision: str
    universo: Universo
    parametros: list[Parametro]
    consultas: list[Consulta]
    tablas: dict[str, list[dict]]
    tablas_criterio_corte: dict[str, str]
    valores_clave: list[ValorClave]
    hallazgos: list[str]
    limitaciones: list[str]
    duracion_s: float
    version_duckdb: str
    fecha_generacion: str
    huella_bd: HuellaBD

    def __post_init__(self) -> None:
        nombres_consulta = {c.nombre for c in self.consultas}
        for vc in self.valores_clave:
            if vc.consulta_origen not in nombres_consulta:
                raise ValueError(
                    f"valor_clave '{vc.etiqueta}' referencia consulta_origen "
                    f"'{vc.consulta_origen}' que no está en `consultas`"
                )
        for nombre, filas in self.tablas.items():
            if len(filas) > LIMITE_FILAS_TABLA:
                raise ValueError(
                    f"tabla '{nombre}' tiene {len(filas)} filas, excede el límite "
                    f"de {LIMITE_FILAS_TABLA}. Trunca con `limitar_tabla()` antes de construir el resultado."
                )
            if nombre not in self.tablas_criterio_corte:
                raise ValueError(f"tabla '{nombre}' no declara su criterio de corte")

    def a_dict(self) -> dict:
        data = {
            "id": self.id,
            "titulo": self.titulo,
            "pregunta": self.pregunta,
            "decision": self.decision,
            "universo": {"texto": self.universo.texto, "filtros": list(self.universo.filtros)},
            "parametros": [
                {"nombre": p.nombre, "valor": p.valor, "justificacion": p.justificacion}
                for p in self.parametros
            ],
            "consultas": [{"nombre": c.nombre, "sql": c.sql} for c in self.consultas],
            "tablas": self.tablas,
            "tablas_criterio_corte": self.tablas_criterio_corte,
            "valores_clave": [
                {
                    "etiqueta": v.etiqueta,
                    "valor": v.valor,
                    "unidad": v.unidad,
                    "consulta_origen": v.consulta_origen,
                }
                for v in self.valores_clave
            ],
            "hallazgos": list(self.hallazgos),
            "limitaciones": list(self.limitaciones),
            "duracion_s": self.duracion_s,
            "version_duckdb": self.version_duckdb,
            "fecha_generacion": self.fecha_generacion,
            "huella_bd": {
                "orders_total": self.huella_bd.orders_total,
                "orders_test": self.huella_bd.orders_test,
                "order_products_prior": self.huella_bd.order_products_prior,
                "order_products_train": self.huella_bd.order_products_train,
                "order_products_total": self.huella_bd.order_products_total,
            },
        }
        return _sanear(data)

    def a_json(self) -> str:
        return json.dumps(self.a_dict(), indent=2, ensure_ascii=False, sort_keys=True)

    def guardar_json(self, ruta: str | Path) -> None:
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(self.a_json() + "\n", encoding="utf-8")


def _sanear(obj: Any) -> Any:
    """Reemplaza NaN/Infinity por None recursivamente (JSON no los admite)."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanear(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanear(v) for v in obj]
    return obj


def limitar_tabla(filas: list[dict], limite: int = LIMITE_FILAS_TABLA) -> tuple[list[dict], bool]:
    """Trunca `filas` a `limite`. Devuelve (filas_truncadas, se_truncó)."""
    if len(filas) <= limite:
        return filas, False
    return filas[:limite], True
