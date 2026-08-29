import sys
import copy
import subprocess
from pathlib import Path
from typing import List, Dict, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt, FloatPrompt
from rich.text import Text
from rich import box

from src.models import (
    Product, QuoteItem, Quote, Customer, StoreShippingDetail,
    QuoteStatus, InvalidStatusTransitionError
)
from src.config import AppConfig
from src.stores import STORE_NAMES
from src.scrapers import scrape_product, metasearch, StoreNotSupportedError
from src.core.calculator import QuoteCalculator, format_currency
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter, ExportResult
from src.core.bom_parser import parse_bom_text, parse_bom_text_hybrid
from src.core.ai_service import suggest_alternatives_with_ai, check_ollama_status
from src.core.bom_searcher import (
    search_bom_items_parallel,
    calculate_match_score,
    build_all_bom_scenarios
)
from src.services.quote_flow import QuoteFlowService

console = Console()

def get_status_style(status_str: str) -> str:
    s = status_str.upper()
    if s == "ACEPTADA":
        return "bold green"
    elif s == "ENVIADA":
        return "bold magenta"
    elif s == "GUARDADA":
        return "bold cyan"
    elif s == "RECHAZADA":
        return "bold red"
    elif s == "VENCIDA":
        return "dim white"
    elif s == "BORRADOR":
        return "bold yellow"
    return "white"

