"""Esquema de la base de datos.

Tablas:
  productos     — catálogo de lo que se vende
  clientes      — quién compra (opcional en cada venta: compras "de mostrador")
  proveedores   — quién surte cada categoría
  ventas        — el hecho central: una fila por venta
  gastos        — costos del negocio (renta, nómina, mercancía, servicios)

Usamos el ORM de SQLAlchemy: cada clase de Python representa una tabla y cada
atributo una columna. SQLAlchemy traduce esto a sentencias `CREATE TABLE`.

Decisión de diseño importante — *desnormalización parcial*:
La tabla `ventas` guarda `producto_id` (la forma "correcta" de relacionar
tablas) PERO también copia `producto`, `categoria` y `precio_unitario` dentro
de cada fila de venta. Normalmente eso se considera mala práctica porque
duplica datos. Aquí lo hacemos a propósito por dos razones:

1. El objetivo del proyecto es que un modelo de 8B parámetros escriba SQL.
   Un modelo pequeño acierta mucho más con `SELECT SUM(cantidad) FROM ventas
   WHERE categoria = 'tecnología'` que con una consulta que requiere un JOIN.
2. Una venta es un *hecho histórico*: si mañana sube el precio de un producto,
   las ventas de ayer deben conservar el precio que tenían en ese momento.
   Copiar el precio en la fila de venta es justamente lo que hacen los
   sistemas de punto de venta reales.

`ventas.cliente_id` es NULLABLE a propósito: la mayoría de las ventas de una
tienda son "de mostrador" (cliente anónimo). Un NULL ahí significa eso, y las
ventas viejas generadas antes de tener clientes siguen siendo válidas.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Clase base de la que heredan todos los modelos."""


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    ciudad: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    fecha_registro: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    ventas: Mapped[list["Venta"]] = relationship(back_populates="cliente_ref")

    __table_args__ = (
        UniqueConstraint("nombre", "ciudad", name="uq_cliente_nombre_ciudad"),
    )

    def __repr__(self) -> str:
        return f"<Cliente {self.nombre} ({self.ciudad})>"


class Proveedor(Base):
    __tablename__ = "proveedores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    # Cada proveedor surte una categoría del catálogo ('ropa', 'hogar', ...).
    categoria_que_provee: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    contacto: Mapped[str] = mapped_column(String(120), nullable=False)

    def __repr__(self) -> str:
        return f"<Proveedor {self.nombre} -> {self.categoria_que_provee}>"


class Gasto(Base):
    __tablename__ = "gastos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    concepto: Mapped[str] = mapped_column(String(120), nullable=False)
    # 'renta', 'nómina', 'mercancía', 'servicios'
    categoria_gasto: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    monto: Mapped[float] = mapped_column(Float, nullable=False)

    def __repr__(self) -> str:
        return f"<Gasto {self.fecha:%Y-%m} {self.concepto} ${self.monto:.2f}>"


class Producto(Base):
    __tablename__ = "productos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    categoria: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Precio "de lista". El precio real de cada venta puede variar un poco
    # (ofertas, redondeos) y se guarda en la fila de venta.
    precio_base: Mapped[float] = mapped_column(Float, nullable=False)

    ventas: Mapped[list["Venta"]] = relationship(back_populates="producto_ref")

    # No queremos dos productos con el mismo nombre en la misma categoría.
    __table_args__ = (
        UniqueConstraint("nombre", "categoria", name="uq_producto_nombre_categoria"),
    )

    def __repr__(self) -> str:  # solo para depurar cómodamente
        return f"<Producto {self.nombre} ({self.categoria}) ${self.precio_base}>"


class Venta(Base):
    __tablename__ = "ventas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Momento exacto de la venta. index=True porque casi todas las preguntas
    # ("este mes", "hoy", "en julio") filtran por fecha.
    fecha: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    # Relación "correcta" con productos (integridad referencial).
    producto_id: Mapped[int] = mapped_column(
        ForeignKey("productos.id"), nullable=False, index=True
    )

    # Cliente de la venta. NULL = venta de mostrador (cliente anónimo).
    cliente_id: Mapped[int | None] = mapped_column(
        ForeignKey("clientes.id"), nullable=True, index=True
    )

    # Campos copiados de Producto para que las consultas sean planas.
    producto: Mapped[str] = mapped_column(String(120), nullable=False)
    categoria: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    precio_unitario: Mapped[float] = mapped_column(Float, nullable=False)
    total: Mapped[float] = mapped_column(Float, nullable=False)

    # Nombre del atributo de relación distinto al de la columna 'producto'
    # (que es texto). Este da acceso al objeto Producto completo.
    producto_ref: Mapped["Producto"] = relationship(
        "Producto", back_populates="ventas"
    )
    cliente_ref: Mapped["Cliente | None"] = relationship(
        "Cliente", back_populates="ventas"
    )

    def __repr__(self) -> str:
        return (
            f"<Venta {self.fecha:%Y-%m-%d %H:%M} {self.producto} "
            f"x{self.cantidad} = ${self.total:.2f}>"
        )
