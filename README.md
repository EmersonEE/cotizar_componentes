# Cotizador de Componentes Electrónicos (Guatemala) ⚡

Herramienta en Python para integradores y vendedores de electrónica en Guatemala. Automatiza la búsqueda de componentes, cotizaciones con margen configurable (por defecto 12%), cálculo de costos de envío por tienda y generación de cotizaciones profesionales en formato **PDF**, **HTML** y **CSV**.

## Tiendas Locales Soportadas

1. **La Electrónica** (`https://laelectronica.com.gt/`)
2. **Electrónica DIY** (`https://www.electronicadiy.com/es`)
3. **Electrónica RyCH** (`https://electronicarych.com/`)

---

## Características Principales

- **🔍 Metabuscador Multitienda en Paralelo**: Busca componentes por nombre o valor (ej. `ESP32`, `resistencia 220`) en las 3 tiendas al mismo tiempo en $\sim 1.5$s.
- **🧩 Desempaquetado Inteligente de Variantes**: En tiendas multivariante como *Electrónica DIY*, desglosa cada variante coincidente (1/4W, 1/2W, 2W, SMD) como opción independiente con su `?variant=<id>` exacto.
- **🚚 Costos de Envío con Reglas de Gratuidad**:
  - *RyCH:* Retiro en tienda ($Q\ 0.00$).
  - *La Electrónica:* Gratis desde $Q\ 150.00$, configurable si no alcanza.
  - *Electrónica DIY:* Gratis desde $Q\ 250.00$, configurable si no alcanza.
- **✏️ Edición y Versionado de Cotizaciones**: Permite abrir cotizaciones anteriores, modificar componentes/cantidades y guardar como nueva versión (`COT-2026-0001_v2`).
- **📑 Documentos Profesionales**: Generación automática de PDF con WeasyPrint, HTML responsivo y CSV interno.

---

## Cómo Iniciar la Aplicación

```bash
./run.sh
```

---

## Pruebas Automatizadas

```bash
.venv/bin/python3 tests/test_scrapers.py
.venv/bin/python3 tests/test_calculator_exporter.py
.venv/bin/python3 tests/test_shipping_and_versioning.py
.venv/bin/python3 tests/test_metasearch.py
```
