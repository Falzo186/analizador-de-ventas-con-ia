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

Huella de contenido (Fase 2B, C4): `huella_contenido` es un sha256 de la
serialización canónica (JSON con `sort_keys=True`, sin indentación) del
resto del documento, EXCLUYENDO los campos volátiles listados en
`CAMPOS_VOLATILES` (cambian entre corridas por diseño: `fecha_generacion`
es un timestamp, `duracion_s` depende de la carga de la máquina). Dos
corridas con los mismos datos y parámetros deben producir la misma huella;
el test de determinismo la compara y además verifica que el resto del JSON
sea idéntico byte a byte. Documentado también en docs/NECESIDADES.md.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CAMPOS_VOLATILES: tuple[str, ...] = ("fecha_generacion", "duracion_s")


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
        data = _sanear(data)
        data["huella_contenido"] = _huella_contenido(data)
        return data

    def a_json(self) -> str:
        return json.dumps(self.a_dict(), indent=2, ensure_ascii=False, sort_keys=True)

    def guardar_json(self, ruta: str | Path) -> None:
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(self.a_json() + "\n", encoding="utf-8")

    @classmethod
    def desde_dict(cls, data: dict) -> "ResultadoNecesidad":
        """Reconstruye un `ResultadoNecesidad` desde el dict que produce
        `a_dict()` (p. ej. leído de `data/referencia/{id}.json`). Ignora
        `huella_contenido` (no es un campo del dataclass, se recalcula al
        volver a serializar) y `huella_bd.order_products_total` (propiedad
        derivada, no almacenada). `__post_init__` revalida la consistencia
        interna (valores_clave -> consultas, tablas -> criterio de corte) al
        reconstruir, igual que al construirlo desde cero — sirve también
        como chequeo de integridad de un JSON leído de disco (Fase 3,
        `src/dashboard/servicio.py`)."""
        u = data["universo"]
        hb = data["huella_bd"]
        return cls(
            id=data["id"],
            titulo=data["titulo"],
            pregunta=data["pregunta"],
            decision=data["decision"],
            universo=Universo(texto=u["texto"], filtros=list(u["filtros"])),
            parametros=[
                Parametro(nombre=p["nombre"], valor=p["valor"], justificacion=p["justificacion"])
                for p in data["parametros"]
            ],
            consultas=[Consulta(nombre=c["nombre"], sql=c["sql"]) for c in data["consultas"]],
            tablas=data["tablas"],
            tablas_criterio_corte=data["tablas_criterio_corte"],
            valores_clave=[
                ValorClave(
                    etiqueta=v["etiqueta"], valor=v["valor"], unidad=v["unidad"],
                    consulta_origen=v["consulta_origen"],
                )
                for v in data["valores_clave"]
            ],
            hallazgos=list(data["hallazgos"]),
            limitaciones=list(data["limitaciones"]),
            duracion_s=data["duracion_s"],
            version_duckdb=data["version_duckdb"],
            fecha_generacion=data["fecha_generacion"],
            huella_bd=HuellaBD(
                orders_total=hb["orders_total"],
                orders_test=hb["orders_test"],
                order_products_prior=hb["order_products_prior"],
                order_products_train=hb["order_products_train"],
            ),
        )

    @classmethod
    def desde_json(cls, ruta: str | Path) -> "ResultadoNecesidad":
        return cls.desde_dict(json.loads(Path(ruta).read_text(encoding="utf-8")))


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


def _huella_contenido(data_sin_huella: dict) -> str:
    """sha256 de `data_sin_huella` sin los campos de `CAMPOS_VOLATILES`, en
    serialización canónica (sort_keys, sin indentación)."""
    sin_volatiles = {k: v for k, v in data_sin_huella.items() if k not in CAMPOS_VOLATILES}
    canonico = json.dumps(sin_volatiles, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def limitar_tabla(filas: list[dict], limite: int = LIMITE_FILAS_TABLA) -> tuple[list[dict], bool]:
    """Trunca `filas` a `limite`. Devuelve (filas_truncadas, se_truncó)."""
    if len(filas) <= limite:
        return filas, False
    return filas[:limite], True
