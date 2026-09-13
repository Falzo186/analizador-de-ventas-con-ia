"""Conexión a la base de datos y utilidades de sesión.

Aquí centralizamos *cómo* nos conectamos a SQLite para que el resto del
proyecto (generador, evaluador, UI) no tenga que repetir esa lógica.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

# Raíz del proyecto = dos carpetas hacia arriba desde este archivo
# (src/db/database.py -> src/db -> src -> raíz).
RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

# La base de datos por defecto es data/northwind.db (datos reales de
# Northwind Traders). Usamos una ruta absoluta para que funcione sin importar
# desde qué carpeta ejecutes los scripts.
# La variable de entorno VENTIA_DB_PATH permite apuntar a otra base (p. ej.
# una temporal en las pruebas, o de vuelta a data/ventia.db si se necesita el
# simulador sintético) sin tocar el código.
RUTA_DB = Path(os.environ.get("VENTIA_DB_PATH", RAIZ_PROYECTO / "data" / "northwind.db"))

# El "engine" es el objeto que sabe hablar con SQLite. future=True activa el
# comportamiento moderno (estilo SQLAlchemy 2.0).
engine = create_engine(f"sqlite:///{RUTA_DB}", future=True)

# Fábrica de sesiones. Una "sesión" es una conversación con la base de datos:
# la abres, haces cambios, haces commit y la cierras.
SesionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


def crear_base_de_datos() -> None:
    """Crea el archivo SQLite y todas las tablas del simulador si aún no existen.

    Northwind (`data/northwind.db`, la base real) ya trae su propio esquema:
    aquí NO hay nada que crear, y sobre todo no queremos que `create_all`
    inyecte las tablas sintéticas (`productos`, `ventas`, ...) dentro del
    archivo de datos reales. Si alguien apunta `VENTIA_DB_PATH` de vuelta al
    simulador (`ventia.db` o cualquier ruta que no sea `northwind.db`), el
    flujo de creación/migración sigue funcionando como antes.
    """
    if RUTA_DB.name == "northwind.db":
        if not RUTA_DB.exists():
            raise FileNotFoundError(
                f"No se encontró la base de datos de Northwind en {RUTA_DB}. "
                "Descárgala y colócala ahí, o apunta VENTIA_DB_PATH a otra base."
            )
        return

    from src.db.schema import Base  # import local para evitar import circular

    RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _migraciones_ligeras()


def _migraciones_ligeras() -> None:
    """Cambios de esquema ADITIVOS sobre una base que ya existía.

    `create_all` crea tablas nuevas pero nunca modifica una tabla existente.
    Cuando añadimos `ventas.cliente_id` a un proyecto que ya tenía ventas,
    hay que hacer el ALTER a mano. Solo columnas nuevas y NULLABLE: así las
    ventas viejas quedan con `cliente_id = NULL` (venta de mostrador) y nada
    se rompe. Para cambios más complejos, lo correcto es borrar `ventas.db`.
    """
    inspector = inspect(engine)
    if "ventas" not in inspector.get_table_names():
        return
    columnas = {c["name"] for c in inspector.get_columns("ventas")}
    if "cliente_id" not in columnas:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE ventas ADD COLUMN cliente_id INTEGER "
                "REFERENCES clientes(id)"
            ))


def obtener_sesion():
    """Devuelve una sesión nueva. Recuerda cerrarla (o usar 'with')."""
    return SesionLocal()
