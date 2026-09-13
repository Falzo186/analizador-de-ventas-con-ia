"""Pruebas del esquema ampliado: clientes, proveedores, gastos, ventas.cliente_id."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import inspect

from src.db.database import engine, obtener_sesion
from src.db.schema import Cliente, Gasto, Producto, Proveedor, Venta


def _columnas(tabla: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(tabla)}


def test_existen_las_tablas_nuevas():
    tablas = set(inspect(engine).get_table_names())
    assert {"clientes", "proveedores", "gastos", "ventas", "productos"} <= tablas


def test_columnas_de_clientes_proveedores_gastos():
    assert _columnas("clientes") >= {"id", "nombre", "ciudad", "fecha_registro"}
    assert _columnas("proveedores") >= {
        "id", "nombre", "categoria_que_provee", "contacto"
    }
    assert _columnas("gastos") >= {
        "id", "fecha", "concepto", "categoria_gasto", "monto"
    }


def test_ventas_tiene_cliente_id_nullable():
    cols = {c["name"]: c for c in inspect(engine).get_columns("ventas")}
    assert "cliente_id" in cols
    assert cols["cliente_id"]["nullable"] is True


def test_venta_puede_guardarse_sin_cliente():
    with obtener_sesion() as s:
        p = Producto(nombre="X", categoria="ropa", precio_base=10.0)
        s.add(p)
        s.flush()
        s.add(Venta(
            fecha=datetime(2026, 5, 1, 12, 0), producto_id=p.id, cliente_id=None,
            producto="X", categoria="ropa", cantidad=1,
            precio_unitario=10.0, total=10.0,
        ))
        s.commit()
        v = s.query(Venta).one()
        assert v.cliente_id is None


def test_venta_con_cliente_enlaza_bien():
    with obtener_sesion() as s:
        p = Producto(nombre="X", categoria="ropa", precio_base=10.0)
        c = Cliente(nombre="Ana", ciudad="Puebla",
                    fecha_registro=datetime(2025, 1, 1))
        s.add_all([p, c])
        s.flush()
        s.add(Venta(
            fecha=datetime(2026, 5, 1, 12, 0), producto_id=p.id, cliente_id=c.id,
            producto="X", categoria="ropa", cantidad=1,
            precio_unitario=10.0, total=10.0,
        ))
        s.commit()
        v = s.query(Venta).one()
        assert v.cliente_ref.nombre == "Ana"
