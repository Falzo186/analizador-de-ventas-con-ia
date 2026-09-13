"""Generador de datos falsos pero realistas para ventia.

Comandos:

  # Ventas de un rango de fechas (varios meses de una vez)
  python -m src.generator.generator backfill --desde 2026-03-01 --hasta 2026-08-31

  # Gastos mensuales (renta, nómina, mercancía, servicios) de un rango
  python -m src.generator.generator gastos --desde 2026-03-01 --hasta 2026-08-31

  # Ventas en vivo, cada X segundos durante N minutos
  python -m src.generator.generator realtime --minutos 10 --intervalo 5

  # Solo cargar catálogos / ver resumen
  python -m src.generator.generator seed
  python -m src.generator.generator resumen

Qué hace que los datos parezcan reales (y no ruido uniforme):

* Las categorías no se venden por igual: alimentos mucho, tecnología poco.
* Horario de tienda 9:00–21:00, con más movimiento a mediodía y por la tarde.
* Fines de semana ~40% más; y estacionalidad por mes (agosto y diciembre altos).
* El precio unitario varía respecto al de lista (ofertas, redondeo).
* ~60% de las ventas se asignan a un cliente registrado; el resto son de
  mostrador (cliente_id = NULL).
"""

from __future__ import annotations

import argparse
import random
import time
from datetime import date, datetime, timedelta

from faker import Faker
from sqlalchemy import func, select

from src.db.database import crear_base_de_datos, obtener_sesion
from src.db.schema import Cliente, Gasto, Producto, Proveedor, Venta
from src.generator.catalog import PESO_CATEGORIA, RANGO_CANTIDAD
from src.generator.catalogo_negocio import GASTOS_RECURRENTES
from src.generator.seed import sembrar_catalogos, sembrar_productos

fake = Faker("es_MX")

# Peso relativo de cada hora del día. Dos "jorobas": comida y salida del trabajo.
HORARIO = {
    9: 0.4, 10: 0.7, 11: 1.0, 12: 1.4, 13: 1.6, 14: 1.5,
    15: 1.0, 16: 0.9, 17: 1.1, 18: 1.5, 19: 1.7, 20: 1.2, 21: 0.6,
}

VENTAS_BASE_POR_DIA = 40          # promedio en un día entre semana
MULTIPLICADOR_FINDE = 1.4        # +40% sábado y domingo

# Estacionalidad: factor de volumen por mes (retail mexicano aproximado).
# Enero flojo ("cuesta de enero"), agosto regreso a clases, nov "Buen Fin",
# diciembre Navidad.
MULTIPLICADOR_MES = {
    1: 0.85, 2: 0.90, 3: 0.95, 4: 1.00, 5: 1.05, 6: 1.00,
    7: 1.05, 8: 1.15, 9: 1.00, 10: 1.05, 11: 1.25, 12: 1.40,
}

# Probabilidad de que una venta sea a un cliente registrado (vs. de mostrador).
PROB_CLIENTE = 0.60


def _rng(semilla: int | None) -> random.Random:
    """Generador de aleatorios propio, opcionalmente con semilla fija.

    Usar una semilla hace que dos ejecuciones produzcan exactamente los
    mismos datos: útil para pruebas y para poder comparar resultados.
    """
    return random.Random(semilla)


def _elegir_producto(sesion, rng: random.Random) -> Producto:
    """Elige un producto al azar, ponderado por la popularidad de su categoría."""
    productos = list(sesion.scalars(select(Producto)))
    pesos = [PESO_CATEGORIA.get(p.categoria, 1.0) for p in productos]
    return rng.choices(productos, weights=pesos, k=1)[0]


def _cantidad(categoria: str, rng: random.Random) -> int:
    """Cantidad vendida. Sesgada hacia números bajos."""
    minimo, maximo = RANGO_CANTIDAD.get(categoria, (1, 3))
    # triangular: el valor más probable es el mínimo
    return int(round(rng.triangular(minimo, maximo, minimo)))


