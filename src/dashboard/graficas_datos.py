"""Selección de datos para las gráficas de cada necesidad (Fase 4).

Cada función recibe un `ResultadoNecesidad` ya cargado (vía `src.dashboard.
servicio`, nunca calculado en vivo aquí) y devuelve `pandas.DataFrame`(s)
listos para graficar, usando SOLO las tablas/valores_clave/parametros que
ya trae el JSON -- ninguna calcula ni inventa datos nuevos.

Este módulo NO importa `altair`, `matplotlib` ni `streamlit`: es la capa de
datos compartida entre `src/ui/dashboard.py` (gráficas Altair, Fase 3) y
`src/pdf/generador.py` (gráficas matplotlib, Fase 4), para que ambos dibujen
exactamente lo mismo sin duplicar la lógica de selección de columnas/filas.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.analytics.contrato import ResultadoNecesidad

FACTORES_N9: dict[str, str] = {
    "factor_hora": "Hora del día",
    "factor_dia": "Día de la semana",
    "factor_intervalo": "Intervalo desde el pedido previo",
    "factor_numero_pedido": "Número de pedido (cubos)",
}


def datos_n1(r: ResultadoNecesidad) -> pd.DataFrame:
    filas = [dict(f, clasificacion="Asegurar inventario") for f in r.tablas["asegurar_inventario"]]
    filas += [dict(f, clasificacion="Revisar/retirar") for f in r.tablas["revisar_retirar"]]
    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.drop_duplicates(subset="product_id")
    return df


def datos_n2(r: ResultadoNecesidad, top: int = 20) -> pd.DataFrame:
    df = pd.DataFrame(r.tablas["top_50_lift"]).head(top).copy()
    if not df.empty:
        df["par"] = df["producto_a_nombre"] + " + " + df["producto_b_nombre"]
    return df


def datos_n3(r: ResultadoNecesidad) -> pd.DataFrame:
    return pd.DataFrame(r.tablas["matriz_dow_hora"])


@dataclass
class DatosN4:
    df: pd.DataFrame
    umbral_razon: float


def datos_n4(r: ResultadoNecesidad) -> DatosN4:
    df = pd.DataFrame(r.tablas["top_50_razon"])
    umbral = next(p.valor for p in r.parametros if p.nombre == "UMBRAL_RAZON")
    return DatosN4(df=df, umbral_razon=umbral)


def datos_n5(r: ResultadoNecesidad, top: int = 20) -> pd.DataFrame:
    df = pd.DataFrame(r.tablas["top_50_indice_ancla"])
    if not df.empty:
        df = df.sort_values("indice_ancla", ascending=False).head(top)
    return df


def datos_n6(r: ResultadoNecesidad) -> pd.DataFrame:
    return pd.DataFrame(r.tablas["departamentos"])


@dataclass
class DatosN7:
    df_k: pd.DataFrame
    df_pareado: pd.DataFrame
    n_usuarios_pareado: int


def datos_n7(r: ResultadoNecesidad) -> DatosN7:
    df_k = pd.DataFrame(r.tablas["por_numero_pedido"])
    filas_pareado = r.tablas["pareado_1_vs_10"]
    # n_usuarios=0 (nadie tiene pedido 1 Y pedido 10) deja las 8 diferencias
    # en NULL (AVG/STDDEV sobre un conjunto vacío) -- se trata igual que
    # "sin filas", no como una fila con valores None que rompería una
    # comparación numérica al graficar.
    if filas_pareado and filas_pareado[0]["n_usuarios"] > 0:
        fila = filas_pareado[0]
        n_usuarios = fila["n_usuarios"]
        df_pareado = pd.DataFrame([
            {"métrica": "Tamaño", "diferencia_media": fila["diff_tamano_media"]},
            {"métrica": "Tasa de recompra", "diferencia_media": fila["diff_tasa_recompra_media"]},
            {"métrica": "Productos nuevos por pedido", "diferencia_media": fila["diff_productos_nuevos_media"]},
            {"métrica": "Departamentos distintos", "diferencia_media": fila["diff_departamentos_distintos_media"]},
        ])
    else:
        df_pareado = pd.DataFrame(columns=["métrica", "diferencia_media"])
        n_usuarios = 0
    return DatosN7(df_k=df_k, df_pareado=df_pareado, n_usuarios_pareado=n_usuarios)


@dataclass
class DatosN8:
    df: pd.DataFrame
    corte_rutinario: float | None
    corte_explorador: float | None


def datos_n8(r: ResultadoNecesidad) -> DatosN8:
    df = pd.DataFrame(r.tablas["histograma_indice_novedad"])
    corte_bajo = next((v.valor for v in r.valores_clave if v.etiqueta.startswith("Corte rutinario")), None)
    corte_alto = next((v.valor for v in r.valores_clave if v.etiqueta.startswith("Corte explorador")), None)
    return DatosN8(df=df, corte_rutinario=corte_bajo, corte_explorador=corte_alto)


def datos_n9_histograma(r: ResultadoNecesidad) -> pd.DataFrame:
    return pd.DataFrame(r.tablas["histograma_tamano"])


def datos_n9_factor(r: ResultadoNecesidad, factor_key: str) -> pd.DataFrame:
    return pd.DataFrame(r.tablas[factor_key])


def datos_n10(r: ResultadoNecesidad) -> pd.DataFrame:
    return pd.DataFrame(r.tablas["tasa_recompra_por_tramo"])


def datos_n11(r: ResultadoNecesidad) -> pd.DataFrame:
    return pd.DataFrame(r.tablas["metricas"])
