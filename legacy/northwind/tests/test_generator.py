"""Pruebas del generador de ventas y gastos."""

from __future__ import annotations

import random
from datetime import date, datetime

from sqlalchemy import func, select

from src.db.database import obtener_sesion
from src.db.schema import Cliente, Gasto, Producto, Proveedor, Venta
from src.generator.catalog import total_productos
from src.generator.catalogo_negocio import NUM_CLIENTES
from src.generator.generator import (
    backfill,
    crear_venta,
    generar_gastos,
)
from src.generator.seed import (
    sembrar_catalogos,
    sembrar_clientes,
    sembrar_productos,
    sembrar_proveedores,
)


def test_sembrar_es_idempotente():
    assert sembrar_productos() == total_productos()
    assert sembrar_productos() == 0  # segunda vez no inserta nada
    with obtener_sesion() as s:
        assert s.scalar(select(func.count(Producto.id))) == total_productos()


def test_backfill_genera_ventas_en_el_rango():
    insertadas = backfill(date(2026, 7, 1), date(2026, 7, 7),
                          semilla=42, verbose=False)
    assert insertadas > 0
    with obtener_sesion() as s:
        n = s.scalar(select(func.count(Venta.id)))
        assert n == insertadas
        fmin, fmax = s.execute(
            select(func.min(Venta.fecha), func.max(Venta.fecha))
        ).first()
        assert fmin >= datetime(2026, 7, 1, 0, 0)
        assert fmax <= datetime(2026, 7, 7, 23, 59, 59)


def test_backfill_es_reproducible_con_semilla():
    a = backfill(date(2026, 7, 1), date(2026, 7, 3), semilla=7, verbose=False)
    # limpiar ventas y repetir
    with obtener_sesion() as s:
        s.query(Venta).delete()
        s.commit()
    b = backfill(date(2026, 7, 1), date(2026, 7, 3), semilla=7, verbose=False)
    assert a == b


def test_total_de_cada_venta_es_coherente():
    sembrar_productos()
    with obtener_sesion() as s:
        rng = random.Random(1)
        for _ in range(50):
            v = crear_venta(s, datetime(2026, 7, 1, 12, 0), rng)
            assert v.total == round(v.precio_unitario * v.cantidad, 2)
            assert v.cantidad >= 1
            assert v.categoria in {"tecnología", "ropa", "alimentos", "hogar"}
        s.commit()


def test_rechaza_rango_invertido():
    import pytest

    with pytest.raises(ValueError):
        backfill(date(2026, 7, 10), date(2026, 7, 1), verbose=False)


# --------------------------------------------------------------- catálogos

def test_sembrar_catalogos_es_idempotente():
    r1 = sembrar_catalogos()
    assert r1["clientes"] == NUM_CLIENTES
    assert r1["proveedores"] == 4
    r2 = sembrar_catalogos()
    assert r2 == {"productos": 0, "clientes": 0, "proveedores": 0}
    with obtener_sesion() as s:
        assert s.scalar(select(func.count(Cliente.id))) == NUM_CLIENTES
        assert s.scalar(select(func.count(Proveedor.id))) == 4


def test_sembrar_clientes_y_proveedores_por_separado():
    assert sembrar_clientes() == NUM_CLIENTES
    assert sembrar_clientes() == 0
    assert sembrar_proveedores() == 4
    assert sembrar_proveedores() == 0


# ------------------------------------------------------ asignación de cliente

def test_sin_clientes_la_venta_es_de_mostrador():
    sembrar_productos()  # NO siembra clientes
    with obtener_sesion() as s:
        rng = random.Random(0)
        for _ in range(20):
            v = crear_venta(s, datetime(2026, 7, 1, 12, 0), rng)
            assert v.cliente_id is None
        s.commit()


def test_backfill_asigna_cliente_a_una_parte_de_las_ventas():
    backfill(date(2026, 7, 1), date(2026, 7, 20), semilla=1, verbose=False)
    with obtener_sesion() as s:
        total = s.scalar(select(func.count(Venta.id)))
        con_cliente = s.scalar(
            select(func.count(Venta.id)).where(Venta.cliente_id.is_not(None))
        )
        # PROB_CLIENTE = 0.60; con cientos de ventas la fracción ronda eso.
        frac = con_cliente / total
        assert 0.45 < frac < 0.75
        # todos los cliente_id apuntan a clientes reales
        ids_validos = set(s.scalars(select(Cliente.id)))
        usados = set(s.scalars(
            select(Venta.cliente_id).where(Venta.cliente_id.is_not(None))
        ))
        assert usados <= ids_validos


# ----------------------------------------------------------------- gastos

def test_generar_gastos_uno_por_concepto_por_mes():
    n = generar_gastos(date(2026, 3, 1), date(2026, 8, 31), semilla=1, verbose=False)
    assert n == 6 * 4  # 6 meses x 4 conceptos
    with obtener_sesion() as s:
        assert s.scalar(select(func.count(Gasto.id))) == 24
        # renta fija: mismo monto todos los meses
        rentas = list(s.scalars(
            select(Gasto.monto).where(Gasto.categoria_gasto == "renta")
        ))
        assert len(rentas) == 6 and len(set(rentas)) == 1
        # nómina variable: no todos los meses iguales
        nominas = list(s.scalars(
            select(Gasto.monto).where(Gasto.categoria_gasto == "nómina")
        ))
        assert len(set(nominas)) > 1


def test_generar_gastos_es_idempotente():
    generar_gastos(date(2026, 3, 1), date(2026, 5, 31), semilla=1, verbose=False)
    n2 = generar_gastos(date(2026, 3, 1), date(2026, 5, 31), semilla=1, verbose=False)
    assert n2 == 0


def test_generar_gastos_reproducible_con_semilla():
    a = generar_gastos(date(2026, 3, 1), date(2026, 4, 30), semilla=9, verbose=False)
    with obtener_sesion() as s:
        montos_a = sorted(s.scalars(select(Gasto.monto)))
        s.query(Gasto).delete()
        s.commit()
    generar_gastos(date(2026, 3, 1), date(2026, 4, 30), semilla=9, verbose=False)
    with obtener_sesion() as s:
        montos_b = sorted(s.scalars(select(Gasto.monto)))
    assert a == 8
    assert montos_a == montos_b