def _precio_unitario(precio_base: float, rng: random.Random) -> float:
    """Precio real cobrado. A veces con descuento, siempre con ajuste fino."""
    if rng.random() < 0.15:                       # 15% de las ventas en oferta
        factor = rng.choice([0.9, 0.85, 0.8, 0.75])
    else:
        factor = rng.uniform(0.98, 1.02)          # ruido de redondeo
    return round(precio_base * factor, 2)


def _hora_de_venta(dia: date, rng: random.Random) -> datetime:
    """Devuelve un datetime dentro del horario de tienda para ese día."""
    horas = list(HORARIO.keys())
    pesos = list(HORARIO.values())
    hora = rng.choices(horas, weights=pesos, k=1)[0]
    minuto = rng.randint(0, 59)
    segundo = rng.randint(0, 59)
    return datetime(dia.year, dia.month, dia.day, hora, minuto, segundo)


def _ids_de_clientes(sesion) -> list[int]:
    return list(sesion.scalars(select(Cliente.id)))


def crear_venta(
    sesion,
    fecha: datetime,
    rng: random.Random,
    cliente_ids: list[int] | None = None,
) -> Venta:
    """Construye UNA venta y la agrega a la sesión (sin hacer commit).

    `cliente_ids`: lista de ids de clientes entre los que elegir. Si es None
    se consulta a la base (cómodo para llamadas sueltas). Pásala ya cargada
    en bucles grandes para no consultar en cada iteración.
    """
    if cliente_ids is None:
        cliente_ids = _ids_de_clientes(sesion)

    producto = _elegir_producto(sesion, rng)
    cantidad = _cantidad(producto.categoria, rng)
    precio_unitario = _precio_unitario(producto.precio_base, rng)
    total = round(precio_unitario * cantidad, 2)

    cliente_id = None
    if cliente_ids and rng.random() < PROB_CLIENTE:
        cliente_id = rng.choice(cliente_ids)

    venta = Venta(
        fecha=fecha,
        producto_id=producto.id,
        cliente_id=cliente_id,
        producto=producto.nombre,
        categoria=producto.categoria,
        cantidad=cantidad,
        precio_unitario=precio_unitario,
        total=total,
    )
    sesion.add(venta)
    return venta


def generar_una_venta(rng: random.Random | None = None) -> dict:
    """Crea UNA venta con la hora actual, la guarda, y devuelve sus datos.

    Pensada para el botón "Generar venta ahora" de la UI. Devuelve un dict
    (no el objeto Venta) para que se pueda usar tras cerrar la sesión.
    Reutiliza `crear_venta`, la misma lógica que el backfill y el tiempo real.
    """
    sembrar_catalogos()
    rng = rng or _rng(None)
    with obtener_sesion() as sesion:
        venta = crear_venta(sesion, datetime.now(), rng)
        sesion.commit()
        cliente = (
            sesion.get(Cliente, venta.cliente_id) if venta.cliente_id else None
        )
        return {
            "id": venta.id,
            "fecha": venta.fecha,
            "producto": venta.producto,
            "categoria": venta.categoria,
            "cantidad": venta.cantidad,
            "precio_unitario": venta.precio_unitario,
            "total": venta.total,
            "cliente": cliente.nombre if cliente else "Mostrador",
        }


def _ventas_para_el_dia(dia: date, rng: random.Random) -> int:
    """Cuántas ventas generar en un día concreto (finde + estacionalidad)."""
    factor = MULTIPLICADOR_FINDE if dia.weekday() >= 5 else 1.0
    factor *= MULTIPLICADOR_MES.get(dia.month, 1.0)
    # variación diaria de +-25%
    return max(1, int(round(VENTAS_BASE_POR_DIA * factor * rng.uniform(0.75, 1.25))))


