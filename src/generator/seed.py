"""Carga los catálogos fijos: productos, clientes y proveedores.

Todo es *idempotente*: puedes ejecutarlo mil veces y siempre quedan
exactamente los mismos registros, sin duplicados. Así el resto del proyecto
puede asumir que los catálogos ya existen.
"""

from __future__ import annotations

from sqlalchemy import select

from src.db.database import crear_base_de_datos, obtener_sesion
from src.db.schema import Cliente, Producto, Proveedor
from src.generator.catalog import CATALOGO
from src.generator.catalogo_negocio import clientes_fijos, proveedores_fijos


def sembrar_productos() -> int:
    """Inserta los productos que falten. Devuelve cuántos insertó."""
    crear_base_de_datos()
    insertados = 0
    with obtener_sesion() as sesion:
        for categoria, productos in CATALOGO.items():
            for nombre, precio in productos.items():
                existe = sesion.scalar(
                    select(Producto).where(
                        Producto.nombre == nombre,
                        Producto.categoria == categoria,
                    )
                )
                if existe is None:
                    sesion.add(
                        Producto(
                            nombre=nombre,
                            categoria=categoria,
                            precio_base=float(precio),
                        )
                    )
                    insertados += 1
        sesion.commit()
    return insertados


def sembrar_clientes() -> int:
    """Inserta los ~30 clientes fijos que falten. Devuelve cuántos insertó."""
    crear_base_de_datos()
    insertados = 0
    with obtener_sesion() as sesion:
        for c in clientes_fijos():
            existe = sesion.scalar(
                select(Cliente).where(
                    Cliente.nombre == c["nombre"],
                    Cliente.ciudad == c["ciudad"],
                )
            )
            if existe is None:
                sesion.add(Cliente(**c))
                insertados += 1
        sesion.commit()
    return insertados


def sembrar_proveedores() -> int:
    """Inserta el proveedor de cada categoría que falte."""
    crear_base_de_datos()
    insertados = 0
    with obtener_sesion() as sesion:
        for p in proveedores_fijos():
            existe = sesion.scalar(
                select(Proveedor).where(Proveedor.nombre == p["nombre"])
            )
            if existe is None:
                sesion.add(Proveedor(**p))
                insertados += 1
        sesion.commit()
    return insertados


def sembrar_catalogos() -> dict[str, int]:
    """Siembra todo (productos + clientes + proveedores). Idempotente."""
    return {
        "productos": sembrar_productos(),
        "clientes": sembrar_clientes(),
        "proveedores": sembrar_proveedores(),
    }


if __name__ == "__main__":
    for tabla, n in sembrar_catalogos().items():
        print(f"{tabla}: {n} registros nuevos")