class CotizadorCLI:
    def __init__(self):
        self.config = AppConfig.load()
        self.history_mgr = HistoryManager()
        self.exporter = QuoteExporter()

    def show_banner(self):
        banner_text = Text()
        banner_text.append("⚡ COTIZADOR DE COMPONENTES ELECTRÓNICOS ⚡\n", style="bold cyan")
        banner_text.append("Guatemala • La Electrónica | Electrónica DIY | Electrónica RyCH\n", style="dim white")
        banner_text.append(f"Margen: {self.config.service_fee_percent}% • Moneda: {self.config.currency_code} ({self.config.currency_symbol})", style="bold yellow")
        
        console.print(Panel(banner_text, box=box.ROUNDED, expand=False, border_style="cyan"))

    def run(self):
        while True:
            console.clear()
            self.show_banner()
            console.print("\n[bold green]MENÚ PRINCIPAL[/bold green]")
            console.print("  [bold cyan]1.[/bold cyan] 📋 Crear Cotización por Lista Rápida (BOM Multilínea - 4 Opciones)")
            console.print("  [bold cyan]2.[/bold cyan] ➕ Crear Cotización Manual (Ítem por Ítem)")
            console.print("  [bold cyan]3.[/bold cyan] ✏️  Editar Cotización Guardada (Nueva Versión)")
            console.print("  [bold cyan]4.[/bold cyan] 📄 Duplicar Cotización (Nueva Independiente)")
            console.print("  [bold cyan]5.[/bold cyan] 🔍 Buscar y Ver Historial de Cotizaciones")
            console.print("  [bold cyan]6.[/bold cyan] 🔄 Re-verificar Precios de una Cotización")
            console.print("  [bold cyan]7.[/bold cyan] 📊 Ver Métricas Comerciales y Analítica")
            console.print("  [bold cyan]8.[/bold cyan] ⚙️  Configuración (Margen, Envíos, Negocio)")
            console.print("  [bold cyan]9.[/bold cyan] 🗑️  Eliminar Cotización (Definitivo)")
            console.print("  [bold cyan]10.[/bold cyan] 🚪 Salir")

            choice = Prompt.ask("\nSelecciona una opción", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"], default="1")

            if choice == "1":
                self.crear_cotizacion_bom()
            elif choice == "2":
                self.crear_nueva_cotizacion()
            elif choice == "3":
                self.editar_cotizacion()
            elif choice == "4":
                self.duplicar_cotizacion()
            elif choice == "5":
                self.ver_historial()
            elif choice == "6":
                self.reverificar_cotizacion()
            elif choice == "7":
                self.mostrar_metricas_comerciales()
            elif choice == "8":
                self.configuracion_menu()
            elif choice == "9":
                self.eliminar_cotizacion()
            elif choice == "10":
                console.print("\n[bold green]¡Hasta pronto![/bold green] 👋\n")
                sys.exit(0)

            Prompt.ask("\n[dim]Presiona Enter para continuar...[/dim]")

    def _pedir_datos_cliente(self, default_customer: Optional[Customer] = None) -> Customer:
        """Prompts for customer name, phone, email, and notes with format validation."""
        console.print("\n[bold cyan]👤 DATOS DEL CLIENTE[/bold cyan]")
        
        default_name = default_customer.name if default_customer else "Cliente General"
        default_phone = default_customer.phone if default_customer else ""
        default_email = default_customer.email if default_customer else ""
        default_notes = default_customer.notes if default_customer else ""

        if default_customer is None:
            frequent_customers = self.history_mgr.get_frequent_customers(limit=5)
            if frequent_customers:
                console.print("[dim]Clientes frecuentes sugeridos:[/dim]")
                for idx, fc in enumerate(frequent_customers, 1):
                    console.print(f"  [bold cyan]{idx}.[/bold cyan] {fc['name']} (Tel: {fc['phone'] or 'N/A'}, {fc['count']} cotizaciones)")
                console.print("  [bold cyan]0.[/bold cyan] Ingresar cliente nuevo")
                fc_choice = Prompt.ask("Selecciona un cliente frecuente o 0 para nuevo", default="0")
                if fc_choice.isdigit() and 1 <= int(fc_choice) <= len(frequent_customers):
                    chosen = frequent_customers[int(fc_choice) - 1]
                    default_name = chosen["name"]
                    default_phone = chosen["phone"]
                    default_email = chosen["email"]
                    default_notes = chosen["notes"]

        client_name = Prompt.ask("Nombre del cliente", default=default_name).strip() or "Cliente General"
        client_phone = Prompt.ask("Teléfono / WhatsApp (opcional)", default=default_phone).strip()
        
        while True:
            client_email = Prompt.ask("Correo Electrónico (opcional)", default=default_email).strip()
            temp_customer = Customer(name=client_name, phone=client_phone, email=client_email, notes="")
            errors = temp_customer.validate()
            if errors:
                for err in errors:
                    console.print(f"[bold red]❌ {err}[/bold red]")
                if not Confirm.ask("¿Deseas corregir el correo electrónico?", default=True):
                    client_email = ""
                    break
            else:
                break

        client_notes = Prompt.ask("Notas / Observaciones del cliente (opcional)", default=default_notes).strip()

        return Customer(
            name=client_name,
            phone=client_phone,
            email=client_email,
            notes=client_notes
        )

    def _solicitar_envios_interactivo(self, items: List[QuoteItem], existing_custom_costs: Optional[Dict[str, float]] = None) -> List[StoreShippingDetail]:
        """Evaluates stores in the quote and interactively asks for shipping costs if needed."""
        store_subtotals = QuoteCalculator.calculate_store_subtotals(items)
        shipping_rules = self.config.shipping_rules
        custom_costs = existing_custom_costs.copy() if existing_custom_costs else {}

        console.print("\n[bold cyan]=== EVALUACIÓN DE COSTOS DE ENVÍO POR TIENDA ===[/bold cyan]")

        for store_name, subtotal in store_subtotals.items():
            rule = shipping_rules.get(store_name, {})
            is_pickup = rule.get("is_pickup_only", False)
            threshold = rule.get("free_threshold")
            default_cost = float(rule.get("default_cost", 35.0))

            if is_pickup or threshold is None:
                console.print(f"🏪 [bold]{store_name}:[/bold] Subtotal {format_currency(subtotal, self.config.currency_symbol)} → [green]✔ No aplica costo de envío (Retiro en tienda)[/green]")
                custom_costs[store_name] = 0.0
            elif subtotal >= threshold:
                console.print(f"🏪 [bold]{store_name}:[/bold] Subtotal {format_currency(subtotal, self.config.currency_symbol)} → [bold green]✔ ¡Envío Gratis alcanzado! (Mínimo Q{threshold:,.0f})[/bold green]")
                custom_costs[store_name] = 0.0
            else:
                current_suggested = custom_costs.get(store_name, default_cost)
                console.print(f"🏪 [bold]{store_name}:[/bold] Subtotal {format_currency(subtotal, self.config.currency_symbol)} ([yellow]No alcanza el mínimo de Q{threshold:,.0f} para envío gratis[/yellow])")
                cost = FloatPrompt.ask(
                    f"   Ingresa el costo de envío para {store_name}",
                    default=float(current_suggested)
                )
                custom_costs[store_name] = round(cost, 2)

        return QuoteCalculator.evaluate_shipping_details(store_subtotals, shipping_rules, custom_costs)

    def _ingresar_producto_manual(self, default_name: str = "", default_url: str = "", default_store: str = "Electrónica RyCH") -> Optional[Product]:
        """Allows user to manually input component details when scraping fails or store is offline."""
        console.print("\n[bold cyan]✍️ INGRESO MANUAL DE COMPONENTE[/bold cyan]")
        console.print("[dim]Ingresa los datos manualmente para continuar la cotización sin bloqueos.[/dim]\n")

        name = Prompt.ask("Nombre / Descripción del componente", default=default_name).strip()
        if not name:
            console.print("[red]El nombre del componente es obligatorio.[/red]")
            return None

        console.print("\n[cyan]Selecciona la tienda de origen:[/cyan]")
        store_map = {}
        for i, sname in enumerate(STORE_NAMES, 1):
            store_map[str(i)] = sname
            console.print(f"  [{i}] {sname}")
        console.print(f"  [{len(STORE_NAMES) + 1}] Otra Tienda / Proveedor")
        store_choices = list(store_map.keys()) + [str(len(STORE_NAMES) + 1)]
        store_opt = Prompt.ask("Opción de tienda", choices=store_choices, default="1")
        if store_opt in store_map:
            store_name = store_map[store_opt]
        else:
            store_name = Prompt.ask("Nombre de la tienda/proveedor", default="Proveedor Local").strip() or "Proveedor Local"

        url = Prompt.ask("URL de referencia (opcional, Enter para omitir)", default=default_url).strip()
        sku = Prompt.ask("SKU / Código de producto (opcional, Enter para omitir)", default="").strip() or None

        while True:
            price = FloatPrompt.ask("Precio unitario en Quetzales (Q)")
            if price > 0:
                break
            console.print("[red]El precio debe ser un número positivo mayor a 0.[/red]")

        console.print("\n[cyan]Disponibilidad / Estado de Stock:[/cyan]")
        console.print("  [1] Disponible (En stock)")
        console.print("  [2] Agotado / Sobre pedido")
        stk_choice = Prompt.ask("Selecciona estado", choices=["1", "2"], default="1")
        in_stock = (stk_choice == "1")
        stock_status = "Disponible" if in_stock else "Agotado"

        return Product(
            name=name,
            url=url,
            store_name=store_name,
            unit_price=round(price, 2),
            in_stock=in_stock,
            stock_status=stock_status,
            sku=sku,
            is_manual=True
        )

    def _mostrar_historial_precio(self, product: Product):
        """F4: muestra el último precio cotizado para este producto (URL o SKU) si existe."""
        try:
            hist = self.history_mgr.get_price_history(url=product.url, sku=product.sku, limit=1)
            if hist:
                h = hist[0]
                console.print(
                    f"[dim]🕓 Última vez cotizado: {format_currency(h['unit_price'], self.config.currency_symbol)} "
                    f"({h['date']}, {h['quote_id']})[/dim]"
                )
        except Exception:
            pass

    def _obtener_producto_interactivo(self) -> Optional[Product]:
        """Allows user to search by name in the 3 stores, paste a direct URL, or enter manually."""
        console.print("\n[bold yellow]¿Cómo deseas agregar el componente?[/bold yellow]")
        console.print("  [bold cyan]1.[/bold cyan] 🔍 Buscar por nombre / valor en las 3 tiendas (Metabuscador)")
        console.print("  [bold cyan]2.[/bold cyan] 🔗 Pegar URL directa")
        console.print("  [bold cyan]3.[/bold cyan] ✍️  Ingreso Manual (si la tienda falló o no está en línea)")
        console.print("  [bold cyan]4.[/bold cyan] ↩️  Cancelar")

        modo = Prompt.ask("Selecciona método", choices=["1", "2", "3", "4"], default="1")

        if modo == "1":
            while True:
                query = Prompt.ask("\n🔍 Ingresa término de búsqueda (ej. 'ESP32', 'resistencia 220', 'sensor ultrasónico')").strip()
                if not query:
                    return None

                try:
                    with console.status(f"[bold green]Buscando '{query}' en RyCH, La Electrónica y DIY en paralelo...[/bold green]", spinner="dots"):
                        results = metasearch(query, max_per_store=5)
                except Exception as e:
                    console.print(f"[bold red]❌ Error de conexión con las tiendas:[/bold red] {e}")
                    if Confirm.ask("¿Deseas ingresar este producto de forma manual?", default=True):
                        return self._ingresar_producto_manual(default_name=query)
                    return None

                # Filter valid prices
                results = [r for r in results if r.unit_price > 0]

                if not results:
                    console.print(f"[yellow]No se encontraron resultados con precio válido para '{query}'.[/yellow]")
                    
                    if self.config.enable_ai and check_ollama_status(self.config.ollama_url):
                        if Confirm.ask("¿Deseas pedirle a la IA Local (Qwen 2.5) sugerencias de reemplazos o equivalentes compatibles?", default=True):
                            with console.status(f"[bold magenta]Consultando sugerencias de reemplazo para '{query}' a la IA Local...[/bold magenta]", spinner="dots"):
                                alts = suggest_alternatives_with_ai(query, host=self.config.ollama_url, model=self.config.ollama_model)
                            
                            if alts:
                                alt_table = Table(title=f"💡 Sugerencias de Reemplazo por IA para '{query}'", box=box.ROUNDED)
                                alt_table.add_column("#", justify="center", style="bold magenta")
                                alt_table.add_column("Componente Sugerido", style="bold white")
                                alt_table.add_column("Compatibilidad", style="cyan")
                                alt_table.add_column("Justificación Técnica", style="dim")
                                for a_i, alt in enumerate(alts, 1):
                                    alt_table.add_row(str(a_i), alt["nombre"], alt["compatibilidad"], alt["explicacion"])
                                console.print(alt_table)
                                
                                console.print("  [bold cyan][#][/bold cyan] Selecciona número para buscar esa alternativa en las tiendas")
                                console.print("  [bold cyan][0][/bold cyan] No buscar ninguna de las sugerencias")
                                alt_sel = IntPrompt.ask(f"Opción (0 a {len(alts)})", default=1)
                                if 1 <= alt_sel <= len(alts):
                                    chosen_alt = alts[alt_sel - 1]["nombre"]
                                    console.print(f"[green]Buscando alternativa sugerida: '{chosen_alt}'...[/green]")
                                    query = chosen_alt
                                    continue

                    if Confirm.ask("¿Deseas ingresar este producto manualmente?", default=False):
                        return self._ingresar_producto_manual(default_name=query)
                    if not Confirm.ask("¿Deseas buscar con otro término?", default=True):
                        return None
                    continue

                table = Table(title=f"Resultados para '{query}' en las 3 tiendas", box=box.ROUNDED)
                table.add_column("#", justify="center", style="bold cyan", no_wrap=True)
                table.add_column("Tienda", style="dim")
                table.add_column("Componente / Descripción", style="white")
                table.add_column("Precio", justify="right", style="bold green")
                table.add_column("Stock", justify="center")

                for i, r in enumerate(results, 1):
                    stock_style = "green" if r.in_stock else "red"
                    table.add_row(
                        str(i),
                        r.store_name,
                        r.title[:48] + ("..." if len(r.title) > 48 else ""),
                        format_currency(r.unit_price, self.config.currency_symbol),
                        f"[{stock_style}]{r.stock_status}[/{stock_style}]"
                    )

                console.print(table)

                sel = IntPrompt.ask(f"\nSelecciona el # del componente a agregar (1 a {len(results)}, o 0 para buscar otro término)", default=1)
                if sel == 0:
                    if not Confirm.ask("¿Deseas intentar otra búsqueda?", default=True):
                        return None
                    continue

                if 1 <= sel <= len(results):
                    chosen = results[sel - 1]
                    with console.status(f"[bold green]Cargando detalles de {chosen.title}...[/bold green]"):
                        try:
                            prod = scrape_product(chosen.url)
                            self._mostrar_historial_precio(prod)
                            return prod
                        except Exception as e:
                            console.print(f"[yellow]⚠️ No se pudo extraer la ficha técnica en vivo ({e}). Usando datos del buscador.[/yellow]")
                            fallback = Product(
                                name=chosen.title,
                                url=chosen.url,
                                store_name=chosen.store_name,
                                unit_price=chosen.unit_price,
                                in_stock=chosen.in_stock,
                                stock_status=chosen.stock_status,
                                image_url=chosen.image_url
                            )
                            self._mostrar_historial_precio(fallback)
                            return fallback
                else:
                    console.print("[red]Selección fuera de rango.[/red]")

        elif modo == "2":
            url = Prompt.ask("\nPega la URL del producto").strip()
            if not url:
                return None
            with console.status("[bold green]Extrayendo datos de la tienda...[/bold green]", spinner="dots"):
                try:
                    product = scrape_product(url)
                    if product.unit_price <= 0:
                        console.print("[bold red]❌ El producto no tiene un precio válido en la tienda.[/bold red]")
                        if Confirm.ask("¿Deseas ingresar los datos manualmente?", default=True):
                            return self._ingresar_producto_manual(default_url=url)
                        return None
                    self._mostrar_historial_precio(product)
                    return product
                except StoreNotSupportedError as e:
                    console.print(f"[bold red]❌ Error de Tienda:[/bold red] {e}")
                    if Confirm.ask("¿Deseas ingresar este producto de forma manual?", default=True):
                        return self._ingresar_producto_manual(default_url=url)
                    return None
                except Exception as e:
                    console.print(f"[bold red]❌ Error al extraer producto de la tienda:[/bold red] {e}")
                    if Confirm.ask("¿Deseas ingresar este producto manualmente para no detener la cotización?", default=True):
                        return self._ingresar_producto_manual(default_url=url)
                    return None

        elif modo == "3":
            return self._ingresar_producto_manual()

        return None

    def _mostrar_panel_documentos(self, exp_res: ExportResult, quote_id: str):
        """Displays a clean summary panel with all generated files (Cliente and Interna)."""
        console.print(Panel(
            f"[bold green]✔ Cotización guardada con éxito![/bold green]\n\n"
            f"📄 [bold]ID Cotización:[/bold] {quote_id}\n\n"
            f"[bold cyan]Archivos para el Cliente:[/bold cyan]\n"
            f"  📑 [bold]PDF Cliente:[/bold]  {exp_res.client_pdf if exp_res.client_pdf else 'No generado'}\n"
            f"  🌐 [bold]HTML Cliente:[/bold] {exp_res.client_html}\n\n"
            f"[bold yellow]Archivos de Control Interno (con Enlaces de Compra y Estado Comercial):[/bold yellow]\n"
            f"  🔗 [bold]PDF Interno:[/bold]  {exp_res.internal_pdf if exp_res.internal_pdf else 'No generado'}\n"
            f"  🌐 [bold]HTML Interno:[/bold] {exp_res.internal_html}\n"
            f"  📊 [bold]CSV Registro:[/bold] {exp_res.csv}",
            title="[bold cyan]Documentos Generados (Versión Dual)[/bold cyan]",
            border_style="green"
        ))

        if exp_res.client_pdf or exp_res.internal_pdf:
            console.print("\n[bold]¿Deseas abrir algún documento PDF ahora?[/bold]")
            console.print("  [1] 📑 Abrir PDF para Cliente (Limpio)")
            console.print("  [2] 🔗 Abrir PDF Interno (Con enlaces de compra directos)")
            console.print("  [3] ↩️  No abrir")
            abrir_opc = Prompt.ask("Selecciona opción", choices=["1", "2", "3"], default="1")
            
            target_pdf = None
            if abrir_opc == "1" and exp_res.client_pdf:
                target_pdf = exp_res.client_pdf
            elif abrir_opc == "2" and exp_res.internal_pdf:
                target_pdf = exp_res.internal_pdf

            if target_pdf:
                try:
                    subprocess.Popen(["xdg-open", str(target_pdf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

    def crear_cotizacion_bom(self):
        """Enhanced BOM flow: all candidates per line, score & confidence, manual selection, REVISAR confirmation, unfound list, and optimal mixed scenario."""
        console.print("\n[bold cyan]=== COTIZADOR POR LISTA RÁPIDA (BOM MULTILÍNEA) ===[/bold cyan]")
        
        customer = self._pedir_datos_cliente()

        console.print("\n[dim]Pega tu lista de componentes (ej. '2x ESP32 NodeMCU', '10x Resistencia 220 ohm 1/4W').[/dim]")
        console.print("[yellow]Al terminar de pegar, presiona Enter en una línea vacía:[/yellow]\n")

        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if not line.strip():
                if lines:
                    break
                else:
                    continue
            lines.append(line)

        if not lines:
            console.print("[yellow]No se ingresó ninguna línea de texto. Cancelando.[/yellow]")
            return

        raw_text = "\n".join(lines)
        
        # Check if local Ollama AI is enabled & running
        if self.config.enable_ai and check_ollama_status(self.config.ollama_url):
            if Confirm.ask(f"\n¿Deseas interpretar el texto con IA Local (Ollama: {self.config.ollama_model})? (Recomendado para mensajes de WhatsApp o notas libres)", default=True):
                with console.status(f"[bold magenta]Extrayendo componentes con IA Local ({self.config.ollama_model})...[/bold magenta]", spinner="dots"):
                    parse_res = parse_bom_text_hybrid(raw_text, config=self.config, force_ai=True)
            else:
                parse_res = parse_bom_text(raw_text)
        else:
            parse_res = parse_bom_text(raw_text)

        if not parse_res.items:
            console.print("[bold red]❌ No se pudo interpretar ningún componente del texto ingresado.[/bold red]")
            return

        if parse_res.invalid_lines:
            console.print(f"[yellow]⚠️ Se ignoraron {len(parse_res.invalid_lines)} líneas no interpretables.[/yellow]")

        src_label = " [magenta](Extracción IA Local Qwen 2.5)[/magenta]" if getattr(parse_res, "source", "") == "ai_ollama" else ""
        console.print(f"\n[bold green]✔ Se interpretaron {parse_res.total_items} componentes (Total: {parse_res.total_quantity} unidades).[/bold green]{src_label}")
        
        margin = self.config.service_fee_percent
        if Confirm.ask(f"\n¿Deseas usar el margen de compra predeterminado de {margin}%?", default=True):
            fee_percent = margin
        else:
            fee_percent = FloatPrompt.ask("Ingresa el porcentaje de margen deseado (%)", default=margin)

        with console.status(f"[bold green]Consultando las 3 tiendas en paralelo para los {parse_res.total_items} componentes...[/bold green]", spinner="dots"):
            match_results = search_bom_items_parallel(parse_res.items, max_workers=5)

        # ----------------------------------------------------
        # 1. Mostrar resumen línea por línea con candidatos
        # ----------------------------------------------------
        while True:
            console.clear()
            self.show_banner()
            console.print("\n[bold cyan]=== RESULTADOS DE BÚSQUEDA Y ASIGNACIÓN DE CANDIDATOS ===[/bold cyan]")
            console.print(f"[dim]Cliente: {customer.name} | Ítems en lista: {len(match_results)}[/dim]\n")

            summary_table = Table(box=box.ROUNDED)
            summary_table.add_column("#", justify="center", style="bold cyan", no_wrap=True)
            summary_table.add_column("Solicitado en BOM", style="white")
            summary_table.add_column("Candidato Seleccionado", style="dim")
            summary_table.add_column("Tienda", style="cyan")
            summary_table.add_column("P. Unitario", justify="right", style="green")
            summary_table.add_column("Confianza / Score", justify="center")

            unfound_count = 0

            for i, m in enumerate(match_results, 1):
                if m.selected_candidate:
                    c = m.selected_candidate
                    conf_badge = m.status_badge
                    if m.is_confirmed and m.status == "REVISAR":
                        conf_badge += " [green](✔ Confirmado)[/green]"
                    
                    summary_table.add_row(
                        str(i),
                        f"{m.bom_item.quantity}x {m.bom_item.product_query}",
                        c.title[:38] + ("..." if len(c.title) > 38 else ""),
                        c.store_name,
                        format_currency(c.unit_price, self.config.currency_symbol),
                        conf_badge
                    )
                else:
                    unfound_count += 1
                    summary_table.add_row(
                        str(i),
                        f"{m.bom_item.quantity}x {m.bom_item.product_query}",
                        "[red]Ningún candidato válido encontrado[/red]",
                        "-",
                        "-",
                        "[bold red]❌ No encontrado[/bold red]"
                    )

            console.print(summary_table)

            # Highlight unfound lines if any
            if unfound_count > 0:
                console.print(f"\n[bold yellow]⚠️ Componentes no encontrados / no disponibles ({unfound_count}):[/bold yellow]")
                for idx, m in enumerate(match_results, 1):
                    if not m.selected_candidate:
                        console.print(f"  • Línea #{idx}: [red]{m.bom_item.quantity}x {m.bom_item.product_query}[/red]")

            # Highlight REVISAR lines requiring confirmation
            pending_reviews = [
                (idx, m) for idx, m in enumerate(match_results, 1)
                if m.requires_review_confirmation
            ]
            if pending_reviews:
                console.print(f"\n[bold red]⚠️ Coincidencias con confianza REVISAR que requieren confirmación ({len(pending_reviews)}):[/bold red]")
                for idx, m in pending_reviews:
                    c = m.selected_candidate
                    console.print(f"  • Línea #{idx} '{m.bom_item.product_query}' ➔ Asignado: '{c.title}' ({c.store_name} - Q{c.unit_price:.2f}) [Score: {int(m.confidence_score*100)}%]")

            console.print("\n[bold cyan]Acciones Disponibles:[/bold cyan]")
            console.print("  [bold green][C][/bold green] 🚀 [bold green]Continuar y generar las 4 opciones de cotización[/bold green]")
            console.print("  [bold yellow][#][/bold yellow] Ver todos los candidatos / cambiar candidato de una línea (ej. escribe '1', '2'...)")
            if self.config.enable_ai and check_ollama_status(self.config.ollama_url):
                console.print("  [bold magenta][A][/bold magenta] 💡 [bold magenta]Sugerir reemplazos / equivalentes con IA Local (Qwen 2.5)[/bold magenta]")
            console.print("  [bold red][X][/bold red] Cancelar cotización")

            user_action = Prompt.ask("\nSelecciona una acción", default="C").strip().lower()

            if user_action == "x":
                console.print("[yellow]Cotización cancelada.[/yellow]")
                return

            elif user_action == "c":
                # Validate if any unconfirmed REVISAR exists
                if pending_reviews:
                    console.print("\n[bold yellow]Hay coincidencias clasificadas como REVISAR pendientes de confirmación.[/bold yellow]")
                    for idx, m in pending_reviews:
                        c = m.selected_candidate
                        if Confirm.ask(f"¿Confirmas que '{c.title}' ({c.store_name}) es el componente correcto para '{m.bom_item.product_query}'?", default=True):
                            m.is_confirmed = True
                        else:
                            m.selected_candidate = None
                            m.status = "NO_ENCONTRADO"
                    continue
                break

            elif user_action == "a" and self.config.enable_ai and check_ollama_status(self.config.ollama_url):
                line_to_alt = IntPrompt.ask(f"Ingresa el número de línea para sugerir reemplazos (1 a {len(match_results)})", default=1)
                if 1 <= line_to_alt <= len(match_results):
                    target_m = match_results[line_to_alt - 1]
                    target_name = target_m.bom_item.product_query
                    with console.status(f"[bold magenta]Consultando a la IA Local alternativas para '{target_name}'...[/bold magenta]", spinner="dots"):
                        alts = suggest_alternatives_with_ai(target_name, host=self.config.ollama_url, model=self.config.ollama_model)
                    
                    if alts:
                        alt_table = Table(title=f"💡 Sugerencias de Reemplazo por IA para Línea #{line_to_alt} ('{target_name}')", box=box.ROUNDED)
                        alt_table.add_column("#", justify="center", style="bold magenta")
                        alt_table.add_column("Componente Sugerido", style="bold white")
                        alt_table.add_column("Compatibilidad", style="cyan")
                        alt_table.add_column("Justificación Técnica", style="dim")
                        for a_i, alt in enumerate(alts, 1):
                            alt_table.add_row(str(a_i), alt["nombre"], alt["compatibilidad"], alt["explicacion"])
                        console.print(alt_table)

                        if Confirm.ask("¿Deseas buscar alguna de estas sugerencias en las tiendas para esta línea?", default=True):
                            alt_idx = IntPrompt.ask(f"Selecciona sugerencia (1 a {len(alts)})", default=1)
                            if 1 <= alt_idx <= len(alts):
                                chosen_alt_name = alts[alt_idx - 1]["nombre"]
                                console.print(f"[green]Buscando en tiendas para: '{chosen_alt_name}'...[/green]")
                                try:
                                    with console.status(f"Consultando tiendas para '{chosen_alt_name}'...", spinner="dots"):
                                        alt_cands = metasearch(chosen_alt_name, max_per_store=5)
                                    alt_cands = [c for c in alt_cands if c.unit_price > 0]
                                    if alt_cands:
                                        scored = []
                                        for ac in alt_cands:
                                            sc = calculate_match_score(chosen_alt_name, ac.title, ac.in_stock)
                                            if sc >= 0.15:
                                                scored.append((ac, sc))
                                        scored.sort(key=lambda x: (x[1], x[0].in_stock, -x[0].unit_price), reverse=True)
                                        target_m.all_candidates = scored
                                        if scored:
                                            target_m.selected_candidate = scored[0][0]
                                            target_m.confidence_score = scored[0][1]
                                            target_m.status = "ALTA" if scored[0][1] >= 0.70 and scored[0][0].in_stock else ("MEDIA" if scored[0][1] >= 0.50 else "REVISAR")
                                            target_m.is_confirmed = True
                                            console.print(f"[bold green]✔ Alternativa asignada a Línea #{line_to_alt}:[/bold green] {scored[0][0].title} ({scored[0][0].store_name})")
                                    else:
                                        console.print(f"[yellow]No se encontraron productos en stock para '{chosen_alt_name}'.[/yellow]")
                                except Exception as e:
                                    console.print(f"[red]Error al buscar alternativa:[/red] {e}")
                    else:
                        console.print("[yellow]La IA no pudo generar sugerencias para este componente.[/yellow]")
                    Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif user_action.isdigit():
                line_num = int(user_action)
                if 1 <= line_num <= len(match_results):
                    m = match_results[line_num - 1]
                    console.print(f"\n[bold cyan]Candidatos encontrados para Línea #{line_num}:[/bold cyan] [bold white]{m.bom_item.quantity}x {m.bom_item.product_query}[/bold white]")
                    
                    if not m.all_candidates:
                        console.print("[yellow]No se encontraron candidatos en ninguna de las 3 tiendas para esta línea.[/yellow]")
                        Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")
                        continue

                    c_table = Table(box=box.ROUNDED)
                    c_table.add_column("#", justify="center", style="bold cyan")
                    c_table.add_column("Tienda", style="cyan")
                    c_table.add_column("Título / Descripción del Producto", style="white")
                    c_table.add_column("Precio Unit.", justify="right", style="green")
                    c_table.add_column("Stock", justify="center")
                    c_table.add_column("Score", justify="center")
                    c_table.add_column("Nivel Confianza", justify="center")

                    for c_idx, (cand, score) in enumerate(m.all_candidates, 1):
                        st_icon = "[green]Disponible[/green]" if cand.in_stock else "[red]Agotado[/red]"
                        if score >= 0.70:
                            lvl = "[green]ALTA[/green]"
                        elif score >= 0.50:
                            lvl = "[yellow]MEDIA[/yellow]"
                        else:
                            lvl = "[red]REVISAR[/red]"

                        c_table.add_row(
                            str(c_idx),
                            cand.store_name,
                            cand.title,
                            format_currency(cand.unit_price, self.config.currency_symbol),
                            st_icon,
                            f"{int(score * 100)}%",
                            lvl
                        )

                    console.print(c_table)
                    console.print("  [bold cyan][0][/bold cyan] Descartar / Dejar como no encontrado")

                    sel_c = IntPrompt.ask(f"Selecciona candidato (1 a {len(m.all_candidates)}, o 0 para descartar)", default=1)
                    if 1 <= sel_c <= len(m.all_candidates):
                        chosen_cand, chosen_score = m.all_candidates[sel_c - 1]
                        m.selected_candidate = chosen_cand
                        m.confidence_score = chosen_score
                        if chosen_score >= 0.70 and chosen_cand.in_stock:
                            m.status = "ALTA"
                        elif chosen_score >= 0.50:
                            m.status = "MEDIA"
                        else:
                            m.status = "REVISAR"
                        m.is_confirmed = True
                        console.print(f"[bold green]✔ Asignado:[/bold green] {chosen_cand.title} ({chosen_cand.store_name})")
                    elif sel_c == 0:
                        m.selected_candidate = None
                        m.status = "NO_ENCONTRADO"
                        m.is_confirmed = True
                        console.print("[yellow]Línea marcada como no encontrada.[/yellow]")
                    Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")
                else:
                    console.print("[red]Número de línea fuera de rango.[/red]")
                    Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

        # ----------------------------------------------------
        # 2. Construir los 4 escenarios (con Mixto Óptimo)
        # ----------------------------------------------------
        with console.status("[bold green]Calculando el escenario mixto óptimo y las 3 tiendas...[/bold green]"):
            scenarios = build_all_bom_scenarios(
                match_results=match_results,
                customer=customer,
                config=self.config,
                service_fee_percent=fee_percent
            )

        # ----------------------------------------------------
        # 3. Menú interactivo de selección y exportación
        # ----------------------------------------------------
        while True:
            console.clear()
            self.show_banner()
            console.print("\n[bold cyan]=== COMPARATIVA DE LAS 4 OPCIONES DE COTIZACIÓN ===[/bold cyan]")
            console.print(f"[dim]Cliente: {customer.name} | Tel: {customer.phone or 'N/A'} | Ítems en lista: {len(parse_res.items)}[/dim]\n")

            comp_table = Table(box=box.ROUNDED)
            comp_table.add_column("Opción", justify="center", style="bold cyan", no_wrap=True)
            comp_table.add_column("Escenario de Cotización", style="white")
            comp_table.add_column("Disponibilidad / Cobertura", justify="center")
            comp_table.add_column("Subtotal Comp.", justify="right", style="dim")
            comp_table.add_column("Total Envíos", justify="right", style="dim")
            comp_table.add_column(f"Margen ({fee_percent}%)", justify="right", style="dim")
            comp_table.add_column("TOTAL A PAGAR", justify="right", style="bold green")

            for sc in scenarios:
                cov_style = "green" if sc.is_complete else "yellow"
                q = sc.quote
                comp_table.add_row(
                    f"[{sc.scenario_id}]",
                    f"[bold]{sc.title}[/bold]",
                    f"[{cov_style}]{sc.coverage_label}[/{cov_style}]",
                    format_currency(q.items_subtotal, q.currency_symbol),
                    format_currency(q.total_shipping, q.currency_symbol) if q.total_shipping > 0 else "[green]Q 0.00[/green]",
                    format_currency(q.service_fee_amount, q.currency_symbol),
                    f"[bold green]{format_currency(q.total, q.currency_symbol)}[/bold green]"
                )

            console.print(comp_table)
            console.print("\n[bold cyan]Acciones Disponibles:[/bold cyan]")
            console.print("  [bold green][1][/bold green] Exportar [bold]Opción 1 (Cotización Mixta Óptima - Mínimo Costo Total)[/bold]")
            console.print("  [bold green][2][/bold green] Exportar [bold]Opción 2 (Todo en Electrónica RyCH)[/bold]")
            console.print("  [bold green][3][/bold green] Exportar [bold]Opción 3 (Todo en La Electrónica)[/bold]")
            console.print("  [bold green][4][/bold green] Exportar [bold]Opción 4 (Todo en Electrónica DIY)[/bold]")
            console.print("  [bold yellow][V1 - V4][/bold yellow] Ver el detalle desglosado de componentes de una opción (ej. 'v1' o 'v4')")
            console.print("  [bold red][C][/bold red] Cancelar cotización")

            user_choice = Prompt.ask("\nSelecciona opción", default="1").strip().lower()

            if user_choice == "c":
                console.print("[yellow]Cotización cancelada.[/yellow]")
                return

            if user_choice.startswith("v") and len(user_choice) == 2 and user_choice[1] in ["1", "2", "3", "4"]:
                v_idx = int(user_choice[1]) - 1
                selected_sc = scenarios[v_idx]
                console.print(f"\n[bold yellow]Detalle de componentes para: {selected_sc.title}[/bold yellow]")
                
                det_table = Table(box=box.SIMPLE_HEAD)
                det_table.add_column("#", justify="center", style="cyan")
                det_table.add_column("Solicitado en BOM", style="dim")
                det_table.add_column("Componente Asignado", style="white")
                det_table.add_column("Tienda", style="cyan")
                det_table.add_column("Cant.", justify="center", style="bold")
                det_table.add_column("Precio U.", justify="right", style="green")
                det_table.add_column("Subtotal", justify="right", style="bold green")

                for it_idx, item in enumerate(selected_sc.items, 1):
                    det_table.add_row(
                        str(it_idx),
                        match_results[it_idx-1].bom_item.product_query[:25] if it_idx <= len(match_results) else "-",
                        item.product.name[:38],
                        item.product.store_name,
                        str(item.quantity),
                        format_currency(item.unit_price, selected_sc.quote.currency_symbol),
                        format_currency(item.subtotal, selected_sc.quote.currency_symbol)
                    )

                console.print(det_table)

                if selected_sc.missing_queries:
                    console.print("\n[bold red]⚠️ Componentes no disponibles en esta opción:[/bold red]")
                    for mis in selected_sc.missing_queries:
                        console.print(f"  • [red]❌ {mis}[/red]")

                Prompt.ask("\n[dim]Presiona Enter para regresar a la comparativa...[/dim]")
                continue

            if user_choice in ["1", "2", "3", "4"]:
                sel_idx = int(user_choice) - 1
                chosen_scenario = scenarios[sel_idx]
                
                if not chosen_scenario.items:
                    console.print("[bold red]❌ Esta opción no tiene ningún componente disponible. Por favor selecciona otra opción.[/bold red]")
                    Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")
                    continue

                console.print(f"\n[bold green]Has seleccionado:[/bold green] [bold cyan]{chosen_scenario.title}[/bold cyan]")

                flow = QuoteFlowService(self.config, self.history_mgr, self.exporter)
                final_quote = flow.finalize_scenario_quote(
                    items=chosen_scenario.items,
                    customer=customer,
                    service_fee_percent=fee_percent,
                    shipping_details=chosen_scenario.quote.shipping_details,
                )

                self._mostrar_cotizacion_completa(final_quote)

                if Confirm.ask("\n¿Deseas guardar la cotización y generar los documentos (Cliente + Interno)?", default=True):
                    with console.status("[bold green]Generando archivos PDF, HTML y CSV...[/bold green]", spinner="dots"):
                        res = flow.save_and_export(final_quote)
                    if res.merged_count:
                        console.print(f"[yellow]🧮 Se fusionaron {res.merged_count} componente(s) repetido(s).[/yellow]")
                    self._mostrar_panel_documentos(res.export, res.quote.quote_id)
                break
            else:
                console.print("[red]Opción no válida. Ingresa 1, 2, 3, 4, o v1-v4 para ver el detalle.[/red]")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

    def crear_nueva_cotizacion(self):
        console.print("\n[bold cyan]=== NUEVA COTIZACIÓN MANUAL ===[/bold cyan]")
        
        customer = self._pedir_datos_cliente()
        items: List[QuoteItem] = []

        while True:
            product = self._obtener_producto_interactivo()
            if not product:
                if not items:
                    console.print("[yellow]No se agregó ningún componente. Cancelando cotización.[/yellow]")
                    return
                else:
                    if Confirm.ask("\n¿Deseas finalizar la cotización con los componentes actuales?", default=True):
                        break
                    else:
                        continue

            p_table = Table(box=box.SIMPLE, show_header=False)
            p_table.add_row("Producto:", f"[bold white]{product.name}[/bold white]")
            p_table.add_row("Tienda:", f"[cyan]{product.store_name}[/cyan]")
            p_table.add_row("Precio Unitario:", f"[bold green]{format_currency(product.unit_price, self.config.currency_symbol)}[/bold green]")
            
            stock_style = "green" if product.in_stock else "red"
            p_table.add_row("Disponibilidad:", f"[{stock_style}]{product.stock_status}[/{stock_style}]")

            console.print(Panel(p_table, title="[bold]Componente Seleccionado[/bold]", border_style="green"))

            if not product.in_stock:
                console.print("[bold yellow]⚠️ Advertencia: Este producto aparece sin stock o agotado en la tienda.[/bold yellow]")
                if not Confirm.ask("¿Deseas agregarlo a la cotización de todas formas?", default=False):
                    continue

            qty = IntPrompt.ask("Cantidad deseada", default=1)
            while qty <= 0:
                console.print("[red]La cantidad debe ser al menos 1.[/red]")
                qty = IntPrompt.ask("Cantidad deseada", default=1)

            quote_item = QuoteCalculator.create_quote_item(product, qty)
            items.append(quote_item)
            console.print(f"[green]✔ Agregado:[/green] {qty}x {product.name} = [bold]{format_currency(quote_item.subtotal, self.config.currency_symbol)}[/bold]")

            self._mostrar_resumen_items(items)

            if not Confirm.ask("\n¿Deseas agregar otro componente?", default=True):
                break

        if not items:
            console.print("[yellow]No se agregaron componentes. Cancelando cotización.[/yellow]")
            return

        margin = self.config.service_fee_percent
        if Confirm.ask(f"\n¿Deseas usar el margen de compra predeterminado de {margin}%?", default=True):
            fee_percent = margin
        else:
            fee_percent = FloatPrompt.ask("Ingresa el porcentaje de margen deseado (%)", default=margin)

        shipping_details = self._solicitar_envios_interactivo(items)

        flow = QuoteFlowService(self.config, self.history_mgr, self.exporter)
        quote = flow.finalize_scenario_quote(
            items=items,
            customer=customer,
            service_fee_percent=fee_percent,
            shipping_details=shipping_details,
        )

        self._mostrar_cotizacion_completa(quote)

        if Confirm.ask("\n¿Deseas guardar la cotización y generar los documentos (Cliente + Interno)?", default=True):
            with console.status("[bold green]Generando archivos PDF, HTML y CSV...[/bold green]", spinner="dots"):
                res = flow.save_and_export(quote)
            if res.merged_count:
                console.print(f"[yellow]🧮 Se fusionaron {res.merged_count} componente(s) repetido(s).[/yellow]")
            self._mostrar_panel_documentos(res.export, res.quote.quote_id)

    def duplicar_cotizacion(self):
        """Duplicates an existing quote as a new independent quote."""
        console.print("\n[bold cyan]=== DUPLICAR COTIZACIÓN (NUEVA INDEPENDIENTE) ===[/bold cyan]")
        quotes = self.history_mgr.load_all_quotes()
        if not quotes:
            console.print("[yellow]No hay cotizaciones guardadas aún para duplicar.[/yellow]")
            return

        table = Table(title="Cotizaciones Recientes", box=box.ROUNDED)
        table.add_column("ID", style="bold cyan")
        table.add_column("Estado", justify="center")
        table.add_column("Fecha", style="dim")
        table.add_column("Cliente", style="white")
        table.add_column("Ítems", justify="center")
        table.add_column("Total", justify="right", style="bold green")

        for q in quotes[-10:]:
            st_eff = self.history_mgr.effective_status(q)
            st_style = get_status_style(st_eff)
            table.add_row(
                q.quote_id,
                f"[{st_style}]{st_eff}[/{st_style}]",
                q.date,
                q.customer.name,
                str(len(q.items)),
                format_currency(q.total, q.currency_symbol)
            )
        console.print(table)

        qid = Prompt.ask("\nIngresa el ID de la cotización que deseas duplicar").strip()
        original_quote = self.history_mgr.get_quote(qid)
        if not original_quote:
            console.print(f"[bold red]No se encontró ninguna cotización con ID '{qid}'.[/bold red]")
            return

        console.print(f"\n[bold yellow]Cotización Base Seleccionada:[/bold yellow] {original_quote.quote_id} ({original_quote.customer.name}) con {len(original_quote.items)} componentes.")

        if Confirm.ask("¿Deseas asignar un cliente diferente a la nueva cotización duplicada?", default=False):
            new_customer = self._pedir_datos_cliente(default_customer=original_quote.customer)
        else:
            new_customer = copy.deepcopy(original_quote.customer)

        with console.status("[bold green]Duplicando cotización y calculando ID independiente...[/bold green]"):
            duplicated = self.history_mgr.duplicate_quote(original_quote.quote_id, new_customer=new_customer)
            exp_res = self.exporter.export_all(duplicated, self.config.business)

        console.print(f"\n[bold green]✔ ¡Cotización duplicada con éxito como {duplicated.quote_id}![/bold green]")
        console.print(f"[dim]La cotización original {original_quote.quote_id} permanece 100% intacta.[/dim]\n")
        self._mostrar_cotizacion_completa(duplicated)
        self._mostrar_panel_documentos(exp_res, duplicated.quote_id)

    def editar_cotizacion(self):
        console.print("\n[bold cyan]=== EDITAR COTIZACIÓN GUARDADA ===[/bold cyan]")
        quotes = self.history_mgr.load_all_quotes()
        if not quotes:
            console.print("[yellow]No hay cotizaciones guardadas aún para editar.[/yellow]")
            return

        table = Table(title="Cotizaciones Recientes", box=box.ROUNDED)
        table.add_column("ID", style="bold cyan")
        table.add_column("Estado", justify="center")
        table.add_column("Fecha", style="dim")
        table.add_column("Cliente", style="white")
        table.add_column("Ítems", justify="center")
        table.add_column("Total", justify="right", style="bold green")

        for q in quotes[-10:]:
            st_eff = self.history_mgr.effective_status(q)
            st_style = get_status_style(st_eff)
            table.add_row(
                q.quote_id,
                f"[{st_style}]{st_eff}[/{st_style}]",
                q.date,
                q.customer.name,
                str(len(q.items)),
                format_currency(q.total, q.currency_symbol)
            )
        console.print(table)

        qid = Prompt.ask("\nIngresa el ID de la cotización que deseas editar").strip()
        original_quote = self.history_mgr.get_quote(qid)
        if not original_quote:
            console.print(f"[bold red]No se encontró ninguna cotización con ID '{qid}'.[/bold red]")
            return

        working_items: List[QuoteItem] = copy.deepcopy(original_quote.items)
        working_customer: Customer = copy.deepcopy(original_quote.customer)
        custom_shipping_costs: Dict[str, float] = {
            sd.store_name: sd.shipping_cost
            for sd in original_quote.shipping_details
            if sd.shipping_was_custom
        }
        fee_percent = original_quote.service_fee_percent

        while True:
            console.clear()
            self.show_banner()
            console.print(f"\n[bold yellow]MODO EDICIÓN:[/bold yellow] [bold cyan]{original_quote.quote_id}[/bold cyan] (Cliente: {working_customer.name})")
            
            store_subtotals = QuoteCalculator.calculate_store_subtotals(working_items) if working_items else {}
            current_shipping = QuoteCalculator.evaluate_shipping_details(store_subtotals, self.config.shipping_rules, custom_shipping_costs)
            temp_quote = QuoteCalculator.build_quote(
                quote_id=original_quote.quote_id,
                items=working_items if working_items else [QuoteItem(Product("Vacío", "", "N/A", 0), 1, 0, 0)],
                customer=working_customer,
                shipping_details=current_shipping,
                service_fee_percent=fee_percent,
                validity_days=self.config.validity_days,
                version=original_quote.version,
                base_quote_id=original_quote.base_quote_id
            )
            temp_quote.status = original_quote.status
            temp_quote.status_updated_at = original_quote.status_updated_at
            self._mostrar_cotizacion_completa(temp_quote)

            console.print("\n[bold green]ACCIONES DISPONIBLES:[/bold green]")
            console.print("  [bold cyan]1.[/bold cyan] ➕ Agregar nuevo componente (Metabuscador o URL)")
            console.print("  [bold cyan]2.[/bold cyan] ✏️  Modificar cantidad de un componente")
            console.print("  [bold cyan]3.[/bold cyan] 🔄 Re-extraer precio actual de un componente (o todos)")
            console.print("  [bold cyan]4.[/bold cyan] ❌ Eliminar un componente")
            console.print("  [bold cyan]5.[/bold cyan] 🚚 Modificar costos de envío por tienda")
            console.print("  [bold cyan]6.[/bold cyan] 👤 Modificar datos del cliente (Nombre, Teléfono, Email, Notas)")
            console.print("  [bold cyan]7.[/bold cyan] 💾 [bold green]Guardar como nueva versión (v2, v3...) y generar PDF/HTML/CSV[/bold green]")
            console.print("  [bold cyan]8.[/bold cyan] ↩️  Cancelar y salir sin guardar")

            opc = Prompt.ask("\nSelecciona una acción", choices=["1", "2", "3", "4", "5", "6", "7", "8"], default="1")

            if opc == "1":
                new_prod = self._obtener_producto_interactivo()
                if new_prod:
                    qty = IntPrompt.ask(f"Cantidad deseada para '{new_prod.name}'", default=1)
                    working_items.append(QuoteCalculator.create_quote_item(new_prod, qty))
                    console.print("[bold green]✔ Componente agregado exitosamente.[/bold green]")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif opc == "2":
                if not working_items:
                    console.print("[yellow]No hay componentes.[/yellow]")
                    continue
                idx = IntPrompt.ask(f"Ingresa el # de ítem a modificar (1 a {len(working_items)})")
                if 1 <= idx <= len(working_items):
                    item = working_items[idx - 1]
                    new_qty = IntPrompt.ask(f"Nueva cantidad para '{item.product.name}'", default=item.quantity)
                    if new_qty > 0:
                        working_items[idx - 1] = QuoteCalculator.create_quote_item(item.product, new_qty)
                        console.print("[green]✔ Cantidad actualizada.[/green]")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif opc == "3":
                if not working_items:
                    console.print("[yellow]No hay componentes.[/yellow]")
                    continue
                console.print("\n1. Actualizar TODOS los componentes")
                console.print("2. Actualizar un componente específico")
                sub_opc = Prompt.ask("Selecciona opción", choices=["1", "2"], default="1")

                with console.status("[bold yellow]Consultando tiendas en vivo...[/bold yellow]"):
                    if sub_opc == "1":
                        for i, it in enumerate(working_items):
                            try:
                                p = scrape_product(it.product.url)
                                working_items[i] = QuoteCalculator.create_quote_item(p, it.quantity)
                            except Exception as e:
                                console.print(f"[red]Error actualizando ítem {i+1}: {e}[/red]")
                        console.print("[green]✔ Todos los componentes han sido actualizados con precios en vivo.[/green]")
                    else:
                        item_num = IntPrompt.ask(f"Ingresa el # de ítem (1 a {len(working_items)})")
                        if 1 <= item_num <= len(working_items):
                            it = working_items[item_num - 1]
                            try:
                                p = scrape_product(it.product.url)
                                working_items[item_num - 1] = QuoteCalculator.create_quote_item(p, it.quantity)
                                console.print(f"[green]✔ Ítem actualizado a Q {p.unit_price:.2f}[/green]")
                            except Exception as e:
                                console.print(f"[red]Error: {e}[/red]")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif opc == "4":
                if not working_items:
                    console.print("[yellow]No hay componentes.[/yellow]")
                    continue
                del_idx = IntPrompt.ask(f"Ingresa el # de ítem a eliminar (1 a {len(working_items)})")
                if 1 <= del_idx <= len(working_items):
                    removed = working_items.pop(del_idx - 1)
                    console.print(f"[red]✔ Eliminado:[/red] {removed.product.name}")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif opc == "5":
                custom_shipping = self._solicitar_envios_interactivo(working_items, custom_shipping_costs)
                custom_shipping_costs = {sd.store_name: sd.shipping_cost for sd in custom_shipping}
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif opc == "6":
                working_customer = self._pedir_datos_cliente(default_customer=working_customer)
                console.print("[green]✔ Datos del cliente actualizados para esta versión.[/green]")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif opc == "7":
                if not working_items:
                    console.print("[red]No se puede guardar una cotización vacía.[/red]")
                    Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")
                    continue

                new_qid, new_version, base_id = self.history_mgr.get_next_version_info(original_quote.quote_id)
                final_shipping = self._solicitar_envios_interactivo(working_items, custom_shipping_costs)

                versioned_quote = QuoteCalculator.build_quote(
                    quote_id=new_qid,
                    items=working_items,
                    customer=working_customer,
                    shipping_details=final_shipping,
                    service_fee_percent=fee_percent,
                    validity_days=self.config.validity_days,
                    version=new_version,
                    base_quote_id=base_id,
                    currency_symbol=self.config.currency_symbol,
                    currency_code=self.config.currency_code
                )
                versioned_quote.status = QuoteStatus.GUARDADA.value

                flow = QuoteFlowService(self.config, self.history_mgr, self.exporter)
                with console.status("[bold green]Generando archivos de la nueva versión (Cliente e Interno)...[/bold green]"):
                    res = flow.save_and_export(versioned_quote)
                if res.merged_count:
                    console.print(f"[yellow]🧮 Se fusionaron {res.merged_count} componente(s) repetido(s).[/yellow]")
                self._mostrar_panel_documentos(res.export, res.quote.quote_id)
                break

            elif opc == "8":
                if Confirm.ask("¿Estás seguro de cancelar la edición? Se descartarán los cambios no guardados.", default=True):
                    break

    def _mostrar_resumen_items(self, items: List[QuoteItem]):
        table = Table(title="Componentes en esta cotización", box=box.ROUNDED)
        table.add_column("#", justify="center", style="cyan", no_wrap=True)
        table.add_column("Componente", style="white")
        table.add_column("Tienda", style="dim")
        table.add_column("Cant.", justify="center", style="bold")
        table.add_column("P. Unitario", justify="right", style="green")
        table.add_column("Subtotal", justify="right", style="bold green")

        subtotal_sum = 0.0
        for i, item in enumerate(items, 1):
            table.add_row(
                str(i),
                item.product.name[:45] + ("..." if len(item.product.name) > 45 else ""),
                item.product.store_name,
                str(item.quantity),
                format_currency(item.unit_price, self.config.currency_symbol),
                format_currency(item.subtotal, self.config.currency_symbol)
            )
            subtotal_sum += item.subtotal

        console.print(table)
        console.print(f"[bold]Subtotal componentes:[/bold] [green]{format_currency(subtotal_sum, self.config.currency_symbol)}[/green]")

    def _mostrar_cotizacion_completa(self, quote: Quote):
        st_style = get_status_style(quote.status)
        table = Table(title=f"COTIZACIÓN: {quote.quote_id} (v{quote.version}) • Estado: [{st_style}]{quote.status}[/{st_style}]", box=box.HEAVY_EDGE)
        table.add_column("#", justify="center", style="cyan", no_wrap=True)
        table.add_column("Componente", style="white")
        table.add_column("Tienda", style="dim")
        table.add_column("Cant.", justify="center", style="bold")
        table.add_column("Precio U.", justify="right", style="green")
        table.add_column("Subtotal", justify="right", style="bold green")

        for i, item in enumerate(quote.items, 1):
            table.add_row(
                str(i),
                item.product.name,
                item.product.store_name,
                str(item.quantity),
                format_currency(item.unit_price, quote.currency_symbol),
                format_currency(item.subtotal, quote.currency_symbol)
            )

        console.print("\n")
        console.print(table)

        # Client info card
        client_summary = Table(box=box.SIMPLE, show_header=False)
        client_summary.add_row("Cliente:", f"[bold white]{quote.customer.name}[/bold white]")
        if quote.customer.phone:
            client_summary.add_row("Teléfono:", f"[cyan]{quote.customer.phone}[/cyan]")
        if quote.customer.email:
            client_summary.add_row("Email:", f"[cyan]{quote.customer.email}[/cyan]")
        if quote.customer.notes:
            client_summary.add_row("Notas:", f"[yellow]{quote.customer.notes}[/yellow]")
        client_summary.add_row("Estado Comercial:", f"[{st_style}]{quote.status}[/{st_style}] (Act: {quote.status_updated_at[:19] if quote.status_updated_at else quote.date})")
        console.print(Panel(client_summary, title="[bold]Información del Cliente y Estado[/bold]", border_style="blue", expand=False))

        # Financial summary card
        summary_table = Table(box=box.SIMPLE, show_header=False)
        summary_table.add_row("Subtotal Componentes:", format_currency(quote.items_subtotal, quote.currency_symbol))
        if getattr(quote, "discount_amount", 0.0) > 0:
            disc_label = f"Descuento Especial ({quote.discount_percent:.1f}%):" if getattr(quote, "discount_percent", 0.0) > 0 else "Descuento Especial:"
            summary_table.add_row(f"[green]{disc_label}[/green]", f"[bold green]- {format_currency(quote.discount_amount, quote.currency_symbol)}[/bold green]")
        summary_table.add_row(f"Cargo por Gestión/Servicio ({quote.service_fee_percent}%):", format_currency(quote.service_fee_amount, quote.currency_symbol))
        
        if quote.shipping_details:
            summary_table.add_row("[bold cyan]Desglose Envíos:[/bold cyan]", "")
            for sd in quote.shipping_details:
                if sd.qualifies_free or sd.is_pickup_only:
                    cost_str = f"[green]{sd.status_label}[/green]"
                else:
                    cost_str = f"[bold]{format_currency(sd.shipping_cost, quote.currency_symbol)}[/bold]"
                summary_table.add_row(f"  ↳ Envío {sd.store_name}:", cost_str)

        summary_table.add_row("[bold green]TOTAL A PAGAR:[/bold green]", f"[bold green]{format_currency(quote.total, quote.currency_symbol)}[/bold green]")
        
        console.print(Panel(summary_table, title="[bold]Desglose Financiero[/bold]", border_style="cyan", expand=False))

    def mostrar_packing_list(self, quote: Quote):
        """Muestra la hoja de compra consolidada (Packing List) agrupada por tienda."""
        plist = self.exporter.generate_packing_list(quote)
        console.clear()
        self.show_banner()
        console.print(f"\n[bold green]📦 HOJA DE COMPRAS (PACKING LIST) — {quote.quote_id}[/bold green]")
        console.print(f"[dim]Cliente: {quote.customer.name} | Fecha: {quote.date}[/dim]\n")

        for sname, sdata in plist["stores"].items():
            st_table = Table(title=f"🏬 {sname}", box=box.ROUNDED, border_style="cyan")
            st_table.add_column("Cant.", justify="center", style="bold yellow")
            st_table.add_column("Componente", style="white")
            st_table.add_column("SKU", style="dim")
            st_table.add_column("Precio Unit.", justify="right")
            st_table.add_column("Subtotal", justify="right", style="bold green")
            st_table.add_column("URL / Enlace", style="cyan")

            for it in sdata["items"]:
                st_table.add_row(
                    str(it["quantity"]),
                    it["name"],
                    it["sku"] or "-",
                    format_currency(it["unit_price"], quote.currency_symbol),
                    format_currency(it["subtotal"], quote.currency_symbol),
                    it["url"] or "Ingreso Manual"
                )
            console.print(st_table)
            console.print(f"[bold]  ↳ Subtotal ítems:[/bold] {format_currency(sdata['subtotal'], quote.currency_symbol)} | [bold]Flete:[/bold] {format_currency(sdata['shipping_cost'], quote.currency_symbol)} | [bold green]Total a pagar en {sname}:[/bold green] [bold green]{format_currency(sdata['total_store'], quote.currency_symbol)}[/bold green]\n")

        console.print(Panel(
            f"[bold]Total a Desembolsar en Tiendas:[/bold] [bold cyan]{format_currency(plist['total_purchase_cost'], quote.currency_symbol)}[/bold cyan]\n"
            f"[bold]Total Cotizado al Cliente:[/bold] [bold green]{format_currency(plist['total_client_price'], quote.currency_symbol)}[/bold green]\n"
            f"[bold]Ganancia Neta Estimada (Margen):[/bold] [bold yellow]{format_currency(plist['estimated_profit'], quote.currency_symbol)}[/bold yellow]",
            title="[bold]Resumen Financiero de Compra[/bold]",
            border_style="green",
            expand=False
        ))

    def mostrar_metricas_comerciales(self):
        """Muestra panel resumen con indicadores comerciales y tasa de cierre."""
        stats = self.history_mgr.get_commercial_analytics()
        console.clear()
        self.show_banner()
        console.print("\n[bold cyan]📊 MÉTRICAS COMERCIALES Y ANALÍTICA DE COTIZACIONES[/bold cyan]\n")

        kpi_table = Table(box=box.ROUNDED, show_header=False)
        kpi_table.add_row("Total de Cotizaciones Emitidas:", f"[bold cyan]{stats['total_quotes']}[/bold cyan]")
        kpi_table.add_row("Cotizaciones Aceptadas (Ganadas):", f"[bold green]{stats['accepted_count']}[/bold green]")
        kpi_table.add_row("Tasa de Cierre Comercial:", f"[bold yellow]{stats['conversion_rate']}%[/bold yellow]")
        kpi_table.add_row("Monto Total Cotizado:", f"[cyan]{format_currency(stats['total_quoted_amount'], self.config.currency_symbol)}[/cyan]")
        kpi_table.add_row("Monto Total Facturado / Ganado:", f"[bold green]{format_currency(stats['total_sold_amount'], self.config.currency_symbol)}[/bold green]")
        kpi_table.add_row("Margen Neto Acumulado (Ganancia):", f"[bold yellow]{format_currency(stats['total_earned_margin'], self.config.currency_symbol)}[/bold yellow]")
        console.print(Panel(kpi_table, title="[bold]Indicadores Clave (KPIs)[/bold]", border_style="cyan", expand=False))

        # Status distribution
        st_table = Table(title="Distribución de Estados Comerciales", box=box.SIMPLE)
        st_table.add_column("Estado", style="bold")
        st_table.add_column("Cantidad", justify="center")
        for st_name, st_count in stats["status_counts"].items():
            st_table.add_row(st_name, str(st_count))
        console.print(st_table)

        # Frequent customers
        if stats.get("frequent_customers"):
            fc_table = Table(title="Top Clientes Frecuentes", box=box.SIMPLE)
            fc_table.add_column("Cliente", style="bold white")
            fc_table.add_column("Teléfono", style="cyan")
            fc_table.add_column("Cotizaciones", justify="center")
            fc_table.add_column("Monto Aceptado", justify="right", style="green")
            for fc in stats["frequent_customers"]:
                fc_table.add_row(fc["name"], fc["phone"] or "-", str(fc["count"]), format_currency(fc["total_spent"], self.config.currency_symbol))
            console.print(fc_table)

    def ver_historial(self):
        while True:
            console.clear()
            self.show_banner()
            console.print("\n[bold cyan]=== HISTORIAL Y BÚSQUEDA DE COTIZACIONES ===[/bold cyan]")
            all_quotes = self.history_mgr.load_all_quotes()

            if not all_quotes:
                console.print("[yellow]No hay cotizaciones guardadas aún.[/yellow]")
                return

            search_query = Prompt.ask("Buscar por ID, Cliente, Teléfono, Email, Notas o Fecha (Enter para ver todas)", default="").strip()
            
            # Status filter option
            console.print("\n[dim]Filtros de estado: [1] TODOS | [2] BORRADOR | [3] GUARDADA | [4] ENVIADA | [5] ACEPTADA | [6] RECHAZADA | [7] VENCIDA[/dim]")
            st_choice = Prompt.ask("Filtrar por estado", choices=["1", "2", "3", "4", "5", "6", "7"], default="1")
            status_map = {"1": "TODOS", "2": "BORRADOR", "3": "GUARDADA", "4": "ENVIADA", "5": "ACEPTADA", "6": "RECHAZADA", "7": "VENCIDA"}
            selected_filter = status_map[st_choice]

            filtered_quotes = self.history_mgr.search_quotes(query=search_query, status_filter=selected_filter)

            if not filtered_quotes:
                console.print("[yellow]No se encontraron cotizaciones coincidentes con los filtros.[/yellow]")
                if not Confirm.ask("¿Deseas realizar otra búsqueda?", default=True):
                    return
                continue

            table = Table(title=f"Cotizaciones ({len(filtered_quotes)} encontradas)", box=box.ROUNDED)
            table.add_column("ID Cotización", style="bold cyan")
            table.add_column("Estado", justify="center")
            table.add_column("Ver.", justify="center", style="dim")
            table.add_column("Fecha", style="dim")
            table.add_column("Cliente", style="white")
            table.add_column("Teléfono", style="dim")
            table.add_column("Ítems", justify="center")
            table.add_column("Total (GTQ)", justify="right", style="bold green")

            for q in filtered_quotes:
                st_eff = self.history_mgr.effective_status(q)
                st_style = get_status_style(st_eff)
                table.add_row(
                    q.quote_id,
                    f"[{st_style}]{st_eff}[/{st_style}]",
                    f"v{q.version}",
                    q.date,
                    q.customer.name,
                    q.customer.phone or "-",
                    str(len(q.items)),
                    format_currency(q.total, q.currency_symbol)
                )

            console.print(table)

            console.print("\n[bold cyan]Opciones de Acción sobre el Historial:[/bold cyan]")
            console.print("  [bold green][V][/bold green] Ver detalle completo")
            console.print("  [bold green][K][/bold green] 📦 Ver Hoja de Compras (Packing List por Tienda)")
            console.print("  [bold green][S][/bold green] 🏷️  Cambiar estado comercial (Borrador, Enviada, Aceptada...)")
            console.print("  [bold green][D][/bold green] 📄 Duplicar cotización como nueva independiente")
            console.print("  [bold green][R][/bold green] 🔄 Re-verificar precios en vivo (Crear nueva versión)")
            console.print("  [bold green][P][/bold green] 📑 Re-generar / Abrir archivos PDF")
            console.print("  [bold green][E][/bold green] 💾 Exportar historial (JSON/CSV)")
            console.print("  [bold green][I][/bold green] 📥 Importar historial (JSON)")
            console.print("  [bold cyan][B][/bold cyan] 🔍 Nueva búsqueda en historial")
            console.print("  [bold cyan][0][/bold cyan] ↩️  Regresar al menú principal")

            hist_opc = Prompt.ask("\nSelecciona acción", choices=["v", "k", "s", "d", "r", "p", "e", "i", "b", "0"], default="0").lower()

            if hist_opc == "0":
                return
            elif hist_opc == "b":
                continue

            elif hist_opc == "k":
                qid = Prompt.ask("Ingresa el ID de la cotización").strip()
                quote = self.history_mgr.get_quote(qid)
                if quote:
                    self.mostrar_packing_list(quote)
                else:
                    console.print(f"[bold red]Cotización '{qid}' no encontrada.[/bold red]")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif hist_opc == "e":
                export_dir = self.history_mgr.file_path.parent
                json_path = export_dir / "historial_export.json"
                csv_path = export_dir / "historial_export.csv"
                self.history_mgr.export_history(json_path)
                self.history_mgr.export_history_csv(csv_path)
                console.print(f"[bold green]✔ Historial exportado:[/bold green]\n  📄 {json_path}\n  📊 {csv_path}")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif hist_opc == "i":
                import_path = Prompt.ask("Ruta del archivo JSON a importar").strip()
                if not import_path:
                    continue
                try:
                    added = self.history_mgr.import_history(Path(import_path))
                    console.print(f"[bold green]✔ Importación completada: {added} cotización(es) agregada(s).[/bold green]")
                except Exception as e:
                    console.print(f"[bold red]❌ Error al importar:[/bold red] {e}")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif hist_opc == "v":
                qid = Prompt.ask("Ingresa el ID de la cotización").strip()
                quote = self.history_mgr.get_quote(qid)
                if quote:
                    self._mostrar_cotizacion_completa(quote)
                else:
                    console.print("[red]Cotización no encontrada.[/red]")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif hist_opc == "s":
                qid = Prompt.ask("Ingresa el ID de la cotización").strip()
                quote = self.history_mgr.get_quote(qid)
                if not quote:
                    console.print("[red]Cotización no encontrada.[/red]")
                else:
                    allowed = quote.get_allowed_transitions()
                    console.print(f"\n[bold yellow]Estado actual de {quote.quote_id}:[/bold yellow] [{get_status_style(quote.status)}]{quote.status}[/{get_status_style(quote.status)}]")
                    
                    if not allowed:
                        console.print("[red]Esta cotización no tiene transiciones permitidas.[/red]")
                    else:
                        console.print("\n[cyan]Transiciones válidas disponibles:[/cyan]")
                        for idx, s in enumerate(allowed, 1):
                            console.print(f"  [{idx}] Cambiar a [{get_status_style(s.value)}]{s.value}[/{get_status_style(s.value)}]")
                        console.print("  [0] Cancelar")

                        sel_st = IntPrompt.ask("Selecciona nuevo estado", default=0)
                        if 1 <= sel_st <= len(allowed):
                            new_st = allowed[sel_st - 1]
                            try:
                                updated_q = self.history_mgr.update_quote_status(quote.quote_id, new_st)
                                # F6: al aceptar la venta se pueden registrar notas de factura/entrega
                                if new_st == QuoteStatus.ACEPTADA:
                                    notes = Prompt.ask(
                                        "📦 Notas de venta (factura/entrega, opcional)",
                                        default=updated_q.sale_notes,
                                    )
                                    if notes != updated_q.sale_notes:
                                        updated_q.sale_notes = notes
                                        self.history_mgr.save_quote(updated_q, force_overwrite=True)
                                self.exporter.export_all(updated_q, self.config.business)
                                console.print(f"[bold green]✔ ¡Estado de {updated_q.quote_id} actualizado a {updated_q.status} con éxito![/bold green]")
                            except InvalidStatusTransitionError as e:
                                console.print(f"[bold red]❌ Error de transición:[/bold red] {e}")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif hist_opc == "d":
                qid = Prompt.ask("Ingresa el ID de la cotización que deseas duplicar").strip()
                orig = self.history_mgr.get_quote(qid)
                if orig:
                    if Confirm.ask("¿Deseas asignar un cliente diferente?", default=False):
                        new_cust = self._pedir_datos_cliente(default_customer=orig.customer)
                    else:
                        new_cust = copy.deepcopy(orig.customer)
                    dup = self.history_mgr.duplicate_quote(orig.quote_id, new_customer=new_cust)
                    exp_res = self.exporter.export_all(dup, self.config.business)
                    console.print(f"[bold green]✔ Cotización duplicada exitosamente como {dup.quote_id}[/bold green]")
                    self._mostrar_panel_documentos(exp_res, dup.quote_id)
                else:
                    console.print("[red]Cotización no encontrada.[/red]")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif hist_opc == "r":
                qid = Prompt.ask("Ingresa el ID de la cotización").strip()
                self._ejecutar_reverificacion_interactiva(qid)
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

            elif hist_opc == "p":
                qid = Prompt.ask("Ingresa el ID de la cotización").strip()
                quote = self.history_mgr.get_quote(qid)
                if quote:
                    with console.status("[bold green]Exportando archivos...[/bold green]"):
                        exp_res = self.exporter.export_all(quote, self.config.business)
                    self._mostrar_panel_documentos(exp_res, quote.quote_id)
                else:
                    console.print("[red]Cotización no encontrada.[/red]")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

    def reverificar_cotizacion(self):
        console.print("\n[bold cyan]=== RE-VERIFICAR PRECIOS DE COTIZACIÓN (VERSIONADO INMUTABLE) ===[/bold cyan]")
        quotes = self.history_mgr.load_all_quotes()
        if not quotes:
            console.print("[yellow]No hay cotizaciones registradas para verificar.[/yellow]")
            return

        qid = Prompt.ask("Ingresa el ID de la cotización que deseas re-verificar").strip()
        self._ejecutar_reverificacion_interactiva(qid)

    def _ejecutar_reverificacion_interactiva(self, qid: str):
        quote = self.history_mgr.get_quote(qid)
        if not quote:
            console.print(f"[red]Cotización '{qid}' no encontrada.[/red]")
            return

        console.print(f"\n[cyan]Consultando tiendas para los {len(quote.items)} componentes de {qid}...[/cyan]")
        
        with console.status("[bold yellow]Actualizando precios en tiempo real...[/bold yellow]", spinner="bouncingBar"):
            try:
                candidate_q, changes, diff = self.history_mgr.check_quote_price_updates(qid)
            except Exception as e:
                console.print(f"[bold red]Error al re-verificar:[/bold red] {e}")
                return

        # 1. Tabla de items y diferencias de precio
        table = Table(title=f"Comparativa de Precios en Vivo: {quote.quote_id} ➔ {candidate_q.quote_id}", box=box.ROUNDED)
        table.add_column("Componente", style="white")
        table.add_column("Tienda", style="dim")
        table.add_column("Precio Anterior", justify="right")
        table.add_column("Precio Actual", justify="right")
        table.add_column("Diferencia U.", justify="right")
        table.add_column("Subtotal Actual", justify="right")
        table.add_column("Disponibilidad", justify="center")

        for c in changes:
            diff_style = "green" if c["price_diff"] < 0 else ("red" if c["price_diff"] > 0 else "dim")
            diff_str = f"{c['price_diff']:+.2f}" if c["price_diff"] != 0 else "0.00"
            stock_style = "green" if c["new_in_stock"] else "red"
            
            table.add_row(
                c["product_name"][:32],
                c["store"],
                format_currency(c["old_price"], quote.currency_symbol),
                format_currency(c["new_price"], quote.currency_symbol),
                f"[{diff_style}]{diff_str}[/{diff_style}]",
                format_currency(c["new_subtotal"], quote.currency_symbol),
                f"[{stock_style}]{c['stock_status']}[/{stock_style}]"
            )

        console.print("\n")
        console.print(table)

        # 2. Desglose de cambios de envío
        if diff["shipping_diff_details"]:
            s_table = Table(title="Desglose de Envíos por Tienda", box=box.SIMPLE)
            s_table.add_column("Tienda", style="cyan")
            s_table.add_column("Envío Anterior", justify="right")
            s_table.add_column("Envío Actual", justify="right")
            s_table.add_column("Diferencia", justify="right")

            for sd in diff["shipping_diff_details"]:
                sd_diff_str = f"{sd['diff']:+.2f}" if sd['diff'] != 0 else "0.00"
                sd_style = "green" if sd['diff'] <= 0 else "red"
                s_table.add_row(
                    sd["store"],
                    format_currency(sd["old_shipping"], quote.currency_symbol),
                    format_currency(sd["new_shipping"], quote.currency_symbol),
                    f"[{sd_style}]{sd_diff_str}[/{sd_style}]"
                )
            console.print(s_table)

        # 3. Resumen financiero comparativo con deltas
        sum_table = Table(box=box.SIMPLE, show_header=False)
        
        tot_diff = diff["total_diff"]
        tot_style = "green" if tot_diff < 0 else ("red" if tot_diff > 0 else "dim")
        tot_diff_str = f"{tot_diff:+.2f}" if tot_diff != 0 else "0.00"

        sum_table.add_row("Subtotal Componentes:", f"{format_currency(diff['old_items_subtotal'], quote.currency_symbol)}  ➔  [bold]{format_currency(diff['new_items_subtotal'], quote.currency_symbol)}[/bold] ({diff['items_subtotal_diff']:+.2f})")
        sum_table.add_row("Margen de Servicio:", f"{format_currency(diff['old_service_fee'], quote.currency_symbol)}  ➔  [bold]{format_currency(diff['new_service_fee'], quote.currency_symbol)}[/bold] ({diff['service_fee_diff']:+.2f})")
        sum_table.add_row("Total Envíos:", f"{format_currency(diff['old_total_shipping'], quote.currency_symbol)}  ➔  [bold]{format_currency(diff['new_total_shipping'], quote.currency_symbol)}[/bold] ({diff['total_shipping_diff']:+.2f})")
        sum_table.add_row("[bold]TOTAL GENERAL:[/bold]", f"[bold]{format_currency(diff['old_total'], quote.currency_symbol)}[/bold]  ➔  [bold green]{format_currency(diff['new_total'], quote.currency_symbol)}[/bold green] ([{tot_style}]{tot_diff_str}[/{tot_style}])")

        console.print(Panel(sum_table, title="[bold cyan]Resumen de Diferencias Financieras[/bold cyan]", border_style="cyan"))

        console.print(f"\n[bold yellow]⚠️ Aviso de Inmutabilidad:[/bold yellow] La versión original [bold]{quote.quote_id}[/bold] permanecerá intacta en el historial.")
        if Confirm.ask(f"\n¿Deseas aceptar los cambios y crear la versión [bold green]{candidate_q.quote_id}[/bold green] (v{candidate_q.version})?", default=True):
            saved_v = self.history_mgr.save_reverified_version(candidate_q)
            with console.status("[bold green]Generando archivos PDF, HTML y CSV para la nueva versión...[/bold green]"):
                exp_res = self.exporter.export_all(saved_v, self.config.business)

            console.print(f"\n[bold green]✔ ¡Nueva versión {saved_v.quote_id} creada y guardada con éxito![/bold green]")
            self._mostrar_panel_documentos(exp_res, saved_v.quote_id)
        else:
            console.print("\n[yellow]Revalidación cancelada. El historial no ha sufrido ninguna modificación.[/yellow]")

    def eliminar_cotizacion(self):
        """Elimina DEFINITIVAMENTE una cotización (y sus versiones) del historial."""
        console.print("\n[bold red]=== ELIMINAR COTIZACIÓN (DEFINITIVO) ===[/bold red]")
        quotes = self.history_mgr.load_all_quotes()
        if not quotes:
            console.print("[yellow]No hay cotizaciones guardadas para eliminar.[/yellow]")
            return

        table = Table(title="Cotizaciones Registradas", box=box.ROUNDED)
        table.add_column("ID", style="bold cyan")
        table.add_column("Estado", justify="center")
        table.add_column("Fecha", style="dim")
        table.add_column("Cliente", style="white")
        table.add_column("Total", justify="right", style="bold green")

        for q in quotes[-15:]:
            st_eff = self.history_mgr.effective_status(q)
            st_style = get_status_style(st_eff)
            table.add_row(
                q.quote_id,
                f"[{st_style}]{st_eff}[/{st_style}]",
                q.date,
                q.customer.name,
                format_currency(q.total, q.currency_symbol)
            )
        console.print(table)

        qid = Prompt.ask("\nIngresa el ID de la cotización a eliminar").strip()
        target = self.history_mgr.get_quote(qid)
        if not target:
            console.print(f"[bold red]No se encontró ninguna cotización con ID '{qid}'.[/bold red]")
            return

        console.print(f"\n[bold yellow]Cotización seleccionada:[/bold yellow] {target.quote_id} "
                      f"({target.customer.name}) con {len(target.items)} componentes.")
        console.print("[dim]Se eliminarán también todas sus versiones (_vN) del historial.[/dim]")
        console.print("[dim]Los archivos PDF/HTML/CSV ya generados en la carpeta output/ no se tocan.[/dim]")

        if Confirm.ask(f"¿Eliminar DEFINITIVAMENTE {target.quote_id}? Esta acción no se puede deshacer.", default=False):
            removed = self.history_mgr.delete_quote(target.quote_id)
            if removed:
                console.print(f"[bold green]✔ {removed} registro(s) eliminados del historial.[/bold green]")
            else:
                console.print("[yellow]No se eliminó nada (la cotización ya no existe).[/yellow]")

    def configuracion_menu(self):
        console.print("\n[bold cyan]=== CONFIGURACIÓN DE PARÁMETROS ===[/bold cyan]")
        console.print(f"1. Margen de compra predeterminado: [bold green]{self.config.service_fee_percent}%[/bold green]")
        console.print(f"2. Vigencia de cotización: [bold green]{self.config.validity_days} días[/bold green]")
        console.print(f"3. Nombre de negocio: [bold white]{self.config.business.name}[/bold white]")
        console.print(f"4. Teléfono/WhatsApp: [bold white]{self.config.business.phone}[/bold white]")
        console.print(f"5. Email: [bold white]{self.config.business.email}[/bold white]")
        
        console.print("\n[bold cyan]Reglas de Envío por Tienda:[/bold cyan]")
        for store, rules in self.config.shipping_rules.items():
            if rules.get("is_pickup_only"):
                console.print(f"  • {store}: [green]Retiro en tienda (Sin costo)[/green]")
            else:
                console.print(f"  • {store}: [yellow]Gratis desde Q{rules.get('free_threshold', 0):,.0f}[/yellow] (Costo por defecto: Q{rules.get('default_cost', 35.0):,.2f})")

        if Confirm.ask("\n¿Deseas editar algún valor de la configuración?", default=False):
            self.config.service_fee_percent = FloatPrompt.ask("Nuevo margen predeterminado (%)", default=self.config.service_fee_percent)
            self.config.validity_days = IntPrompt.ask("Nueva vigencia en días", default=self.config.validity_days)
            self.config.business.name = Prompt.ask("Nombre de tu negocio", default=self.config.business.name)
            self.config.business.phone = Prompt.ask("Teléfono / WhatsApp", default=self.config.business.phone)
            self.config.business.email = Prompt.ask("Email de contacto", default=self.config.business.email)

            if Confirm.ask("¿Deseas editar los umbrales de envío gratis?", default=False):
                for store in ["La Electrónica", "Electrónica DIY"]:
                    thresh = FloatPrompt.ask(f"Monto mínimo para envío gratis en {store} (Q)", default=self.config.shipping_rules[store]["free_threshold"])
                    cost = FloatPrompt.ask(f"Costo de envío sugerido en {store} cuando no alcanza mínimo (Q)", default=self.config.shipping_rules[store]["default_cost"])
                    self.config.shipping_rules[store]["free_threshold"] = thresh
                    self.config.shipping_rules[store]["default_cost"] = cost

            self.config.save()
            console.print("[bold green]✔ Configuración guardada exitosamente.[/bold green]")
