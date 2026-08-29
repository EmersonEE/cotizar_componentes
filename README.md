# Cotizador de Componentes Electrónicos (Guatemala) ⚡

Herramienta en Python para integradores y vendedores de electrónica en Guatemala. Automatiza la búsqueda de componentes, cotizaciones con margen configurable (por defecto 10%, editable en `config.json`), cálculo de costos de envío por tienda y generación de cotizaciones profesionales en formato **PDF**, **HTML** y **CSV**, tanto por **Interfaz Gráfica Web (Streamlit)** como por **Terminal (CLI)**.

## Tiendas Locales Soportadas

1. **La Electrónica** (`https://laelectronica.com.gt/`)
2. **Electrónica DIY** (`https://www.electronicadiy.com/es`)
3. **Electrónica RyCH** (`https://electronicarych.com/`)

---

## Cómo Iniciar la Aplicación

### 🌐 Opción 1: Interfaz Gráfica Web (Recomendada)
Abre el dashboard en tu navegador con vista previa en tiempo real:
```bash
./run_web.sh
```

### 💻 Opción 2: Terminal Interactiva (CLI)
Ejecuta la interfaz de consola con menús y tablas en terminal:
```bash
./run.sh
```

---

## Características Principales

- **🌐 Dashboard Web de Dos Columnas**: Panel de control a la izquierda y vista previa exacta del documento a la derecha con actualización en tiempo real.
- **🔍 Metabuscador Multitienda en Paralelo**: Busca componentes por nombre o valor (ej. `ESP32`, `resistencia 220`) en las 3 tiendas al mismo tiempo en $\sim 1.5$s.
- **🧩 Desempaquetado Inteligente de Variantes**: En tiendas multivariante como *Electrónica DIY*, desglosa cada variante coincidente (1/4W, 1/2W, 2W, SMD) como opción independiente con su `?variant=<id>` exacto.
- **🚚 Costos de Envío con Reglas de Gratuidad**:
  - *RyCH:* Retiro en tienda ($Q\ 0.00$).
  - *La Electrónica:* Gratis desde $Q\ 200.00$, configurable si no alcanza.
  - *Electrónica DIY:* Gratis desde $Q\ 250.00$, configurable si no alcanza.
- **✏️ Edición y Versionado de Cotizaciones**: Permite abrir cotizaciones anteriores, modificar componentes/cantidades y guardar como nueva versión (`COT-2026-0001_v2`).
- **📑 Documentos Profesionales**: Generación automática de PDF con WeasyPrint, HTML responsivo y CSV interno.
- **💬 Enlace Directo a WhatsApp**: Envía cotizaciones a tus clientes por WhatsApp con un solo clic.
- **🗑️ Gestión completa del historial**: eliminar cotizaciones (con confirmación), estado **VENCIDA automático** al vencer la vigencia, y **exportar/importar** el historial completo (JSON/CSV).
- **🕓 Precio de referencia**: al buscar o agregar un producto se muestra el último precio cotizado para esa URL/SKU.
- **📦 Seguimiento de venta**: notas de factura/entrega en cotizaciones ACEPTADAS.
- **🧮 Dedupe automático**: componentes repetidos (misma URL/SKU) se fusionan al guardar la cotización.

---

## Pruebas Automatizadas

Toda la suite es offline/mocked (sin red ni datos reales). Ejecútala con:

```bash
.venv/bin/pip install -e ".[dev]"   # instala pytest y ruff
.venv/bin/pytest
.venv/bin/ruff check src app.py main.py tests
```

Los tests cubren: scrapers (con `fetch_url` mockeado), cálculo financiero y envíos,
exportación dual Cliente/Interna, parser BOM y scoring, optimizador mixto (búsqueda
acotada), persistencia segura (colisión de IDs, backup y recuperación), estados
comerciales, re-verificación paralela, historial (búsqueda, export/import, borrado)
y configuraciones.

## Respaldos

- `data/history.json.bak`: copia automática del historial antes de cada escritura.
- Menú Historial → **Exportar JSON/CSV** para respaldo manual y **Importar** para restaurar.

## 🔍 Chequeo de Salud de las Tiendas

Verifica que las 3 tiendas siguen parseando correctamente (precio, stock y búsqueda),
midiendo latencia — ideal para detectar cambios de HTML en las tiendas antes de que
afecten cotizaciones reales:

```bash
.venv/bin/python3 scripts/check_stores.py            # tabla legible
.venv/bin/python3 scripts/check_stores.py --json     # salida JSON (automatización)
.venv/bin/python3 main.py --check-stores             # integrado en el CLI
```

Exit code `0` si todo OK, `1` si alguna tienda falla. Ejemplo con cron (diario a las 8:00,
con registro de resultados):

```cron
0 8 * * * cd /home/emerson/Documentos/cotizador_emerson && .venv/bin/python3 scripts/check_stores.py --json >> data/health_check.log 2>&1 || echo "[$(date)] FALLO en tiendas" >> data/health_check.log
```
