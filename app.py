import sys
import copy
import tempfile
import urllib.parse
from pathlib import Path
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.logging_setup import setup_logging

from src.models import (
    Product, QuoteItem, Quote, Customer, QuoteStatus, InvalidStatusTransitionError
)
from src.config import AppConfig
from src.stores import STORE_NAMES
from src.scrapers import scrape_product, metasearch
from src.core.calculator import QuoteCalculator
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter
from src.core.bom_parser import parse_bom_text, parse_bom_text_hybrid
from src.core.ai_service import suggest_alternatives_with_ai, check_ollama_status
from src.core.bom_searcher import (
    search_bom_items_parallel,
    calculate_match_score,
    build_all_bom_scenarios
)
from src.services.quote_flow import QuoteFlowService

setup_logging()

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


@st.cache_data(show_spinner=False)
def _render_pdf_bytes(html: str) -> bytes:
    """Renderiza HTML a PDF con WeasyPrint, cacheado por contenido (T2: evita
    regenerar PDFs costosos en cada rerun de la app)."""
    from weasyprint import HTML
    return HTML(string=html).write_pdf()

# 3. Inicialización del Estado de Sesión
def init_session_state():
    if "active_quote_id" not in st.session_state:
        st.session_state.active_quote_id = history_mgr.get_next_quote_id(config.quote_prefix)
    if "version" not in st.session_state:
        st.session_state.version = 1
    if "base_quote_id" not in st.session_state:
        st.session_state.base_quote_id = None
    if "status" not in st.session_state:
        st.session_state.status = QuoteStatus.GUARDADA.value
    if "customer_name" not in st.session_state:
        st.session_state.customer_name = ""
    if "customer_phone" not in st.session_state:
        st.session_state.customer_phone = ""
    if "customer_email" not in st.session_state:
        st.session_state.customer_email = ""
    if "customer_notes" not in st.session_state:
        st.session_state.customer_notes = ""
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
    if "bom_match_results" not in st.session_state:
        st.session_state.bom_match_results = None
    if "bom_scenarios" not in st.session_state:
        st.session_state.bom_scenarios = None
    if "service_fee_percent" not in st.session_state:
        st.session_state.service_fee_percent = config.service_fee_percent

init_session_state()

def reset_to_new_quote():
    st.session_state.active_quote_id = history_mgr.get_next_quote_id(config.quote_prefix)
    st.session_state.version = 1
    st.session_state.base_quote_id = None
    st.session_state.status = QuoteStatus.GUARDADA.value
    st.session_state.customer_name = ""
    st.session_state.customer_phone = ""
    st.session_state.customer_email = ""
    st.session_state.customer_notes = ""
    st.session_state.quote_items = []
    st.session_state.custom_shipping_costs = {}
    st.session_state.search_results = []
    st.session_state.last_search_query = ""
    st.session_state.editing_mode = False
    st.session_state.bom_match_results = None
    st.session_state.bom_scenarios = None
    st.session_state.service_fee_percent = config.service_fee_percent

