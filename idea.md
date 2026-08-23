Quiero que me ayudes a construir una aplicación en Python para generar 
cotizaciones profesionales de componentes electrónicos en Guatemala.

CONTEXTO Y OBJETIVO
Soy un integrador/vendedor de componentes electrónicos. Compro en tiendas 
locales guatemaltecas y revendo a clientes con un margen. Necesito una 
herramienta que automatice la creación de cotizaciones profesionales para 
enviar a mis clientes.

FUENTES DE DATOS (scraping de precios)
La app debe extraer precio, nombre y disponibilidad de un componente a 
partir de una URL de producto de estas tiendas:
- https://laelectronica.com.gt/
- https://www.electronicadiy.com/es
- https://electronicarych.com/

Cada tienda tiene HTML distinto, así que necesito un scraper específico 
por dominio (usar requests + BeautifulSoup, o Selenium/Playwright si 
alguna carga precios por JavaScript). Debe manejar:
- Detección automática de a qué tienda pertenece la URL.
- Extracción de: nombre del producto, precio, imagen (opcional), 
  disponibilidad/stock si existe.
- Manejo de errores (URL caída, producto sin stock, cambio de estructura 
  HTML, timeout) sin que la app se caiga.

FLUJO DE USO
1. Pego una URL de un componente.
2. La app extrae automáticamente nombre y precio.
3. Me pregunta la cantidad que necesito de ese componente.
4. Repito el proceso para agregar más componentes a la misma cotización.
5. Al terminar, la app calcula:
   - Subtotal por componente (precio unitario × cantidad).
   - Subtotal general.
   - Cargo por servicio de compra: 12% sobre el subtotal.
   - Total final.
6. Genera dos archivos de salida:
   - Un CSV con el detalle (para mi control interno).
   - Un HTML/PDF con diseño profesional (para enviar al cliente).

DATOS QUE DEBE GUARDAR POR COTIZACIÓN
- Número de cotización (correlativo automático).
- Fecha.
- Nombre del cliente (input opcional al generar).
- Por cada componente: nombre, URL de origen, tienda, precio unitario, 
  cantidad, subtotal.
- Subtotal general, cargo de servicio (12%), total.
- Vigencia de la cotización (ej. "válida por 5 días") porque los precios 
  pueden cambiar.

REQUISITOS DE DISEÑO (documento final)
El HTML/PDF debe verse como una cotización comercial real, no un reporte 
genérico:
- Encabezado con espacio para logo/nombre de mi negocio y datos de contacto.
- Tabla limpia con columnas: Componente | Tienda | Cant. | Precio unitario | Subtotal.
- Desglose visible de subtotal, cargo de servicio (12%) y total, con el 
  total destacado.
- Pie de página con condiciones (vigencia, moneda en quetzales GTQ, 
  método de pago si aplica).
- Tipografía y espaciado cuidados — algo que pueda enviar a un cliente 
  sin tener que "arreglarlo" en Word después.
- Idealmente usando una librería como WeasyPrint o una plantilla HTML+CSS 
  bien diseñada que luego se exporte a PDF.

FUNCIONES ADICIONALES QUE ME GUSTARÍA CONSIDERAR
- Historial de cotizaciones guardadas (para no perder cotizaciones viejas 
  y poder reabrirlas o reenviarlas).
- Opción de re-verificar precios de una cotización guardada (volver a 
  hacer scraping de las mismas URLs para ver si cambiaron).
- Configuración editable del porcentaje de margen (hoy es 12%, pero que 
  no esté "quemado" en el código por si cambia).
- Modo interfaz: decidir si será CLI (línea de comandos, más rápido de 
  construir) o una interfaz gráfica simple (Tkinter, o una app web local 
  con Flask/Streamlit) — ¿cuál me recomiendas según facilidad de uso?
- Manejo de moneda en quetzales (Q) con formato correcto (Q 1,250.00).
- Validación de que la URL pegada realmente corresponde a una de las 
  tres tiendas soportadas.

RESTRICCIONES TÉCNICAS
- Lenguaje: Python.
- Debo poder correrlo localmente en Linux (Arch/Omarchy).
- Prioriza librerías bien mantenidas y fáciles de instalar (pip).
- El scraping debe ser respetuoso (delays razonables, user-agent 
  identificado, sin sobrecargar los sitios).

LO QUE NECESITO DE TI AHORA
1. Antes de escribir código, propón la arquitectura del proyecto 
   (estructura de carpetas/archivos, librerías a usar, y por qué).
2. Pregúntame cualquier duda sobre el flujo antes de asumir algo 
   (ej. si quiero interfaz gráfica o CLI, si necesito multi-moneda, etc.).
3. Constrúyelo de forma incremental: primero el scraper y su 
   validación, luego la lógica de cotización, luego el generador de 
   documento profesional.
