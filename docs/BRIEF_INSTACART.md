=== BRIEF ===
OBJETIVO
- Reemplazar Northwind por Instacart (Online Grocery Shopping Dataset 2017).
- Web app de dos paneles: izquierda, chat con IA local estilo ChatGPT (conversación, historial, respuesta en streaming); derecha, dashboard.
- Dashboard: 11 necesidades (N1-N11), cada una con gráficas, valores clave y la decisión que ayuda a tomar. Botón para (re)generar un PDF.
- El PDF contiene respuestas fundamentadas SIN IA: todo sale de consultas SQL y código determinista; los textos de conclusión salen de plantillas con umbrales. Son las "respuestas de referencia" que se compararán después contra la IA.
- La IA local debe responder de forma coherente a las necesidades y a variantes de ellas. En esta etapa NO se compara automáticamente contra las respuestas de referencia. "Coherente" significa: responde lo preguntado, usa cifras reales devueltas por la BD y no inventa tablas ni columnas.

DATASET (hechos y limitaciones)
- 6 tablas: aisles, departments, products, orders, order_products__prior, order_products__train.
- orders.eval_set puede ser prior, train o test. Los pedidos "test" no tienen productos en ningún archivo: se excluyen de todo análisis de productos y se documenta.
- Los productos de un pedido salen de la unión de prior + train.
- days_since_prior_order es NULL en el primer pedido de cada usuario y está topado en 30.
- order_dow (0-6) y order_hour_of_day (0-23) son anónimos. No asumir a qué día real corresponde el 0; etiquetar "Día 0..6".
- No hay precios, fechas reales ni datos demográficos: prohibido afirmar ingresos o dinero.
- Fuente: "The Instacart Online Grocery Shopping Dataset 2017", https://www.instacart.com/datasets/grocery-shopping-2017. Copia usada: Kaggle, usuario yasserh. Licencia original: uso no comercial.
- Debe existir una ficha del dataset (fuente, licencia, limitaciones, filas y columnas por tabla, calculadas con consultas reales, no copiadas) visible en README, dashboard y PDF.

NECESIDADES (cada una con definición formal de métrica y umbrales, documentada en docs/NECESIDADES.md)
N1 Recompra por producto: tasa = líneas con reordered=1 / líneas totales, con volumen mínimo documentado. Separar productos de alta recompra de los que se prueban y abandonan. Decisión: qué asegurar en inventario y qué revisar o retirar.
N2 Canasta: pares de productos en el mismo pedido, con soporte, confianza y lift. Decisión: paquetes, promociones cruzadas, "te puede interesar".
N3 Picos de demanda: pedidos por día x hora (mapa de calor). Decisión: personal, repartidores y promociones.
N4 Frecuencia de recompra por cliente: intervalo habitual propio vs. su último intervalo observado (no existe "fecha actual"; documentar esta limitación). Marcar clientes en riesgo con regla documentada. Decisión: a quién enviar recordatorio.
N5 Productos ancla: qué productos aparecen con más frecuencia como primer artículo (add_to_cart_order=1) y su tasa de "primero" sobre sus apariciones. Decisión: qué destacar en portada.
N6 Departamentos y pasillos: volumen vs. recompra, en cuadrantes (fieles/esporádicos). Decisión: dónde invertir espacio y publicidad.
N7 Evolución por número de pedido: tamaño, variedad y recompra del pedido 1 vs. el 10, y curva 1..N con mínimo de usuarios. Decisión: qué ofrecer a clientes nuevos.
N8 Rutinarios vs. exploradores: variedad por usuario, con umbrales documentados. Decisión: suscripción/"repetir pedido" vs. novedades.
N9 Tamaño de pedido: distribución y su relación con hora, día y frecuencia del cliente. Decisión: umbral de envío gratis o incentivo.
N10 Final del carrito/impulso: posición (normalizada) en el carrito vs. recompra. Decisión: qué poner cerca de caja o como sugerencia de último momento.
N11 Predicción del siguiente pedido: baseline determinista (frecuencia + recencia de recompra por usuario), sin ML. Top-K por usuario, evaluado con hit-rate contra los pedidos "train".

RENDIMIENTO
- order_products tiene más de 33 millones de filas. Cada panel debe cargar en un tiempo razonable (objetivo: menos de 5 s después de la primera carga). Se permite precalcular tablas agregadas. Prohibido submuestrear sin documentarlo y sin mi aprobación.

SEGURIDAD DE LA IA
- El SQL generado por la IA: solo SELECT, una sola sentencia, con timeout y LIMIT, sobre conexión de solo lectura.

HONESTIDAD
- Cero cifras hardcodeadas. Cada número del dashboard y del PDF sale de una consulta, y la consulta usada se guarda y se muestra.

FUERA DE ALCANCE AHORA
- Comparación automática IA vs. referencia, pruebas estadísticas, fine-tuning.
=== FIN DEL BRIEF ===
