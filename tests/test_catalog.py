"""Pruebas del catálogo: debe cumplir lo que promete el enunciado del proyecto."""

from __future__ import annotations

from src.generator.catalog import CATALOGO, PESO_CATEGORIA, RANGO_CANTIDAD


def test_hay_al_menos_cuatro_categorias():
    assert set(CATALOGO) >= {"tecnología", "ropa", "alimentos", "hogar"}


def test_cada_categoria_tiene_entre_10_y_15_productos():
    for categoria, productos in CATALOGO.items():
        assert 10 <= len(productos) <= 15, categoria


def test_todos_los_precios_son_positivos():
    for productos in CATALOGO.values():
        for nombre, precio in productos.items():
            assert precio > 0, nombre


def test_metadatos_cubren_todas_las_categorias():
    for categoria in CATALOGO:
        assert categoria in PESO_CATEGORIA
        assert categoria in RANGO_CANTIDAD
