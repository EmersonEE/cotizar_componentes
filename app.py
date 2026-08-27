import sys
import copy
import urllib.parse
from pathlib import Path
from typing import List, Dict, Optional

import streamlit as st
import streamlit.components.v1 as components

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.models import Product, QuoteItem, Quote, Customer, StoreShippingDetail
from src.config import AppConfig
from src.scrapers import scrape_product, metasearch, SearchResultItem, StoreNotSupportedError
from src.core.calculator import QuoteCalculator, format_currency
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter, ExportResult
from src.core.bom_parser import parse_bom_text, ParsedBOMItem
from src.core.bom_searcher import search_bom_items_parallel, calculate_match_score, MatchResult

# 1. Configuración de página
st.set_page_config(
    page_title="Cotizador de Componentes - Guatemala",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 2. Instancias de servicio
@st.cache_resource
def get_services():
    config = AppConfig.load()
    history_mgr = HistoryManager()
    exporter = QuoteExporter()
    return config, history_mgr, exporter

config, history_mgr, exporter = get_services()

# 3. Inicialización del Estado de Sesión
def init_session_state():
    if "active_quote_id" not in st.session_state:
        st.session_state.active_quote_id = history_mgr.get_next_quote_id(config.quote_prefix)
    if "version" not in st.session_state:
        st.session_state.version = 1
    if "base_quote_id" not in st.session_state:
        st.session_state.base_quote_id = None
    if "customer_name" not in st.session_state:
        st.session_state.customer_name = ""
    if "customer_phone" not in st.session_state:
        st.session_state.customer_phone = ""
    if "quote_items" not in st.session_state:
        st.session_state.quote_items = []
    if "custom_shipping_costs" not in st.session_state:
        st.session_state.custom_shipping_costs = {}
    if "search_results" not in st.session_state:
        st.session_state.search_results = []
    if "last_search_query" not in st.session_state:
        st.session_state.last_search_query = ""
    if "editing_mode" not in st.session_state:
        st.session_state.editing_mode = False

init_session_state()

def reset_to_new_quote():
    st.session_state.active_quote_id = history_mgr.get_next_quote_id(config.quote_prefix)
    st.session_state.version = 1
    st.session_state.base_quote_id = None
    st.session_state.customer_name = ""
    st.session_state.customer_phone = ""
    st.session_state.quote_items = []
    st.session_state.custom_shipping_costs = {}
    st.session_state.search_results = []
    st.session_state.last_search_query = ""
    st.session_state.editing_mode = False

def load_quote_for_editing(quote: Quote):
    st.session_state.active_quote_id = quote.quote_id
    st.session_state.version = quote.version
    st.session_state.base_quote_id = quote.base_quote_id or quote.quote_id.split('_v')[0]
    st.session_state.customer_name = quote.customer.name
    st.session_state.customer_phone = quote.customer.phone
    st.session_state.quote_items = copy.deepcopy(quote.items)
    st.session_state.custom_shipping_costs = {
        sd.store_name: sd.shipping_cost for sd in quote.shipping_details
    }
    st.session_state.editing_mode = True

def generate_whatsapp_link(quote: Quote) -> str:
    phone_clean = "".join(filter(str.isdigit, quote.customer.phone)) if quote.customer.phone else ""
    if phone_clean and not phone_clean.startswith("502"):
        phone_clean = f"502{phone_clean}"

    text_msg = (
        f"¡Hola {quote.customer.name}! 👋\n"
        f"Te comparto la cotización solicitada: *{quote.quote_id}*\n\n"
        f"📦 *Total de componentes:* {len(quote.items)} ítems\n"
        f"💰 *Total cotizado:* {quote.currency_symbol} {quote.total:,.2f}\n"
        f"📅 *Vigencia:* Válida hasta el {quote.valid_until}\n\n"
        f"Adjunto encuentras el documento oficial en PDF. Quedo a la orden para confirmar tu pedido."
    )
    encoded = urllib.parse.quote_plus(text_msg)
    if phone_clean:
        return f"https://wa.me/{phone_clean}?text={encoded}"
    return f"https://wa.me/?text={encoded}"

# 4. Construcción del Objeto Quote Actual
def get_current_quote() -> Quote:
    items = st.session_state.quote_items
    customer = Customer(
        name=st.session_state.customer_name.strip() or "Cliente General",
        phone=st.session_state.customer_phone.strip()
    )
    
    store_subtotals = QuoteCalculator.calculate_store_subtotals(items) if items else {}
    shipping_details = QuoteCalculator.evaluate_shipping_details(
        store_subtotals,
        config.shipping_rules,
        st.session_state.custom_shipping_costs
    )

    if not items:
        display_items = [QuoteItem(Product("Sin componentes agregados", "", "N/A", 0.0), 1, 0.0, 0.0)]
    else:
        display_items = items

    return QuoteCalculator.build_quote(
        quote_id=st.session_state.active_quote_id,
        items=display_items,
        customer=customer,
        shipping_details=shipping_details,
        service_fee_percent=config.service_fee_percent,
        validity_days=config.validity_days,
        version=st.session_state.version,
        base_quote_id=st.session_state.base_quote_id,
        currency_symbol=config.currency_symbol,
        currency_code=config.currency_code
    )

# 5. Encabezado de la Aplicación
st.markdown("""
<style>
  .main-header {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    color: white;
    padding: 18px 24px;
    border-radius: 12px;
    margin-bottom: 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .main-header h1 {
    margin: 0;
    font-size: 22px;
    font-weight: 800;
    color: #38bdf8;
  }
  .main-header p {
    margin: 0;
    font-size: 13px;
    color: #94a3b8;
  }
  .badge-store-rych { background-color: #dcfce7; color: #166534; padding: 2px 8px; border-radius: 4px; font-weight: 600; font-size: 11px; }
  .badge-store-diy { background-color: #dbeafe; color: #1e40af; padding: 2px 8px; border-radius: 4px; font-weight: 600; font-size: 11px; }
  .badge-store-la { background-color: #fef3c7; color: #92400e; padding: 2px 8px; border-radius: 4px; font-weight: 600; font-size: 11px; }
</style>
<div class="main-header">
  <div>
    <h1>⚡ Cotizador de Componentes Electrónicos</h1>
    <p>Guatemala • La Electrónica | Electrónica DIY | Electrónica RyCH</p>
  </div>
</div>
""", unsafe_allow_html=True)

# 6. Pestañas Principales
tab_cotizador, tab_historial, tab_config = st.tabs([
    "⚡ Cotizador en Vivo",
    "📋 Historial & Versiones",
    "⚙️ Configuración"
])

# ==========================================
# PESTAÑA 1: COTIZADOR EN VIVO (2 COLUMNAS)
# ==========================================
with tab_cotizador:
    col_left, col_right = st.columns([1.15, 0.95], gap="large")

    # --------------------------------------------------
    # COLUMNA IZQUIERDA: CONSTRUCTOR DE COTIZACIÓN
    # --------------------------------------------------
    with col_left:
        if st.session_state.editing_mode:
            st.warning(f"✏️ **Modo Edición Activo:** Modificando cotización `{st.session_state.active_quote_id}`. Al guardar se creará la versión `v{st.session_state.version + 1}`.")

        # 1. Datos del Cliente
        st.markdown("#### 👤 Datos del Cliente")
        c1, c2 = st.columns([1.2, 1.0])
        with c1:
            st.session_state.customer_name = st.text_input("Nombre del Cliente", value=st.session_state.customer_name, placeholder="Ej. Ing. Carlos Mendoza")
        with c2:
            st.session_state.customer_phone = st.text_input("Teléfono / WhatsApp", value=st.session_state.customer_phone, placeholder="Ej. +502 4433-2211")

        st.divider()

        # 2. Buscador y Adición de Componentes (Incluye BOM Multilínea)
        st.markdown("#### 🔍 Agregar Componentes")
        modo_adicion = st.radio(
            "Método de adición:",
            ["📋 Pegar Lista Rápida (BOM)", "🔍 Buscar en las 3 tiendas (Metabuscador)", "🔗 Pegar URL directa"],
            horizontal=True
        )

        if modo_adicion == "📋 Pegar Lista Rápida (BOM)":
            st.caption("Pega una lista con cantidades y nombres (ej. formato WhatsApp). Se procesarán todas en paralelo:")
            bom_input = st.text_area(
                "Lista de Componentes",
                placeholder="2x ESP32 NodeMCU\n10x Resistencia 220 ohm 1/4W\nSensor de temperatura DHT22\nModulo Relay 5V 2 canales\nPantalla OLED 0.96 I2C",
                height=140,
                label_visibility="collapsed"
            )
            if st.button("⚡ Procesar Lista y Buscar en Paralelo", type="primary", use_container_width=True) and bom_input.strip():
                with st.spinner("Interpretando lista y buscando todos los componentes en paralelo..."):
                    parse_res = parse_bom_text(bom_input)
                    if parse_res.items:
                        match_results = search_bom_items_parallel(parse_res.items, max_workers=5)
                        added = 0
                        for m in match_results:
                            if m.best_match:
                                prod = scrape_product(m.best_match.url)
                                item = QuoteCalculator.create_quote_item(prod, m.bom_item.quantity)
                                st.session_state.quote_items.append(item)
                                added += 1
                        st.toast(f"✔ ¡{added} componentes agregados exitosamente!", icon="🚀")
                        st.rerun()
                    else:
                        st.error("No se pudo interpretar ningún componente del texto ingresado.")

        elif modo_adicion == "🔍 Buscar en las 3 tiendas (Metabuscador)":
            search_col1, search_col2 = st.columns([3, 1])
            with search_col1:
                search_query = st.text_input("Nombre o valor del componente", placeholder="Ej. ESP32, resistencia 220, pantalla OLED, LM358", label_visibility="collapsed")
            with search_col2:
                btn_buscar = st.button("Buscar 🚀", use_container_width=True)

            if btn_buscar and search_query.strip():
                with st.spinner(f"Consultando RyCH, La Electrónica y DIY en paralelo para '{search_query}'..."):
                    st.session_state.search_results = metasearch(search_query.strip(), max_per_store=5)
                    st.session_state.last_search_query = search_query.strip()

            if st.session_state.search_results:
                st.caption(f"Resultados para **'{st.session_state.last_search_query}'** ({len(st.session_state.search_results)} encontrados):")
                
                for idx, res in enumerate(st.session_state.search_results):
                    with st.container():
                        r_col1, r_col2, r_col3, r_col4 = st.columns([2.8, 1.1, 0.9, 1.0])
                        with r_col1:
                            store_class = "badge-store-rych" if "RyCH" in res.store_name else ("badge-store-diy" if "DIY" in res.store_name else "badge-store-la")
                            st.markdown(f"<span class='{store_class}'>{res.store_name}</span> **{res.title}**", unsafe_allow_html=True)
                        with r_col2:
                            st.markdown(f"**Q {res.unit_price:,.2f}**")
                            st.caption("Disponible" if res.in_stock else "🔴 Agotado")
                        with r_col3:
                            qty_key = f"qty_search_{idx}_{res.url}"
                            qty_val = st.number_input("Cant.", min_value=1, value=1, step=1, key=qty_key, label_visibility="collapsed")
                        with r_col4:
                            if st.button("➕ Agregar", key=f"btn_add_{idx}_{res.url}", use_container_width=True):
                                prod = scrape_product(res.url)
                                item = QuoteCalculator.create_quote_item(prod, qty_val)
                                st.session_state.quote_items.append(item)
                                st.toast(f"✔ Agregado: {qty_val}x {prod.name}", icon="✅")
                                st.rerun()
                        st.markdown("<hr style='margin: 4px 0; border: none; border-top: 1px dashed #e2e8f0;'>", unsafe_allow_html=True)
            elif btn_buscar:
                st.info("No se encontraron resultados para este término. Intenta con otra palabra clave o pega la URL directa.")

        else:
            url_col1, url_col2, url_col3 = st.columns([3, 1, 1])
            with url_col1:
                direct_url = st.text_input("URL del producto", placeholder="https://...", label_visibility="collapsed")
            with url_col2:
                direct_qty = st.number_input("Cantidad", min_value=1, value=1, step=1, label_visibility="collapsed")
            with url_col3:
                btn_add_url = st.button("➕ Extraer", use_container_width=True)

            if btn_add_url and direct_url.strip():
                with st.spinner("Extrayendo componente de la tienda..."):
                    try:
                        prod = scrape_product(direct_url.strip())
                        item = QuoteCalculator.create_quote_item(prod, direct_qty)
                        st.session_state.quote_items.append(item)
                        st.toast(f"✔ Agregado: {direct_qty}x {prod.name}", icon="✅")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al extraer: {e}")

        st.divider()

        # 3. Lista de Componentes Agregados (Editable)
        st.markdown(f"#### 📦 Componentes en la Cotización ({len(st.session_state.quote_items)})")
        
        if not st.session_state.quote_items:
            st.info("Aún no has agregado ningún componente a esta cotización.")
        else:
            items_to_delete = []
            for i, item in enumerate(st.session_state.quote_items):
                i_col1, i_col2, i_col3, i_col4, i_col5 = st.columns([2.8, 1.0, 0.9, 1.1, 0.5])
                with i_col1:
                    st.markdown(f"**{i+1}. {item.product.name}**")
                    st.caption(f"Tienda: {item.product.store_name}")
                with i_col2:
                    st.markdown(f"Q {item.unit_price:,.2f}")
                with i_col3:
                    new_q = st.number_input("Cant.", min_value=1, value=item.quantity, step=1, key=f"item_qty_{i}", label_visibility="collapsed")
                    if new_q != item.quantity:
                        st.session_state.quote_items[i] = QuoteCalculator.create_quote_item(item.product, new_q)
                        st.rerun()
                with i_col4:
                    st.markdown(f"**Q {item.subtotal:,.2f}**")
                with i_col5:
                    if st.button("🗑️", key=f"del_item_{i}", help="Eliminar componente"):
                        items_to_delete.append(i)

            if items_to_delete:
                for idx in reversed(items_to_delete):
                    st.session_state.quote_items.pop(idx)
                st.rerun()

        st.divider()

        # 4. Evaluación y Costos de Envío por Tienda
        st.markdown("#### 🚚 Costos de Envío por Tienda")
        if st.session_state.quote_items:
            store_subtotals = QuoteCalculator.calculate_store_subtotals(st.session_state.quote_items)
            for store_name, sub in store_subtotals.items():
                rule = config.shipping_rules.get(store_name, {})
                is_pickup = rule.get("is_pickup_only", False)
                thresh = rule.get("free_threshold")
                default_cost = float(rule.get("default_cost", 35.0))

                s_c1, s_c2 = st.columns([2.5, 1.5])
                with s_c1:
                    if is_pickup or thresh is None:
                        st.success(f"**{store_name}** (Subtotal: Q {sub:,.2f}) — ✔ Retiro en tienda (Sin costo)")
                    elif sub >= thresh:
                        st.success(f"**{store_name}** (Subtotal: Q {sub:,.2f}) — ✔ ¡Envío Gratis alcanzado! (Mínimo Q{thresh:,.0f})")
                    else:
                        st.warning(f"**{store_name}** (Subtotal: Q {sub:,.2f}) — No alcanza mínimo de Q{thresh:,.0f} para envío gratis")
                with s_c2:
                    if not is_pickup and (thresh is not None and sub < thresh):
                        cur_cost = st.session_state.custom_shipping_costs.get(store_name, default_cost)
                        cost_input = st.number_input(
                            f"Costo Envío ({store_name})",
                            min_value=0.0,
                            value=float(cur_cost),
                            step=5.0,
                            key=f"ship_cost_{store_name}"
                        )
                        st.session_state.custom_shipping_costs[store_name] = cost_input
        else:
            st.caption("Agrega componentes para calcular los envíos automáticamente.")

        st.divider()

        # 5. Resumen Financiero y Botones de Guardado
        current_quote = get_current_quote()
        
        sum_c1, sum_c2 = st.columns([1, 1])
        with sum_c1:
            st.metric("Subtotal Componentes", f"Q {current_quote.items_subtotal:,.2f}")
            st.metric(f"Servicio Gestión ({current_quote.service_fee_percent}%)", f"Q {current_quote.service_fee_amount:,.2f}")
        with sum_c2:
            st.metric("Total Envíos", f"Q {current_quote.total_shipping:,.2f}")
            st.metric("TOTAL COTIZADO", f"Q {current_quote.total:,.2f}")

        act_c1, act_c2 = st.columns([1, 1])
        with act_c1:
            if st.session_state.editing_mode:
                btn_save_label = f"💾 Guardar como v{st.session_state.version + 1}"
            else:
                btn_save_label = "💾 Guardar Cotización"

            if st.button(btn_save_label, type="primary", use_container_width=True, disabled=len(st.session_state.quote_items) == 0):
                if st.session_state.editing_mode:
                    new_qid, new_v, base_id = history_mgr.get_next_version_info(st.session_state.active_quote_id)
                    quote_to_save = QuoteCalculator.build_quote(
                        quote_id=new_qid,
                        items=st.session_state.quote_items,
                        customer=Customer(st.session_state.customer_name or "Cliente General", st.session_state.customer_phone),
                        shipping_details=current_quote.shipping_details,
                        service_fee_percent=config.service_fee_percent,
                        validity_days=config.validity_days,
                        version=new_v,
                        base_quote_id=base_id
                    )
                else:
                    quote_to_save = current_quote

                history_mgr.save_quote(quote_to_save)
                exporter.export_all(quote_to_save, config.business)
                st.success(f"✔ ¡Cotización `{quote_to_save.quote_id}` guardada con éxito (Cliente + Interno)!")
                st.session_state.editing_mode = False
                st.session_state.active_quote_id = quote_to_save.quote_id
                st.session_state.version = quote_to_save.version
                st.rerun()

        with act_c2:
            if st.button("📄 Nueva Cotización (Limpiar)", use_container_width=True):
                reset_to_new_quote()
                st.rerun()

    # --------------------------------------------------
    # COLUMNA DERECHA: VISTA PREVIA EN VIVO
    # --------------------------------------------------
    with col_right:
        p_c1, p_c2 = st.columns([1.2, 1.2])
        with p_c1:
            st.markdown("#### 👁️ Vista Previa en Vivo")
        with p_c2:
            tipo_vista = st.radio("Modo:", ["Cliente (Limpio)", "Interno (con Links)"], horizontal=True, label_visibility="collapsed")

        is_internal_view = (tipo_vista == "Interno (con Links)")
        current_quote = get_current_quote()

        # Renderizado instantáneo de HTML
        html_preview = exporter.render_html_string(current_quote, config.business, is_internal=is_internal_view)
        components.html(html_preview, height=720, scrolling=True)

        # Botones de Descarga Directa Organizados
        st.markdown("##### 📥 Descargas Oficiales")
        d_row1_c1, d_row1_c2 = st.columns([1, 1])
        d_row2_c1, d_row2_c2 = st.columns([1, 1])

        with d_row1_c1:
            try:
                from weasyprint import HTML
                html_client = exporter.render_html_string(current_quote, config.business, is_internal=False)
                pdf_client_bytes = HTML(string=html_client).write_pdf()
                st.download_button(
                    label="📑 Descargar PDF (Cliente)",
                    data=pdf_client_bytes,
                    file_name=f"{current_quote.quote_id}_Cliente.pdf",
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"PDF Cliente: {e}")

        with d_row1_c2:
            try:
                html_intern = exporter.render_html_string(current_quote, config.business, is_internal=True)
                pdf_intern_bytes = HTML(string=html_intern).write_pdf()
                st.download_button(
                    label="🔗 Descargar PDF (Interno con Links)",
                    data=pdf_intern_bytes,
                    file_name=f"{current_quote.quote_id}_Interna.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"PDF Interno: {e}")

        with d_row2_c1:
            csv_file = exporter.export_csv(current_quote)
            with open(csv_file, "r", encoding="utf-8-sig") as f:
                csv_data = f.read()
            st.download_button(
                label="📊 Descargar CSV",
                data=csv_data,
                file_name=f"{current_quote.quote_id}.csv",
                mime="text/csv",
                use_container_width=True
            )

        with d_row2_c2:
            wa_url = generate_whatsapp_link(current_quote)
            st.link_button("💬 Enviar WhatsApp", url=wa_url, use_container_width=True)

# ==========================================
# PESTAÑA 2: HISTORIAL Y EDICIÓN
# ==========================================
with tab_historial:
    st.markdown("### 📋 Historial de Cotizaciones Guardadas")
    saved_quotes = history_mgr.load_all_quotes()

    if not saved_quotes:
        st.info("No hay cotizaciones guardadas en el historial todavía.")
    else:
        filtro_txt = st.text_input("Buscar por ID o Cliente", placeholder="Filtrar cotizaciones...")
        filtered = [
            q for q in saved_quotes
            if not filtro_txt or filtro_txt.lower() in q.quote_id.lower() or filtro_txt.lower() in q.customer.name.lower()
        ]

        for q in reversed(filtered):
            with st.expander(f"📄 **{q.quote_id}** (v{q.version}) — {q.customer.name} — **Q {q.total:,.2f}** ({q.date})"):
                h_col1, h_col2 = st.columns([2, 1])
                with h_col1:
                    st.markdown(f"**Cliente:** {q.customer.name} | **Tel:** {q.customer.phone or 'N/A'}")
                    st.markdown(f"**Ítems:** {len(q.items)} | **Válida hasta:** {q.valid_until}")
                    
                    for it in q.items:
                        st.caption(f"• {it.quantity}x [{it.product.name}]({it.product.url}) ({it.product.store_name}) = Q {it.subtotal:,.2f}")
                    
                    st.markdown(f"**Subtotal:** Q {q.items_subtotal:,.2f} | **Margen (12%):** Q {q.service_fee_amount:,.2f} | **Envíos:** Q {q.total_shipping:,.2f} | **Total:** **Q {q.total:,.2f}**")

                with h_col2:
                    if st.button("✏️ Cargar para Editar", key=f"load_edit_{q.quote_id}", use_container_width=True):
                        load_quote_for_editing(q)
                        st.toast(f"Cotización {q.quote_id} cargada en el panel de trabajo.", icon="✏️")
                        st.rerun()

                    if st.button("🔄 Re-verificar Precios", key=f"reverify_{q.quote_id}", use_container_width=True):
                        with st.spinner(f"Re-verificando precios en vivo para {q.quote_id}..."):
                            up_q, changes = history_mgr.reverify_quote_prices(q.quote_id)
                            st.success(f"✔ Precios actualizados. Nuevo total: Q {up_q.total:,.2f}")
                            for c in changes:
                                diff_str = f"{c['diff']:+.2f}" if c['diff'] != 0 else "Sin cambio"
                                st.caption(f"• {c['product_name']}: Q {c['old_price']:.2f} → Q {c['new_price']:.2f} ({diff_str})")
                            st.rerun()

# ==========================================
# PESTAÑA 3: CONFIGURACIÓN
# ==========================================
with tab_config:
    st.markdown("### ⚙️ Configuración del Sistema y Negocio")
    
    cfg_c1, cfg_c2 = st.columns([1, 1])
    with cfg_c1:
        st.markdown("#### Parámetros de Cotización")
        new_fee = st.number_input("Margen de servicio predeterminado (%)", min_value=0.0, max_value=100.0, value=float(config.service_fee_percent), step=0.5)
        new_days = st.number_input("Vigencia de cotización (días)", min_value=1, max_value=60, value=int(config.validity_days), step=1)
        
        st.markdown("#### Umbrales de Envío Gratis")
        new_la_thresh = st.number_input("La Electrónica: Mínimo envío gratis (Q)", value=float(config.shipping_rules["La Electrónica"]["free_threshold"]))
        new_la_cost = st.number_input("La Electrónica: Costo si no alcanza mínimo (Q)", value=float(config.shipping_rules["La Electrónica"]["default_cost"]))
        new_diy_thresh = st.number_input("Electrónica DIY: Mínimo envío gratis (Q)", value=float(config.shipping_rules["Electrónica DIY"]["free_threshold"]))
        new_diy_cost = st.number_input("Electrónica DIY: Costo si no alcanza mínimo (Q)", value=float(config.shipping_rules["Electrónica DIY"]["default_cost"]))

    with cfg_c2:
        st.markdown("#### Datos de tu Negocio (Encabezado y Pie de Cotización)")
        new_biz_name = st.text_input("Nombre Comercial", value=config.business.name)
        new_biz_owner = st.text_input("Atención / Titular", value=config.business.owner)
        new_biz_phone = st.text_input("Teléfono / WhatsApp de Contacto", value=config.business.phone)
        new_biz_email = st.text_input("Correo Electrónico", value=config.business.email)
        new_biz_address = st.text_input("Ubicación / Dirección", value=config.business.address)

    if st.button("💾 Guardar Configuración", type="primary"):
        config.service_fee_percent = new_fee
        config.validity_days = new_days
        config.business.name = new_biz_name
        config.business.owner = new_biz_owner
        config.business.phone = new_biz_phone
        config.business.email = new_biz_email
        config.business.address = new_biz_address
        config.shipping_rules["La Electrónica"]["free_threshold"] = new_la_thresh
        config.shipping_rules["La Electrónica"]["default_cost"] = new_la_cost
        config.shipping_rules["Electrónica DIY"]["free_threshold"] = new_diy_thresh
        config.shipping_rules["Electrónica DIY"]["default_cost"] = new_diy_cost
        config.save()
        st.success("✔ ¡Configuración actualizada exitosamente!")
        st.rerun()
