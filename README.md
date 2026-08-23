# Cotizador de Componentes Electrónicos (Guatemala) ⚡

Herramienta en Python para integradores y vendedores de electrónica en Guatemala. Automatiza el scraping de precios de tiendas locales guatemaltecas, calcula cotizaciones con margen de compra configurable (por defecto 12%) y genera cotizaciones profesionales en formato **PDF**, **HTML** y **CSV**.

## Tiendas Locales Soportadas

1. **La Electrónica** (`https://laelectronica.com.gt/`)
2. **Electrónica DIY** (`https://www.electronicadiy.com/es`)
3. **Electrónica RyCH** (`https://electronicarych.com/`)

---

## Características Principales

- **Detección Automática de Tienda**: Solo pega la URL del componente y el sistema identifica la tienda correspondiente.
- **Extracción Inteligente**: Obtiene nombre, precio exacto en Quetzales (Q), disponibilidad/stock e imagen del producto.
- **Cálculo Automático**:
  - Subtotal por ítem (Precio unitario × cantidad).
  - Subtotal acumulado de componentes.
  - Cargo por servicio de compra/gestión (12% editable).
  - Total general cotizado.
- **Generación de Documentos Profesionales**:
  - **PDF de Alta Calidad**: Listo para enviar directamente al cliente por WhatsApp o correo.
  - **HTML Interactivo**: Con vista previa y botón de impresión integrado.
  - **CSV de Detalle**: Para control interno de costos y márgenes.
- **Historial y Re-verificación de Precios**: Guarda todas las cotizaciones con correlativo único (`COT-2026-0001`) y permite volver a consultar los precios en vivo de cotizaciones guardadas para detectar variaciones.
- **Configuración Personalizable**: Permite editar el margen %, vigencia (días), nombre de negocio, teléfono, correo y condiciones de pago en `config.json`.

---

## Estructura del Proyecto

```
cotizador_emerson/
├── config.json               # Configuración de margen, vigencia y datos del negocio
├── requirements.txt          # Dependencias de Python
├── run.sh                    # Script de inicio rápido
├── main.py                   # Punto de entrada principal
├── data/
│   └── history.json          # Histórico de cotizaciones guardadas
├── output/
│   ├── quotes_pdf/           # Cotizaciones en PDF
│   ├── quotes_html/          # Cotizaciones en HTML
│   └── quotes_csv/           # Registros en CSV
├── src/
│   ├── models.py             # Modelos de datos (Producto, Cotización, Cliente)
│   ├── config.py             # Manejo de configuración
│   ├── scrapers/             # Scrapers de cada tienda guatemalteca
│   │   ├── base.py
│   │   ├── la_electronica.py
│   │   ├── electronica_diy.py
│   │   └── electronica_rych.py
│   ├── core/
│   │   ├── calculator.py     # Cálculos financieros y formato monetario (Q)
│   │   ├── history_manager.py# Manejo de histórico y re-verificación
│   │   └── exporter.py       # Renderizado y generación de PDF/HTML/CSV
│   ├── templates/
│   │   └── quote_template.html # Plantilla HTML/CSS comercial
│   └── ui/
│       └── cli.py            # Interfaz interactiva de terminal con 'rich'
└── tests/
    ├── test_scrapers.py
    └── test_calculator_exporter.py
```

---

## Cómo Ejecutar la Aplicación

### Opción 1: Con el lanzador automático (Recomendado)
```bash
./run.sh
```

### Opción 2: Manualmente con entorno virtual
```bash
# 1. Crear y activar entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Ejecutar la aplicación
python3 main.py
```

---

## Ejecución de Pruebas

Para verificar el funcionamiento de los scrapers con URLs en vivo:
```bash
.venv/bin/python3 tests/test_scrapers.py
.venv/bin/python3 tests/test_calculator_exporter.py
```