def load_quote_for_editing(quote: Quote):
    st.session_state.active_quote_id = quote.quote_id
    st.session_state.version = quote.version
    st.session_state.base_quote_id = quote.base_quote_id or quote.quote_id.split('_v')[0]
    st.session_state.status = quote.status
    st.session_state.customer_name = quote.customer.name
    st.session_state.customer_phone = quote.customer.phone
    st.session_state.customer_email = quote.customer.email
    st.session_state.customer_notes = quote.customer.notes
    st.session_state.quote_items = copy.deepcopy(quote.items)
    st.session_state.custom_shipping_costs = {
        sd.store_name: sd.shipping_cost
        for sd in quote.shipping_details
        if sd.shipping_was_custom
    }
    st.session_state.service_fee_percent = quote.service_fee_percent
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
        phone=st.session_state.customer_phone.strip(),
        email=st.session_state.customer_email.strip(),
        notes=st.session_state.customer_notes.strip()
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

    q = QuoteCalculator.build_quote(
        quote_id=st.session_state.active_quote_id,
        items=display_items,
        customer=customer,
        shipping_details=shipping_details,
        service_fee_percent=st.session_state.service_fee_percent,
        validity_days=config.validity_days,
        version=st.session_state.version,
        base_quote_id=st.session_state.base_quote_id,
        currency_symbol=config.currency_symbol,
        currency_code=config.currency_code
    )
    q.status = st.session_state.status
    return q

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
  
  .status-badge-aceptada { background-color: #dcfce7; color: #166534; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; }
  .status-badge-enviada { background-color: #f3e8ff; color: #6b21a8; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; }
  .status-badge-guardada { background-color: #e0f2fe; color: #0369a1; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; }
  .status-badge-rechazada { background-color: #fee2e2; color: #991b1b; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; }
  .status-badge-vencida { background-color: #f1f5f9; color: #475569; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; }
  .status-badge-borrador { background-color: #fef9c3; color: #854d0e; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; }
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
            st.session_state.customer_email = st.text_input("Correo Electrónico (opcional)", value=st.session_state.customer_email, placeholder="cliente@correo.com")
        with c2:
            st.session_state.customer_phone = st.text_input("Teléfono / WhatsApp (opcional)", value=st.session_state.customer_phone, placeholder="Ej. +502 4433-2211")
            st.session_state.customer_notes = st.text_input("Notas / Observaciones (opcional)", value=st.session_state.customer_notes, placeholder="Ej. Proyecto IoT, entrega urgente")

        st.divider()

        # 2. Buscador y Adición de Componentes (Incluye BOM Multilínea Mejorado e Ingreso Manual)
        st.markdown("#### 🔍 Agregar Componentes")
        modo_adicion = st.radio(
            "Método de adición:",
            ["📋 Pegar Lista Rápida (BOM)", "🔍 Buscar en las 3 tiendas (Metabuscador)", "🔗 Pegar URL directa", "✍️ Ingreso Manual"],
            horizontal=True
        )

        if modo_adicion == "📋 Pegar Lista Rápida (BOM)":
            st.caption("Pega una lista multilínea (ej. formato WhatsApp, notas de audio transcritas). Se buscarán todas en paralelo:")
            
            ai_is_ready = config.enable_ai and check_ollama_status(config.ollama_url)
            use_ai_extraction = st.checkbox(
                f"🧠 Extraer con IA Local (Ollama: {config.ollama_model})",
                value=ai_is_ready,
                help="Utiliza el modelo local Qwen 2.5 7B en tu GPU para interpretar mensajes desordenados o conversacionales de WhatsApp."
            )

            bom_input = st.text_area(
                "Lista de Componentes",
                placeholder="2x ESP32 NodeMCU\n10x Resistencia 220 ohm 1/4W\nSensor de temperatura DHT22\nModulo Relay 5V 2 canales\nPantalla OLED 0.96 I2C",
                height=130,
                label_visibility="collapsed"
            )
            
            b_c1, b_c2 = st.columns([1.5, 1.0])
            with b_c1:
                if st.button("⚡ Procesar Lista y Buscar en Paralelo", type="primary", use_container_width=True) and bom_input.strip():
                    with st.spinner("Interpretando lista (IA / Regex) y consultando tiendas en paralelo..."):
                        parse_res = parse_bom_text_hybrid(bom_input, config=config, force_ai=use_ai_extraction) if use_ai_extraction else parse_bom_text(bom_input)
                        if parse_res.items:
                            match_results = search_bom_items_parallel(parse_res.items, max_workers=5)
                            st.session_state.bom_match_results = match_results
                            st.session_state.bom_scenarios = None
                            if getattr(parse_res, "source", "") == "ai_ollama":
                                st.toast("✔ Componentes extraídos con IA Local (Qwen 2.5)", icon="🧠")
                            st.rerun()
                        else:
                            st.error("No se pudo interpretar ningún componente del texto ingresado.")

            with b_c2:
                if st.session_state.bom_match_results and st.button("🗑️ Descartar Búsqueda BOM", use_container_width=True):
                    st.session_state.bom_match_results = None
                    st.session_state.bom_scenarios = None
                    st.rerun()

            # Revisión interactiva de candidatos por línea
            if st.session_state.bom_match_results:
                st.markdown("---")
                st.markdown("##### 📋 Selección y Confirmación de Candidatos por Línea")
                
                unfound_lines = []
                review_needed = []

                for idx, m in enumerate(st.session_state.bom_match_results):
                    with st.container():
                        st.markdown(f"**Línea #{idx+1}:** `{m.bom_item.quantity}x {m.bom_item.product_query}`")
                        
                        if m.all_candidates:
                            options_labels = []
                            for cand, score in m.all_candidates:
                                st_tag = "ALTA" if score >= 0.70 else ("MEDIA" if score >= 0.50 else "REVISAR")
                                stock_tag = "Disponible" if cand.in_stock else "Agotado"
                                options_labels.append(f"[{cand.store_name}] {cand.title} — Q {cand.unit_price:,.2f} ({stock_tag}) [Score: {int(score*100)}% - {st_tag}]")
                            
                            options_labels.append("❌ Ninguno / No disponible")

                            # Default index
                            default_idx = 0
                            if m.selected_candidate:
                                for opt_i, (cand, _) in enumerate(m.all_candidates):
                                    if cand.url == m.selected_candidate.url:
                                        default_idx = opt_i
                                        break
                            else:
                                default_idx = len(options_labels) - 1

                            sel_option = st.selectbox(
                                f"Candidato asignado para línea {idx+1}",
                                options_labels,
                                index=default_idx,
                                key=f"bom_sel_{idx}",
                                label_visibility="collapsed"
                            )

                            if sel_option == "❌ Ninguno / No disponible":
                                m.selected_candidate = None
                                m.status = "NO_ENCONTRADO"
                                unfound_lines.append(f"{m.bom_item.quantity}x {m.bom_item.product_query}")
                            else:
                                chosen_idx = options_labels.index(sel_option)
                                chosen_cand, chosen_score = m.all_candidates[chosen_idx]
                                m.selected_candidate = chosen_cand
                                m.confidence_score = chosen_score
                                if chosen_score >= 0.70 and chosen_cand.in_stock:
                                    m.status = "ALTA"
                                elif chosen_score >= 0.50:
                                    m.status = "MEDIA"
                                else:
                                    m.status = "REVISAR"
                                    review_needed.append((idx+1, m))

                                if m.status == "REVISAR":
                                    st.warning(f"⚠️ Coincidencia clasificada como **REVISAR** (Score: {int(chosen_score*100)}%). Por favor confirma si es el producto correcto:")
                                    m.is_confirmed = st.checkbox(f"Confirmar componente para '{m.bom_item.product_query}'", value=m.is_confirmed, key=f"conf_rev_{idx}")
                        else:
                            st.error("❌ No se encontraron candidatos con precio válido en ninguna tienda.")
                            m.selected_candidate = None
                            m.status = "NO_ENCONTRADO"
                            unfound_lines.append(f"{m.bom_item.quantity}x {m.bom_item.product_query}")

                        # Sugerencias con IA si la IA está activa
                        if config.enable_ai:
                            with st.expander(f"💡 Sugerir Reemplazos / Equivalentes con IA para '{m.bom_item.product_query}'"):
                                if st.button("🧠 Consultar alternativas a la IA Local", key=f"btn_alt_ai_{idx}"):
                                    with st.spinner("Analizando compatibilidad y componentes alternativos..."):
                                        alts = suggest_alternatives_with_ai(m.bom_item.product_query, host=config.ollama_url, model=config.ollama_model)
                                        st.session_state[f"ai_alts_{idx}"] = alts

                                if f"ai_alts_{idx}" in st.session_state and st.session_state[f"ai_alts_{idx}"]:
                                    alts = st.session_state[f"ai_alts_{idx}"]
                                    for alt_i, alt in enumerate(alts):
                                        st.markdown(f"• **{alt['nombre']}** ({alt['compatibilidad']}) — *{alt['explicacion']}*")
                                        if st.button(f"🔍 Asignar y buscar '{alt['nombre']}'", key=f"btn_use_alt_{idx}_{alt_i}"):
                                            with st.spinner(f"Buscando '{alt['nombre']}' en las 3 tiendas..."):
                                                try:
                                                    raw_cands = metasearch(alt['nombre'], max_per_store=5)
                                                    valid_cands = [c for c in raw_cands if c.unit_price > 0]
                                                    if valid_cands:
                                                        scored = []
                                                        for vc in valid_cands:
                                                            sc = calculate_match_score(alt['nombre'], vc.title, vc.in_stock)
                                                            if sc >= 0.15:
                                                                scored.append((vc, sc))
                                                        scored.sort(key=lambda x: (x[1], x[0].in_stock, -x[0].unit_price), reverse=True)
                                                        m.all_candidates = scored
                                                        if scored:
                                                            m.selected_candidate = scored[0][0]
                                                            m.confidence_score = scored[0][1]
                                                            m.status = "ALTA" if scored[0][1] >= 0.70 and scored[0][0].in_stock else "MEDIA"
                                                            m.is_confirmed = True
                                                            st.toast(f"✔ Alternativa '{alt['nombre']}' asignada a la línea {idx+1}", icon="💡")
                                                            st.rerun()
                                                    else:
                                                        st.warning(f"No se encontraron unidades en stock para '{alt['nombre']}'.")
                                                except Exception as e:
                                                    st.error(f"Error al buscar alternativa: {e}")

                        st.markdown("<hr style='margin: 4px 0; border: none; border-top: 1px dashed #e2e8f0;'>", unsafe_allow_html=True)

                if unfound_lines:
                    st.warning(f"⚠️ **Componentes no encontrados / descartados ({len(unfound_lines)}):**\n" + "\n".join([f"- {u}" for u in unfound_lines]))

                # Botón para generar escenarios con la selección actual
                if st.button("🚀 Generar las 4 Opciones de Cotización (Incluye Mixto Óptimo)", type="primary", use_container_width=True):
                    current_cust = Customer(
                        name=st.session_state.customer_name.strip() or "Cliente General",
                        phone=st.session_state.customer_phone.strip(),
                        email=st.session_state.customer_email.strip(),
                        notes=st.session_state.customer_notes.strip()
                    )
                    scenarios = build_all_bom_scenarios(
                        match_results=st.session_state.bom_match_results,
                        customer=current_cust,
                        config=config,
                        service_fee_percent=st.session_state.service_fee_percent
                    )
                    st.session_state.bom_scenarios = scenarios
                    st.rerun()

            # Visualización de los 4 escenarios de cotización generados
            if st.session_state.bom_scenarios:
                st.markdown("---")
                st.markdown("#### 🎯 Comparativa de las 4 Opciones de Cotización")
                
                for sc in st.session_state.bom_scenarios:
                    with st.container():
                        sc_c1, sc_c2 = st.columns([2.5, 1.2])
                        with sc_c1:
                            st.markdown(f"##### [{sc.scenario_id}] {sc.title}")
                            st.caption(f"Cobertura: **{sc.coverage_label}** | Componentes: Q {sc.quote.items_subtotal:,.2f} | Envíos: Q {sc.quote.total_shipping:,.2f} | Margen: Q {sc.quote.service_fee_amount:,.2f}")
                            st.markdown(f"**TOTAL: Q {sc.quote.total:,.2f}**")
                        with sc_c2:
                            if st.button(f"➕ Cargar Opción {sc.scenario_id}", key=f"load_sc_{sc.scenario_id}", use_container_width=True, disabled=len(sc.items) == 0):
                                st.session_state.quote_items = copy.deepcopy(sc.items)
                                st.session_state.custom_shipping_costs = {
                                    sd.store_name: sd.shipping_cost
                                    for sd in sc.quote.shipping_details
                                    if sd.shipping_was_custom
                                }
                                st.session_state.bom_match_results = None
                                st.session_state.bom_scenarios = None
                                st.toast(f"✔ ¡Opción '{sc.title}' cargada a la cotización activa!", icon="🚀")
                                st.rerun()
                        st.markdown("<hr style='margin: 4px 0; border: none; border-top: 1px solid #cbd5e1;'>", unsafe_allow_html=True)

        elif modo_adicion == "🔍 Buscar en las 3 tiendas (Metabuscador)":
            search_col1, search_col2 = st.columns([3, 1])
            with search_col1:
                search_query = st.text_input("Nombre o valor del componente", placeholder="Ej. ESP32, resistencia 220, pantalla OLED, LM358", label_visibility="collapsed")
            with search_col2:
                btn_buscar = st.button("Buscar 🚀", use_container_width=True)

            if btn_buscar and search_query.strip():
                try:
                    with st.spinner(f"Consultando RyCH, La Electrónica y DIY en paralelo para '{search_query}'..."):
                        raw_res = metasearch(search_query.strip(), max_per_store=5)
                        st.session_state.search_results = [r for r in raw_res if r.unit_price > 0]
                        st.session_state.last_search_query = search_query.strip()
                except Exception as e:
                    st.error(f"❌ Error al consultar tiendas: {e}")
                    st.session_state.search_results = []

            if st.session_state.search_results:
                st.caption(f"Resultados para **'{st.session_state.last_search_query}'** ({len(st.session_state.search_results)} encontrados con precio válido):")
                
                for idx, res in enumerate(st.session_state.search_results):
                    with st.container():
                        r_col1, r_col2, r_col3, r_col4 = st.columns([2.8, 1.1, 0.9, 1.0])
                        with r_col1:
                            store_class = "badge-store-rych" if "RyCH" in res.store_name else ("badge-store-diy" if "DIY" in res.store_name else "badge-store-la")
                            st.markdown(f"<span class='{store_class}'>{res.store_name}</span> **{res.title}**", unsafe_allow_html=True)
                            # F4: precio histórico de referencia para este producto
                            try:
                                hist = history_mgr.get_price_history(url=res.url, limit=1)
                                if hist:
                                    st.caption(f"🕓 Últ. cotizado: **Q {hist[0]['unit_price']:,.2f}** ({hist[0]['date']})")
                            except Exception:
                                pass
                        with r_col2:
                            st.markdown(f"**Q {res.unit_price:,.2f}**")
                            st.caption("Disponible" if res.in_stock else "🔴 Agotado")
                        with r_col3:
                            qty_key = f"qty_search_{idx}_{res.url}"
                            qty_val = st.number_input("Cant.", min_value=1, value=1, step=1, key=qty_key, label_visibility="collapsed")
                        with r_col4:
                            if st.button("➕ Agregar", key=f"btn_add_{idx}_{res.url}", use_container_width=True):
                                try:
                                    prod = scrape_product(res.url)
                                except Exception:
                                    prod = Product(
                                        name=res.title,
                                        url=res.url,
                                        store_name=res.store_name,
                                        unit_price=res.unit_price,
                                        in_stock=res.in_stock,
                                        stock_status=res.stock_status,
                                        image_url=res.image_url
                                    )
                                item = QuoteCalculator.create_quote_item(prod, qty_val)
                                st.session_state.quote_items.append(item)
                                st.toast(f"✔ Agregado: {qty_val}x {prod.name}", icon="✅")
                                st.rerun()
                        st.markdown("<hr style='margin: 4px 0; border: none; border-top: 1px dashed #e2e8f0;'>", unsafe_allow_html=True)
            elif btn_buscar:
                st.info(f"No se encontraron resultados válidos para '{st.session_state.get('last_search_query', search_query)}'.")
                if config.enable_ai:
                    with st.expander(f"💡 Sugerencias de Reemplazos / Alternativas con IA para '{search_query}'"):
                        if st.button("🧠 Consultar alternativas a la IA Local", key="btn_search_ai_alt"):
                            with st.spinner("Consultando equivalencias a la IA Local..."):
                                st.session_state.search_ai_alts = suggest_alternatives_with_ai(search_query, host=config.ollama_url, model=config.ollama_model)
                        if "search_ai_alts" in st.session_state and st.session_state.search_ai_alts:
                            for alt in st.session_state.search_ai_alts:
                                st.markdown(f"• **{alt['nombre']}** ({alt['compatibilidad']}) — *{alt['explicacion']}*")

        elif modo_adicion == "🔗 Pegar URL directa":
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
                        if prod.unit_price <= 0:
                            st.error("El producto no tiene un precio válido en la tienda.")
                        else:
                            item = QuoteCalculator.create_quote_item(prod, direct_qty)
                            st.session_state.quote_items.append(item)
                            st.toast(f"✔ Agregado: {direct_qty}x {prod.name}", icon="✅")
                            st.rerun()
                    except Exception as e:
                        st.error(f"❌ Error al extraer producto: {e}")

        elif modo_adicion == "✍️ Ingreso Manual":
            st.caption("Ingresa manualmente los datos del componente cuando una tienda esté caída o el producto no esté en la web:")
            m_col1, m_col2 = st.columns([2, 1])
            with m_col1:
                man_name = st.text_input("Nombre / Descripción del Componente", placeholder="Ej. Transformador 12V 2A", key="man_name")
                man_url = st.text_input("URL de referencia (opcional)", placeholder="https://...", key="man_url")
            with m_col2:
                man_store = st.selectbox("Tienda / Proveedor", STORE_NAMES + ["Otro Proveedor"], key="man_store")
                man_sku = st.text_input("SKU / Código (opcional)", placeholder="Ej. TR-12V2A", key="man_sku")

            m_row2_1, m_row2_2, m_row2_3, m_row2_4 = st.columns([1.2, 1, 1.2, 1.4])
            with m_row2_1:
                man_price = st.number_input("Precio Unitario (Q)", min_value=0.01, value=10.0, step=1.0, key="man_price")
            with m_row2_2:
                man_qty = st.number_input("Cantidad", min_value=1, value=1, step=1, key="man_qty")
            with m_row2_3:
                man_stock = st.selectbox("Disponibilidad", ["Disponible", "Agotado"], key="man_stock")
            with m_row2_4:
                st.write("")
                st.write("")
                if st.button("➕ Agregar Manual", type="primary", use_container_width=True):
                    if not man_name.strip():
                        st.error("El nombre del componente es obligatorio.")
                    else:
                        man_prod = Product(
                            name=man_name.strip(),
                            url=man_url.strip(),
                            store_name=man_store,
                            unit_price=float(man_price),
                            in_stock=(man_stock == "Disponible"),
                            stock_status=man_stock,
                            sku=man_sku.strip() or None,
                            is_manual=True
                        )
                        item = QuoteCalculator.create_quote_item(man_prod, int(man_qty))
                        st.session_state.quote_items.append(item)
                        st.toast(f"✔ Agregado manualmente: {man_qty}x {man_name}", icon="✍️")
                        st.rerun()

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
                    manual_tag = " <span class='badge-store-la'>⚠️ Manual</span>" if item.product.is_manual else ""
                    sku_text = f" | SKU: `{item.product.sku}`" if item.product.sku else ""
                    st.markdown(f"**{i+1}. {item.product.name}**{manual_tag}", unsafe_allow_html=True)
                    st.caption(f"Tienda: {item.product.store_name}{sku_text}")
                    # F4: precio histórico de referencia (última vez cotizado)
                    try:
                        hist = history_mgr.get_price_history(url=item.product.url, sku=item.product.sku, limit=1)
                        if hist:
                            st.caption(f"🕓 Últ. cotizado: **Q {hist[0]['unit_price']:,.2f}** ({hist[0]['date']}, {hist[0]['quote_id']})")
                    except Exception:
                        pass
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

                # F5: editar precio unitario y SKU de un componente ya agregado
                with st.expander("✏️ Editar precio / SKU", key=f"edit_item_{i}"):
                    new_price = st.number_input(
                        "Precio unitario (Q)", min_value=0.01,
                        value=float(item.unit_price), step=1.0, key=f"edit_price_{i}",
                    )
                    new_sku = st.text_input("SKU (opcional)", value=item.product.sku or "", key=f"edit_sku_{i}")
                    if st.button("Aplicar cambios", key=f"apply_edit_{i}"):
                        prod = copy.deepcopy(item.product)
                        prod.unit_price = round(float(new_price), 2)
                        prod.sku = new_sku.strip() or None
                        st.session_state.quote_items[i] = QuoteCalculator.create_quote_item(prod, item.quantity)
                        st.toast("✔ Precio/SKU actualizado", icon="✏️")
                        st.rerun()

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

        # F5: margen configurable por cotización (no solo el global de config.json)
        st.number_input(
            "Margen de servicio (%) para esta cotización",
            min_value=0.0,
            max_value=100.0,
            value=float(st.session_state.service_fee_percent),
            step=0.5,
            key="service_fee_percent",
        )

        sum_c1, sum_c2 = st.columns([1, 1])
        with sum_c1:
            st.metric("Subtotal Componentes", f"Q {current_quote.items_subtotal:,.2f}")
            st.metric(f"Servicio Gestión ({current_quote.service_fee_percent}%):", f"Q {current_quote.service_fee_amount:,.2f}")
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
                        customer=Customer(
                            name=st.session_state.customer_name.strip() or "Cliente General",
                            phone=st.session_state.customer_phone.strip(),
                            email=st.session_state.customer_email.strip(),
                            notes=st.session_state.customer_notes.strip()
                        ),
                        shipping_details=current_quote.shipping_details,
                        service_fee_percent=st.session_state.service_fee_percent,
                        validity_days=config.validity_days,
                        version=new_v,
                        base_quote_id=base_id
                    )
                    quote_to_save.status = QuoteStatus.GUARDADA.value
                else:
                    quote_to_save = current_quote
                    quote_to_save.status = QuoteStatus.GUARDADA.value

                # T9: guardado + dedupe + exportación centralizados en el servicio compartido
                flow = QuoteFlowService(config, history_mgr, exporter)
                res = flow.save_and_export(quote_to_save)
                if res.merged_count:
                    st.toast(f"🧮 Se fusionaron {res.merged_count} componentes repetidos (misma URL/SKU)", icon="🧮")
                st.success(f"✔ ¡Cotización `{res.quote.quote_id}` guardada con éxito!")
                st.session_state.editing_mode = False
                st.session_state.active_quote_id = res.quote.quote_id
                st.session_state.version = res.quote.version
                st.session_state.status = res.quote.status
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
            tipo_vista = st.radio("Modo:", ["Cliente (Limpio)", "Interno (con Links y Estado)"], horizontal=True, label_visibility="collapsed")

        is_internal_view = (tipo_vista == "Interno (con Links y Estado)")
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
                html_client = exporter.render_html_string(current_quote, config.business, is_internal=False)
                pdf_client_bytes = _render_pdf_bytes(html_client)
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
                pdf_intern_bytes = _render_pdf_bytes(html_intern)
                st.download_button(
                    label="🔗 Descargar PDF (Interno)",
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
    st.markdown("### 📋 Historial y Búsqueda de Cotizaciones")
    
    f_col1, f_col2 = st.columns([2.5, 1.2])
    with f_col1:
        filtro_txt = st.text_input("🔍 Buscar por ID, Nombre de Cliente, Teléfono, Email o Fecha", placeholder="Escribe para buscar...")
    with f_col2:
        filtro_estado = st.selectbox("Filtrar por Estado Comercial", ["TODOS", "BORRADOR", "GUARDADA", "ENVIADA", "ACEPTADA", "RECHAZADA", "VENCIDA"])

    # F7: exportar / importar historial (backup manual)
    exp_col1, exp_col2, exp_col3 = st.columns([1, 1, 2])
    with exp_col1:
        if st.button("💾 Exportar JSON", use_container_width=True):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_path = Path(tempfile.gettempdir()) / f"historial_{stamp}.json"
            history_mgr.export_history(export_path)
            st.session_state["export_json_path"] = str(export_path)
    if st.session_state.get("export_json_path") and Path(st.session_state["export_json_path"]).exists():
        with exp_col1:
            p = Path(st.session_state["export_json_path"])
            st.download_button("⬇️ Descargar JSON", data=p.read_bytes(), file_name=p.name, mime="application/json")
    with exp_col2:
        if st.button("💾 Exportar CSV", use_container_width=True):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_path = Path(tempfile.gettempdir()) / f"historial_{stamp}.csv"
            history_mgr.export_history_csv(export_path)
            st.session_state["export_csv_path"] = str(export_path)
    if st.session_state.get("export_csv_path") and Path(st.session_state["export_csv_path"]).exists():
        with exp_col2:
            p = Path(st.session_state["export_csv_path"])
            st.download_button("⬇️ Descargar CSV", data=p.read_bytes(), file_name=p.name, mime="text/csv")
    with exp_col3:
        import_file = st.file_uploader("📥 Importar historial (archivo JSON exportado)", type=["json"], key="import_history")
        if import_file is not None and st.button("Importar", use_container_width=True):
            with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as tmp:
                tmp.write(import_file.getvalue())
                tmp_path = tmp.name
            try:
                added = history_mgr.import_history(Path(tmp_path))
                st.success(f"✔ Importación completada: {added} cotización(es) agregada(s).")
                st.rerun()
            except Exception as e:
                st.error(f"Error al importar: {e}")

    filtered_quotes = history_mgr.search_quotes(query=filtro_txt, status_filter=filtro_estado)

    if not filtered_quotes:
        if filtro_txt or filtro_estado != "TODOS":
            st.info("No se encontraron cotizaciones con los criterios seleccionados.")
        else:
            st.info("No hay cotizaciones guardadas en el historial todavía.")
    else:
        st.caption(f"Mostrando {len(filtered_quotes)} cotizaciones registradas:")
        for q in reversed(filtered_quotes):
            eff_status = history_mgr.effective_status(q)
            st_class = f"status-badge-{eff_status.lower()}"
            header_title = f"📄 **{q.quote_id}** (v{q.version}) — {q.customer.name} — **Q {q.total:,.2f}** ({q.date})"
            
            with st.expander(header_title):
                h_col1, h_col2 = st.columns([2, 1])
                with h_col1:
                    st.markdown(f"**Estado Comercial:** <span class='{st_class}'>{eff_status}</span> &nbsp;&nbsp; *(Actualizado: {q.status_updated_at[:19] if q.status_updated_at else q.date})*", unsafe_allow_html=True)
                    st.markdown(f"**Cliente:** {q.customer.name} | **Tel:** {q.customer.phone or 'N/A'}")
                    if q.customer.email:
                        st.markdown(f"**Email:** `{q.customer.email}`")
                    if q.customer.notes:
                        st.markdown(f"**Notas:** _{q.customer.notes}_")
                    st.markdown(f"**Ítems:** {len(q.items)} | **Válida hasta:** {q.valid_until}")
                    
                    for it in q.items:
                        st.caption(f"• {it.quantity}x [{it.product.name}]({it.product.url}) ({it.product.store_name}) = Q {it.subtotal:,.2f}")
                    
                    st.markdown(f"**Subtotal:** Q {q.items_subtotal:,.2f} | **Margen ({q.service_fee_percent}%):** Q {q.service_fee_amount:,.2f} | **Envíos:** Q {q.total_shipping:,.2f} | **Total:** **Q {q.total:,.2f}**")

                    # F6: seguimiento de venta (factura/entrega) para cotizaciones aceptadas
                    if eff_status == "ACEPTADA":
                        st.markdown("##### 📦 Seguimiento de Venta")
                        sale_notes = st.text_area(
                            "Factura / entrega",
                            value=q.sale_notes,
                            key=f"sale_notes_{q.quote_id}",
                            height=70,
                        )
                        if st.button("💾 Guardar notas de venta", key=f"save_sale_{q.quote_id}"):
                            q.sale_notes = sale_notes
                            history_mgr.save_quote(q, force_overwrite=True)
                            exporter.export_all(q, config.business)
                            st.toast("✔ Notas de venta guardadas", icon="📦")
                            st.rerun()

                with h_col2:
                    # Commercial status transition controls
                    allowed_transitions = q.get_allowed_transitions()
                    if allowed_transitions:
                        st.markdown("##### 🏷️ Cambiar Estado")
                        st_opts = [t.value for t in allowed_transitions]
                        new_st_choice = st.selectbox("Nuevo estado", st_opts, key=f"sel_st_{q.quote_id}", label_visibility="collapsed")
                        if st.button("✔ Aplicar Estado", key=f"btn_st_{q.quote_id}", use_container_width=True):
                            try:
                                up_q = history_mgr.update_quote_status(q.quote_id, new_st_choice)
                                exporter.export_all(up_q, config.business)
                                st.toast(f"✔ Estado de {q.quote_id} actualizado a {new_st_choice}", icon="🏷️")
                                st.rerun()
                            except InvalidStatusTransitionError as e:
                                st.error(f"Error: {e}")

                    st.divider()

                    if st.button("📄 Duplicar Cotización", key=f"dup_{q.quote_id}", help="Duplica esta cotización como un nuevo presupuesto independiente", use_container_width=True):
                        dup = history_mgr.duplicate_quote(q.quote_id)
                        exporter.export_all(dup, config.business)
                        st.toast(f"✔ Cotización duplicada como {dup.quote_id}", icon="📄")
                        st.rerun()

                    if st.button("✏️ Cargar para Editar", key=f"load_edit_{q.quote_id}", use_container_width=True):
                        load_quote_for_editing(q)
                        st.toast(f"Cotización {q.quote_id} cargada en el panel de trabajo.", icon="✏️")
                        st.rerun()

                    reverify_key = f"reverify_preview_{q.quote_id}"
                    if st.button("🔄 Re-verificar Precios", key=f"reverify_{q.quote_id}", use_container_width=True):
                        with st.spinner(f"Consultando precios en vivo para {q.quote_id}..."):
                            try:
                                candidate_q, changes, diff = history_mgr.check_quote_price_updates(q.quote_id)
                                st.session_state[reverify_key] = {
                                    "candidate": candidate_q,
                                    "changes": changes,
                                    "diff": diff
                                }
                            except Exception as e:
                                st.error(f"Error: {e}")

                    st.divider()

                    # F2: eliminación definitiva con confirmación explícita
                    del_key = f"confirm_delete_{q.quote_id}"
                    if st.button("🗑️ Eliminar Cotización", key=f"del_{q.quote_id}", use_container_width=True):
                        st.session_state[del_key] = True
                    if st.session_state.get(del_key):
                        st.warning(f"⚠️ ¿Eliminar **definitivamente** `{q.quote_id}` (y sus versiones)? Esta acción no se puede deshacer.")
                        dc1, dc2 = st.columns(2)
                        with dc1:
                            if st.button("✔ Sí, eliminar", key=f"del_yes_{q.quote_id}", use_container_width=True):
                                removed = history_mgr.delete_quote(q.quote_id)
                                st.session_state.pop(del_key, None)
                                st.session_state.pop(reverify_key, None)
                                if removed:
                                    st.toast(f"🗑️ Cotización {q.quote_id} eliminada ({removed} registro(s))", icon="🗑️")
                                else:
                                    st.error("No se encontró la cotización en el historial.")
                                st.rerun()
                        with dc2:
                            if st.button("Cancelar", key=f"del_no_{q.quote_id}", use_container_width=True):
                                st.session_state.pop(del_key, None)
                                st.rerun()

                # Display Reverification Preview if active
                reverify_key = f"reverify_preview_{q.quote_id}"
                if reverify_key in st.session_state:
                    rev_data = st.session_state[reverify_key]
                    cand = rev_data["candidate"]
                    ch_list = rev_data["changes"]
                    d = rev_data["diff"]

                    st.markdown("---")
                    st.info(f"🔍 **Vista Previa de Revalidación:** Los cambios crearán la versión **`{cand.quote_id}` (v{cand.version})**. La versión `{q.quote_id}` permanecerá intacta.")

                    # Item price & stock changes
                    st.markdown("##### 🛒 Comparativa de Componentes")
                    for c in ch_list:
                        d_str = f"{c['price_diff']:+.2f}" if c['price_diff'] != 0 else "Sin cambio"
                        st_icon = "🟢" if c['new_in_stock'] else "🔴"
                        st.caption(f"• **{c['product_name']}** ({c['store']}): Q {c['old_price']:.2f} ➔ **Q {c['new_price']:.2f}** ({d_str}) | {st_icon} {c['stock_status']}")

                    # Totals comparison
                    st.markdown("##### 💰 Resumen Financiero")
                    r_c1, r_c2, r_c3, r_c4 = st.columns(4)
                    r_c1.metric("Subtotal Comp.", f"Q {d['new_items_subtotal']:,.2f}", f"{d['items_subtotal_diff']:+.2f}")
                    r_c2.metric("Margen Servicio", f"Q {d['new_service_fee']:,.2f}", f"{d['service_fee_diff']:+.2f}")
                    r_c3.metric("Total Envíos", f"Q {d['new_total_shipping']:,.2f}", f"{d['total_shipping_diff']:+.2f}")
                    r_c4.metric("TOTAL A PAGAR", f"Q {d['new_total']:,.2f}", f"{d['total_diff']:+.2f}")

                    act1, act2 = st.columns(2)
                    with act1:
                        if st.button(f"✅ Aceptar y Crear Versión v{cand.version}", key=f"btn_accept_{q.quote_id}", type="primary", use_container_width=True):
                            saved_v = history_mgr.save_reverified_version(cand)
                            exporter.export_all(saved_v, config.business)
                            del st.session_state[reverify_key]
                            st.success(f"✔ ¡Versión `{saved_v.quote_id}` guardada con éxito!")
                            st.rerun()
                    with act2:
                        if st.button("❌ Cancelar Revalidación", key=f"btn_cancel_{q.quote_id}", use_container_width=True):
                            del st.session_state[reverify_key]
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
        
        st.markdown("#### 🧠 Inteligencia Artificial Local (Ollama)")
        ollama_live = check_ollama_status(config.ollama_url)
        st_color = "🟢 Conectado y Listo" if ollama_live else "🔴 Ollama no detectado en localhost:11434"
        st.caption(f"Estado de Ollama: **{st_color}**")
        new_enable_ai = st.toggle("Habilitar Asistente IA Local", value=bool(config.enable_ai))
        new_ollama_url = st.text_input("URL del Servidor Ollama", value=config.ollama_url)
        new_ollama_model = st.text_input("Modelo de IA", value=config.ollama_model)

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
        config.enable_ai = new_enable_ai
        config.ollama_url = new_ollama_url
        config.ollama_model = new_ollama_model
        config.shipping_rules["La Electrónica"]["free_threshold"] = new_la_thresh
        config.shipping_rules["La Electrónica"]["default_cost"] = new_la_cost
        config.shipping_rules["Electrónica DIY"]["free_threshold"] = new_diy_thresh
        config.shipping_rules["Electrónica DIY"]["default_cost"] = new_diy_cost
        config.save()
        st.success("✔ ¡Configuración actualizada exitosamente!")
        st.rerun()
