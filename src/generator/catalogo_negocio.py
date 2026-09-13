"""Catálogos del "negocio": clientes, proveedores y conceptos de gasto.

Igual que `catalog.py` (productos), esto es **fijo y reproducible**: los
clientes se generan con Faker pero con una semilla fija, así que siempre son
los mismos 30. Si cambiaran en cada ejecución no podríamos escribir un SQL de
referencia estable para el evaluador.
"""

from __future__ import annotations

from datetime import date, datetime, time

from faker import Faker

from src.generator.catalog import categorias

# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------

NUM_CLIENTES = 30
SEMILLA_CLIENTES = 2026

# Ciudades ponderadas: aparecen repetidas para que unas tengan más clientes
# que otras (así "¿qué ciudad tiene más clientes?" tiene una respuesta clara).
_CIUDADES_PONDERADAS = (
    ["Ciudad de México"] * 5
    + ["Guadalajara"] * 4
    + ["Monterrey"] * 3
    + ["Puebla"] * 2
    + ["Querétaro"] * 2
    + ["Mérida"] * 1
    + ["Tijuana"] * 1
    + ["León"] * 1
)


def clientes_fijos() -> list[dict]:
    """Los 30 clientes ficticios, siempre iguales (Faker con semilla fija)."""
    fake = Faker("es_MX")
    fake.seed_instance(SEMILLA_CLIENTES)

    clientes: list[dict] = []
    vistos: set[str] = set()
    while len(clientes) < NUM_CLIENTES:
        nombre = fake.name()
        if nombre in vistos:
            continue
        vistos.add(nombre)
        ciudad = fake.random_element(_CIUDADES_PONDERADAS)
        registro = fake.date_between_dates(date(2024, 1, 1), date(2026, 2, 1))
        clientes.append({
            "nombre": nombre,
            "ciudad": ciudad,
            "fecha_registro": datetime.combine(registro, time(9, 0)),
        })
    return clientes


# ---------------------------------------------------------------------------
# Proveedores — uno por categoría del catálogo
# ---------------------------------------------------------------------------

_NOMBRE_PROVEEDOR = {
    "tecnología": "TecnoMayorista del Norte",
    "ropa": "Textiles La Moda S.A.",
    "alimentos": "Abarrotes Distribuidora Central",
    "hogar": "Casa y Hogar Distribuciones",
}


def proveedores_fijos() -> list[dict]:
    salida = []
    for cat in categorias():
        nombre = _NOMBRE_PROVEEDOR.get(cat, f"Proveedor de {cat}")
        slug = nombre.lower().replace(" ", "").replace(".", "")[:20]
        salida.append({
            "nombre": nombre,
            "categoria_que_provee": cat,
            "contacto": f"ventas@{slug}.mx",
        })
    return salida


# ---------------------------------------------------------------------------
# Conceptos de gasto recurrentes (uno de cada uno por mes)
# ---------------------------------------------------------------------------
#
# monto_min == monto_max  -> gasto FIJO (la renta no cambia mes a mes)
# monto_min <  monto_max  -> gasto VARIABLE (se sortea en el rango)
# dia_del_mes             -> qué día se registra el gasto

GASTOS_RECURRENTES: list[dict] = [
    {
        "concepto": "Renta del local",
        "categoria_gasto": "renta",
        "monto_min": 18000.0,
        "monto_max": 18000.0,      # fija
        "dia_del_mes": 1,
    },
    {
        "concepto": "Nómina del personal",
        "categoria_gasto": "nómina",
        "monto_min": 42000.0,
        "monto_max": 49000.0,      # varía por horas extra / rotación
        "dia_del_mes": 15,
    },
    {
        "concepto": "Compra de mercancía a proveedores",
        "categoria_gasto": "mercancía",
        "monto_min": 55000.0,
        "monto_max": 120000.0,     # muy variable según reabastecimiento
        "dia_del_mes": 8,
    },
    {
        "concepto": "Servicios (luz, agua, internet)",
        "categoria_gasto": "servicios",
        "monto_min": 3200.0,
        "monto_max": 6800.0,       # varía con la temporada
        "dia_del_mes": 5,
    },
]


def categorias_gasto() -> list[str]:
    return [g["categoria_gasto"] for g in GASTOS_RECURRENTES]
