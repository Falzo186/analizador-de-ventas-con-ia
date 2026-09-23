"""Pruebas de los catálogos del negocio (clientes, proveedores, gastos)."""

from __future__ import annotations

from src.generator.catalog import categorias
from src.generator.catalogo_negocio import (
    GASTOS_RECURRENTES,
    NUM_CLIENTES,
    categorias_gasto,
    clientes_fijos,
    proveedores_fijos,
)


def test_hay_unos_30_clientes_con_nombre_y_ciudad():
    clientes = clientes_fijos()
    assert NUM_CLIENTES >= 30
    assert len(clientes) == NUM_CLIENTES
    for c in clientes:
        assert c["nombre"] and c["ciudad"] and c["fecha_registro"]


def test_clientes_son_reproducibles():
    a = [c["nombre"] for c in clientes_fijos()]
    b = [c["nombre"] for c in clientes_fijos()]
    assert a == b


def test_un_proveedor_por_categoria():
    provs = proveedores_fijos()
    assert len(provs) == len(categorias())
    assert {p["categoria_que_provee"] for p in provs} == set(categorias())
    for p in provs:
        assert "@" in p["contacto"]


def test_gastos_recurrentes_cubren_las_cuatro_categorias():
    cats = set(categorias_gasto())
    assert cats == {"renta", "nómina", "mercancía", "servicios"}


def test_renta_es_fija_y_los_demas_variables():
    por_cat = {g["categoria_gasto"]: g for g in GASTOS_RECURRENTES}
    assert por_cat["renta"]["monto_min"] == por_cat["renta"]["monto_max"]
    for cat in ("nómina", "mercancía", "servicios"):
        assert por_cat[cat]["monto_min"] < por_cat[cat]["monto_max"]
