"""Catálogo de productos que vende nuestra tienda ficticia.

Es una lista fija (no aleatoria) por una razón: queremos que las preguntas al
modelo tengan respuestas estables y verificables. Si los productos cambiaran
en cada ejecución, no podríamos comparar "lo que dijo el LLM" contra "la
verdad" de forma reproducible.

Cada categoría tiene entre 12 y 14 productos. Los precios están en la moneda
local (pesos) y son valores de lista aproximados; el generador les aplica
pequeñas variaciones al registrar cada venta.

`PESO_CATEGORIA` indica qué tan seguido se vende cada categoría. Alimentos se
vende muchas veces al día; tecnología, pocas veces pero con ticket más alto.
Esto hace que los datos se parezcan a los de una tienda real.
"""

from __future__ import annotations

# nombre -> precio base
CATALOGO: dict[str, dict[str, float]] = {
    "tecnología": {
        "Audífonos Bluetooth": 799,
        "Mouse inalámbrico": 349,
        "Teclado mecánico": 1299,
        "Cargador USB-C 30W": 279,
        "Memoria USB 64GB": 189,
        "Disco SSD 1TB": 1899,
        "Webcam 1080p": 649,
        "Hub USB 4 puertos": 399,
        "Bocina portátil": 899,
        "Power bank 20000mAh": 749,
        "Cable HDMI 2m": 159,
        "Adaptador de corriente": 229,
        "Soporte para laptop": 459,
        "Micrófono de escritorio": 1099,
    },
    "ropa": {
        "Playera de algodón": 199,
        "Camisa de vestir": 549,
        "Pantalón de mezclilla": 699,
        "Sudadera con capucha": 649,
        "Chamarra ligera": 999,
        "Calcetines (paquete 3)": 149,
        "Gorra ajustable": 249,
        "Cinturón de piel": 399,
        "Bufanda de lana": 299,
        "Vestido casual": 749,
        "Shorts deportivos": 329,
        "Ropa interior (paquete 3)": 219,
        "Playera polo": 379,
    },
    "alimentos": {
        "Leche entera 1L": 28,
        "Pan de caja": 42,
        "Huevo (docena)": 46,
        "Arroz 1kg": 34,
        "Frijol 1kg": 38,
        "Aceite vegetal 1L": 39,
        "Café molido 250g": 89,
        "Azúcar 1kg": 32,
        "Pasta 500g": 18,
        "Atún en lata": 22,
        "Cereal 500g": 68,
        "Galletas surtidas": 25,
        "Jugo de naranja 1L": 33,
        "Yogurt natural 1kg": 45,
    },
    "hogar": {
        "Juego de sábanas matrimonial": 599,
        "Almohada estándar": 249,
        "Toalla de baño": 189,
        "Set de vasos (6 pzas)": 279,
        "Sartén antiadherente 24cm": 449,
        "Olla de acero 4L": 629,
        "Organizador plástico 20L": 179,
        "Cortina blackout": 399,
        "Lámpara de mesa": 349,
        "Tapete de entrada": 159,
        "Bote de basura 10L": 139,
        "Juego de cubiertos (16 pzas)": 499,
        "Cesto para ropa": 219,
    },
}

# Qué tan frecuente es una venta de cada categoría (pesos relativos).
PESO_CATEGORIA: dict[str, float] = {
    "tecnología": 1.0,
    "ropa": 2.0,
    "alimentos": 5.0,
    "hogar": 1.5,
}

# Cantidad típica por venta según la categoría. Compras de alimentos suelen
# llevar varias unidades; un SSD casi siempre es 1.
RANGO_CANTIDAD: dict[str, tuple[int, int]] = {
    "tecnología": (1, 2),
    "ropa": (1, 3),
    "alimentos": (1, 8),
    "hogar": (1, 3),
}


def categorias() -> list[str]:
    return list(CATALOGO.keys())


def total_productos() -> int:
    return sum(len(p) for p in CATALOGO.values())