def backfill(
    desde: date,
    hasta: date,
    semilla: int | None = None,
    verbose: bool = True,
) -> int:
    """Genera ventas para todos los días entre `desde` y `hasta` (inclusive).

    Funciona para un día, un mes o varios meses: solo recorre el rango día a
    día aplicando finde + estacionalidad de mes.
    """
    if hasta < desde:
        raise ValueError("La fecha 'hasta' no puede ser anterior a 'desde'.")

    sembrar_catalogos()
    rng = _rng(semilla)
    total_insertadas = 0

    with obtener_sesion() as sesion:
        cliente_ids = _ids_de_clientes(sesion)
        dia = desde
        mes_actual = None
        subtotal_mes = 0
        while dia <= hasta:
            if verbose and (dia.year, dia.month) != mes_actual:
                if mes_actual is not None:
                    print(f"    -> {subtotal_mes} ventas en {mes_actual[0]}-{mes_actual[1]:02d}")
                mes_actual = (dia.year, dia.month)
                subtotal_mes = 0

            n = _ventas_para_el_dia(dia, rng)
            for _ in range(n):
                crear_venta(sesion, _hora_de_venta(dia, rng), rng, cliente_ids)
            total_insertadas += n
            subtotal_mes += n
            dia += timedelta(days=1)

        if verbose and mes_actual is not None:
            print(f"    -> {subtotal_mes} ventas en {mes_actual[0]}-{mes_actual[1]:02d}")
        sesion.commit()

    if verbose:
        print(f"\nBackfill completo: {total_insertadas} ventas del {desde} al {hasta}.")
    return total_insertadas


def _meses_entre(desde: date, hasta: date):
    """Genera (año, mes) para cada mes tocado por el rango [desde, hasta]."""
    anio, mes = desde.year, desde.month
    while (anio, mes) <= (hasta.year, hasta.month):
        yield anio, mes
        mes += 1
        if mes == 13:
            mes, anio = 1, anio + 1


def generar_gastos(
    desde: date,
    hasta: date,
    semilla: int | None = None,
    verbose: bool = True,
) -> int:
    """Genera un gasto de cada concepto recurrente por cada mes del rango.

    - Renta: monto fijo.
    - Nómina, mercancía, servicios: monto sorteado dentro de su rango típico.
    Idempotente por (mes, concepto): si ya existe ese gasto ese mes, no lo
    duplica. Devuelve cuántos gastos insertó.
    """
    if hasta < desde:
        raise ValueError("La fecha 'hasta' no puede ser anterior a 'desde'.")

    crear_base_de_datos()
    rng = _rng(semilla)
    insertados = 0

    with obtener_sesion() as sesion:
        for anio, mes in _meses_entre(desde, hasta):
            for g in GASTOS_RECURRENTES:
                if g["monto_min"] == g["monto_max"]:
                    monto = g["monto_min"]
                else:
                    monto = round(rng.uniform(g["monto_min"], g["monto_max"]), 2)

                fecha = datetime(anio, mes, g["dia_del_mes"], 10, 0)
                ya_existe = sesion.scalar(
                    select(Gasto).where(
                        Gasto.concepto == g["concepto"],
                        func.strftime("%Y-%m", Gasto.fecha) == f"{anio}-{mes:02d}",
                    )
                )
                if ya_existe is None:
                    sesion.add(Gasto(
                        fecha=fecha,
                        concepto=g["concepto"],
                        categoria_gasto=g["categoria_gasto"],
                        monto=float(monto),
                    ))
                    insertados += 1
            if verbose:
                print(f"  {anio}-{mes:02d}: gastos del mes generados")
        sesion.commit()

    if verbose:
        print(f"\nGastos: {insertados} registros nuevos del {desde} al {hasta}.")
    return insertados


def tiempo_real(
    minutos: float,
    intervalo_seg: float = 5.0,
    semilla: int | None = None,
) -> int:
    """Inserta una venta cada `intervalo_seg` segundos durante `minutos`.

    Cada venta se marca con la hora actual del reloj (datetime.now()), así que
    la UI puede mostrar 'ventas en vivo'. Se hace commit tras cada inserción
    para que otros procesos (la UI) vean los datos de inmediato.
    """
    sembrar_catalogos()
    rng = _rng(semilla)
    fin = time.monotonic() + minutos * 60
    n = 0

    print(f"Modo tiempo real: {minutos} min, una venta cada "
          f"{intervalo_seg}s. Ctrl+C para detener.")
    try:
        with obtener_sesion() as sesion:
            cliente_ids = _ids_de_clientes(sesion)
            while time.monotonic() < fin:
                venta = crear_venta(sesion, datetime.now(), rng, cliente_ids)
                sesion.commit()
                n += 1
                print(f"  [{n:>4}] {venta}")
                time.sleep(intervalo_seg)
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")

    print(f"Tiempo real terminado: {n} ventas insertadas.")
    return n


def _resumen_db() -> None:
    """Imprime un pequeño resumen de lo que hay en la base de datos."""
    with obtener_sesion() as sesion:
        n_prod = sesion.scalar(select(func.count(Producto.id)))
        n_cli = sesion.scalar(select(func.count(Cliente.id)))
        n_prov = sesion.scalar(select(func.count(Proveedor.id)))
        n_ventas = sesion.scalar(select(func.count(Venta.id)))
        n_mostrador = sesion.scalar(
            select(func.count(Venta.id)).where(Venta.cliente_id.is_(None))
        )
        n_gastos = sesion.scalar(select(func.count(Gasto.id)))
        rango = sesion.execute(
            select(func.min(Venta.fecha), func.max(Venta.fecha))
        ).first()
    print(f"Productos: {n_prod} | Clientes: {n_cli} | Proveedores: {n_prov}")
    print(f"Ventas: {n_ventas} (de mostrador: {n_mostrador}) | Gastos: {n_gastos}")
    print(f"Rango de ventas: {rango[0]} .. {rango[1]}")


def _parsear_fecha(texto: str) -> date:
    return datetime.strptime(texto, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.generator.generator",
        description="Genera datos falsos para la base de datos de ventia.",
    )
    sub = parser.add_subparsers(dest="modo", required=True)

    p_back = sub.add_parser("backfill", help="Ventas de un rango de fechas")
    p_back.add_argument("--desde", required=True, type=_parsear_fecha)
    p_back.add_argument("--hasta", required=True, type=_parsear_fecha)
    p_back.add_argument("--semilla", type=int, default=None)

    p_gastos = sub.add_parser("gastos", help="Gastos mensuales de un rango")
    p_gastos.add_argument("--desde", required=True, type=_parsear_fecha)
    p_gastos.add_argument("--hasta", required=True, type=_parsear_fecha)
    p_gastos.add_argument("--semilla", type=int, default=None)

    p_rt = sub.add_parser("realtime", help="Insertar ventas en vivo")
    p_rt.add_argument("--minutos", required=True, type=float)
    p_rt.add_argument("--intervalo", type=float, default=5.0)
    p_rt.add_argument("--semilla", type=int, default=None)

    sub.add_parser("seed", help="Cargar catálogos (productos, clientes, proveedores)")
    sub.add_parser("resumen", help="Mostrar qué hay en la base de datos")

    args = parser.parse_args(argv)
    crear_base_de_datos()

    if args.modo == "backfill":
        backfill(args.desde, args.hasta, semilla=args.semilla)
    elif args.modo == "gastos":
        generar_gastos(args.desde, args.hasta, semilla=args.semilla)
    elif args.modo == "realtime":
        tiempo_real(args.minutos, args.intervalo, semilla=args.semilla)
    elif args.modo == "seed":
        for tabla, n in sembrar_catalogos().items():
            print(f"{tabla}: {n} registros nuevos")
    elif args.modo == "resumen":
        _resumen_db()


if __name__ == "__main__":
    main()
