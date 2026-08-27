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

from src.models import Product, QuoteItem, Quote, Customer, StoreShippingDetail
from src.config import AppConfig
from src.scrapers import scrape_product, metasearch, SearchResultItem, StoreNotSupportedError, ScraperError
from src.core.calculator import QuoteCalculator, format_currency
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter, ExportResult
from src.core.bom_parser import parse_bom_text, ParsedBOMItem
from src.core.bom_searcher import (
    search_bom_items_parallel,
    calculate_match_score,
    MatchResult,
    BOMScenario,
    build_all_bom_scenarios
)

console = Console()

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
            console.print("  [bold cyan]4.[/bold cyan] 📋 Ver Historial de Cotizaciones")
            console.print("  [bold cyan]5.[/bold cyan] 🔄 Re-verificar Precios de una Cotización")
            console.print("  [bold cyan]6.[/bold cyan] ⚙️  Configuración (Margen, Envíos, Negocio)")
            console.print("  [bold cyan]7.[/bold cyan] 🚪 Salir")

            choice = Prompt.ask("\nSelecciona una opción", choices=["1", "2", "3", "4", "5", "6", "7"], default="1")

            if choice == "1":
                self.crear_cotizacion_bom()
            elif choice == "2":
                self.crear_nueva_cotizacion()
            elif choice == "3":
                self.editar_cotizacion()
            elif choice == "4":
                self.ver_historial()
            elif choice == "5":
                self.reverificar_cotizacion()
            elif choice == "6":
                self.configuracion_menu()
            elif choice == "7":
                console.print("\n[bold green]¡Hasta pronto![/bold green] 👋\n")
                sys.exit(0)

            Prompt.ask("\n[dim]Presiona Enter para continuar...[/dim]")

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

    def _obtener_producto_interactivo(self) -> Optional[Product]:
        """Allows user to search by name in the 3 stores or paste a direct URL."""
        console.print("\n[bold yellow]¿Cómo deseas agregar el componente?[/bold yellow]")
        console.print("  [bold cyan]1.[/bold cyan] 🔍 Buscar por nombre / valor en las 3 tiendas (Metabuscador)")
        console.print("  [bold cyan]2.[/bold cyan] 🔗 Pegar URL directa")
        console.print("  [bold cyan]3.[/bold cyan] ↩️  Cancelar")

        modo = Prompt.ask("Selecciona método", choices=["1", "2", "3"], default="1")

        if modo == "1":
            while True:
                query = Prompt.ask("\n🔍 Ingresa término de búsqueda (ej. 'ESP32', 'resistencia 220', 'sensor ultrasónico')").strip()
                if not query:
                    return None

                with console.status(f"[bold green]Buscando '{query}' en RyCH, La Electrónica y DIY en paralelo...[/bold green]", spinner="dots"):
                    results = metasearch(query, max_per_store=5)

                if not results:
                    console.print(f"[yellow]No se encontraron resultados para '{query}'.[/yellow]")
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
                        format_currency(r.unit_price, self.config.currency_symbol) if r.unit_price > 0 else "Consultar",
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
                            return prod
                        except Exception:
                            return Product(
                                name=chosen.title,
                                url=chosen.url,
                                store_name=chosen.store_name,
                                unit_price=chosen.unit_price,
                                in_stock=chosen.in_stock,
                                stock_status=chosen.stock_status,
                                image_url=chosen.image_url
                            )
                else:
                    console.print("[red]Selección fuera de rango.[/red]")

        elif modo == "2":
            url = Prompt.ask("\nPega la URL del producto").strip()
            if not url:
                return None
            with console.status("[bold green]Extrayendo datos de la tienda...[/bold green]", spinner="dots"):
                try:
                    product = scrape_product(url)
                    return product
                except StoreNotSupportedError as e:
                    console.print(f"[bold red]❌ Error de Tienda:[/bold red] {e}")
                    return None
                except Exception as e:
                    console.print(f"[bold red]❌ Error al extraer producto:[/bold red] {e}")
                    return None

        return None

    def _mostrar_panel_documentos(self, exp_res: ExportResult, quote_id: str):
        """Displays a clean summary panel with all generated files (Cliente and Interna)."""
        console.print(Panel(
            f"[bold green]✔ Cotización guardada con éxito![/bold green]\n\n"
            f"📄 [bold]ID Cotización:[/bold] {quote_id}\n\n"
            f"[bold cyan]Archivos para el Cliente:[/bold cyan]\n"
            f"  📑 [bold]PDF Cliente:[/bold]  {exp_res.client_pdf if exp_res.client_pdf else 'No generado'}\n"
            f"  🌐 [bold]HTML Cliente:[/bold] {exp_res.client_html}\n\n"
            f"[bold yellow]Archivos de Control Interno (con Enlaces de Compra):[/bold yellow]\n"
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
        """Workflow for creating a quote from a multiline BOM list with 4 comparison options."""
        console.print("\n[bold cyan]=== COTIZADOR POR LISTA RÁPIDA (BOM MULTILÍNEA) ===[/bold cyan]")
        
        # 1. Datos del cliente
        client_name = Prompt.ask("Nombre del cliente", default="Cliente General")
        client_phone = Prompt.ask("Teléfono / WhatsApp (opcional)", default="")
        customer = Customer(name=client_name, phone=client_phone)

        # 2. Captura de texto multilínea
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
        parse_res = parse_bom_text(raw_text)

        if not parse_res.items:
            console.print("[bold red]❌ No se pudo interpretar ningún componente del texto ingresado.[/bold red]")
            return

        if parse_res.invalid_lines:
            console.print(f"[yellow]⚠️ Se ignoraron {len(parse_res.invalid_lines)} líneas no interpretables.[/yellow]")

        console.print(f"\n[bold green]✔ Se interpretaron {parse_res.total_items} componentes (Total: {parse_res.total_quantity} unidades).[/bold green]")
        
        # 3. Margen de servicio
        margin = self.config.service_fee_percent
        if Confirm.ask(f"\n¿Deseas usar el margen de compra predeterminado de {margin}%?", default=True):
            fee_percent = margin
        else:
            fee_percent = FloatPrompt.ask("Ingresa el porcentaje de margen deseado (%)", default=margin)

        # 4. Búsqueda concurrente en paralelo
        with console.status(f"[bold green]Consultando las 3 tiendas en paralelo para los {parse_res.total_items} componentes...[/bold green]", spinner="dots"):
            match_results = search_bom_items_parallel(parse_res.items, max_workers=5)

        # 5. Construir los 4 escenarios de cotización
        scenarios = build_all_bom_scenarios(
            match_results=match_results,
            customer=customer,
            config=self.config,
            service_fee_percent=fee_percent
        )

        # 6. Menú interactivo de selección y visualización de las 4 opciones
        while True:
            console.clear()
            self.show_banner()
            console.print(f"\n[bold cyan]=== COMPARATIVA DE LAS 4 OPCIONES DE COTIZACIÓN ===[/bold cyan]")
            console.print(f"[dim]Cliente: {customer.name} | Ítems solicitados: {len(parse_res.items)}[/dim]\n")

            # Tabla resumen de las 4 opciones
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
            console.print("  [bold green][1][/bold green] Exportar [bold]Opción 1 (Cotización Mixta - Mejor Precio Combinado)[/bold]")
            console.print("  [bold green][2][/bold green] Exportar [bold]Opción 2 (Todo en Electrónica RyCH)[/bold]")
            console.print("  [bold green][3][/bold green] Exportar [bold]Opción 3 (Todo en La Electrónica)[/bold]")
            console.print("  [bold green][4][/bold green] Exportar [bold]Opción 4 (Todo en Electrónica DIY)[/bold]")
            console.print("  [bold yellow][V1 - V4][/bold yellow] Ver el detalle desglosado de componentes de una opción (ej. 'v1' o 'v3')")
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
                det_table.add_column("Solicitado", style="dim")
                det_table.add_column("Componente Asignado", style="white")
                det_table.add_column("Tienda", style="cyan")
                det_table.add_column("Cant.", justify="center", style="bold")
                det_table.add_column("Precio U.", justify="right", style="green")
                det_table.add_column("Subtotal", justify="right", style="bold green")

                for it_idx, item in enumerate(selected_sc.items, 1):
                    det_table.add_row(
                        str(it_idx),
                        match_results[it_idx-1].bom_item.product_query[:25] if it_idx <= len(match_results) else "-",
                        item.product.name[:40],
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
                
                # Asignar ID oficial definitivo
                final_quote_id = self.history_mgr.get_next_quote_id(self.config.quote_prefix)
                
                # Re-evaluar envíos si el usuario desea ajustar
                final_shipping = chosen_scenario.quote.shipping_details
                
                final_quote = QuoteCalculator.build_quote(
                    quote_id=final_quote_id,
                    items=chosen_scenario.items,
                    customer=customer,
                    shipping_details=final_shipping,
                    service_fee_percent=fee_percent,
                    validity_days=self.config.validity_days,
                    currency_symbol=self.config.currency_symbol,
                    currency_code=self.config.currency_code
                )

                # Mostrar resumen formal antes de guardar
                self._mostrar_cotizacion_completa(final_quote)

                if Confirm.ask("\n¿Deseas guardar la cotización y generar los documentos (Cliente + Interno)?", default=True):
                    self.history_mgr.save_quote(final_quote)
                    with console.status("[bold green]Generando archivos PDF, HTML y CSV...[/bold green]", spinner="dots"):
                        exp_res = self.exporter.export_all(final_quote, self.config.business)

                    self._mostrar_panel_documentos(exp_res, final_quote.quote_id)
                break
            else:
                console.print("[red]Opción no válida. Ingresa 1, 2, 3, 4, o v1-v4 para ver el detalle.[/red]")
                Prompt.ask("[dim]Presiona Enter para continuar...[/dim]")

    def crear_nueva_cotizacion(self):
        console.print("\n[bold cyan]=== NUEVA COTIZACIÓN MANUAL ===[/bold cyan]")
        
        client_name = Prompt.ask("Nombre del cliente", default="Cliente General")
        client_phone = Prompt.ask("Teléfono / WhatsApp (opcional)", default="")
        customer = Customer(name=client_name, phone=client_phone)

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

        quote_id = self.history_mgr.get_next_quote_id(self.config.quote_prefix)
        quote = QuoteCalculator.build_quote(
            quote_id=quote_id,
            items=items,
            customer=customer,
            shipping_details=shipping_details,
            service_fee_percent=fee_percent,
            validity_days=self.config.validity_days,
            currency_symbol=self.config.currency_symbol,
            currency_code=self.config.currency_code
        )

        self._mostrar_cotizacion_completa(quote)

        if Confirm.ask("\n¿Deseas guardar la cotización y generar los documentos (Cliente + Interno)?", default=True):
            self.history_mgr.save_quote(quote)
            with console.status("[bold green]Generando archivos PDF, HTML y CSV...[/bold green]", spinner="dots"):
                exp_res = self.exporter.export_all(quote, self.config.business)

            self._mostrar_panel_documentos(exp_res, quote.quote_id)

    def editar_cotizacion(self):
        console.print("\n[bold cyan]=== EDITAR COTIZACIÓN GUARDADA ===[/bold cyan]")
        quotes = self.history_mgr.load_all_quotes()
        if not quotes:
            console.print("[yellow]No hay cotizaciones guardadas aún para editar.[/yellow]")
            return

        table = Table(title="Cotizaciones Recientes", box=box.ROUNDED)
        table.add_column("ID", style="bold cyan")
        table.add_column("Fecha", style="dim")
        table.add_column("Cliente", style="white")
        table.add_column("Ítems", justify="center")
        table.add_column("Total", justify="right", style="bold green")

        for q in quotes[-10:]:
            table.add_row(q.quote_id, q.date, q.customer.name, str(len(q.items)), format_currency(q.total, q.currency_symbol))
        console.print(table)

        qid = Prompt.ask("\nIngresa el ID de la cotización que deseas editar").strip()
        original_quote = self.history_mgr.get_quote(qid)
        if not original_quote:
            console.print(f"[bold red]No se encontró ninguna cotización con ID '{qid}'.[/bold red]")
            return

        working_items: List[QuoteItem] = copy.deepcopy(original_quote.items)
        working_customer: Customer = copy.deepcopy(original_quote.customer)
        custom_shipping_costs: Dict[str, float] = {
            sd.store_name: sd.shipping_cost for sd in original_quote.shipping_details
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
            self._mostrar_cotizacion_completa(temp_quote)

            console.print("\n[bold green]ACCIONES DISPONIBLES:[/bold green]")
            console.print("  [bold cyan]1.[/bold cyan] ➕ Agregar nuevo componente (Metabuscador o URL)")
            console.print("  [bold cyan]2.[/bold cyan] ✏️  Modificar cantidad de un componente")
            console.print("  [bold cyan]3.[/bold cyan] 🔄 Re-extraer precio actual de un componente (o todos)")
            console.print("  [bold cyan]4.[/bold cyan] ❌ Eliminar un componente")
            console.print("  [bold cyan]5.[/bold cyan] 🚚 Modificar costos de envío por tienda")
            console.print("  [bold cyan]6.[/bold cyan] 💾 [bold green]Guardar como nueva versión (v2, v3...) y generar PDF/HTML/CSV[/bold green]")
            console.print("  [bold cyan]7.[/bold cyan] ↩️  Cancelar y salir sin guardar")

            opc = Prompt.ask("\nSelecciona una acción", choices=["1", "2", "3", "4", "5", "6", "7"], default="1")

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

                self.history_mgr.save_quote(versioned_quote)

                with console.status("[bold green]Generando archivos de la nueva versión (Cliente e Interno)...[/bold green]"):
                    exp_res = self.exporter.export_all(versioned_quote, self.config.business)

                self._mostrar_panel_documentos(exp_res, versioned_quote.quote_id)
                break

            elif opc == "7":
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
        table = Table(title=f"COTIZACIÓN: {quote.quote_id} (v{quote.version})", box=box.HEAVY_EDGE)
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

        summary_table = Table(box=box.SIMPLE, show_header=False)
        summary_table.add_row("Subtotal Componentes:", format_currency(quote.items_subtotal, quote.currency_symbol))
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

    def ver_historial(self):
        console.print("\n[bold cyan]=== HISTORIAL DE COTIZACIONES ===[/bold cyan]")
        quotes = self.history_mgr.load_all_quotes()

        if not quotes:
            console.print("[yellow]No hay cotizaciones guardadas aún.[/yellow]")
            return

        table = Table(box=box.ROUNDED)
        table.add_column("ID Cotización", style="bold cyan")
        table.add_column("Ver.", justify="center", style="dim")
        table.add_column("Fecha", style="dim")
        table.add_column("Cliente", style="white")
        table.add_column("Ítems", justify="center")
        table.add_column("Total (GTQ)", justify="right", style="bold green")

        for q in quotes:
            table.add_row(
                q.quote_id,
                f"v{q.version}",
                q.date,
                q.customer.name,
                str(len(q.items)),
                format_currency(q.total, q.currency_symbol)
            )

        console.print(table)

        if Confirm.ask("\n¿Deseas ver el detalle o re-exportar una cotización?", default=False):
            qid = Prompt.ask("Ingresa el ID de la cotización").strip()
            quote = self.history_mgr.get_quote(qid)
            if quote:
                self._mostrar_cotizacion_completa(quote)
                if Confirm.ask("¿Deseas re-generar los archivos (Cliente + Interno)?", default=False):
                    with console.status("[bold green]Exportando archivos...[/bold green]"):
                        exp_res = self.exporter.export_all(quote, self.config.business)
                    self._mostrar_panel_documentos(exp_res, quote.quote_id)
            else:
                console.print("[red]Cotización no encontrada.[/red]")

    def reverificar_cotizacion(self):
        console.print("\n[bold cyan]=== RE-VERIFICAR PRECIOS DE COTIZACIÓN ===[/bold cyan]")
        quotes = self.history_mgr.load_all_quotes()
        if not quotes:
            console.print("[yellow]No hay cotizaciones registradas para verificar.[/yellow]")
            return

        qid = Prompt.ask("Ingresa el ID de la cotización que deseas re-verificar").strip()
        quote = self.history_mgr.get_quote(qid)
        if not quote:
            console.print("[red]Cotización no encontrada.[/red]")
            return

        console.print(f"\n[cyan]Consultando tiendas para los {len(quote.items)} componentes de {qid}...[/cyan]")
        
        with console.status("[bold yellow]Actualizando precios en tiempo real...[/bold yellow]", spinner="bouncingBar"):
            try:
                updated_quote, changes = self.history_mgr.reverify_quote_prices(qid)
            except Exception as e:
                console.print(f"[bold red]Error al re-verificar:[/bold red] {e}")
                return

        table = Table(title=f"Resultados de Verificación: {qid}", box=box.ROUNDED)
        table.add_column("Componente", style="white")
        table.add_column("Tienda", style="dim")
        table.add_column("Precio Anterior", justify="right")
        table.add_column("Precio Actual", justify="right")
        table.add_column("Diferencia", justify="right")
        table.add_column("Stock", justify="center")

        for c in changes:
            diff_style = "green" if c["diff"] < 0 else ("red" if c["diff"] > 0 else "dim")
            diff_str = f"{c['diff']:+.2f}" if c["diff"] != 0 else "0.00"
            stock_style = "green" if c["in_stock"] else "red"
            
            table.add_row(
                c["product_name"][:35],
                c["store"],
                f"Q {c['old_price']:.2f}",
                f"Q {c['new_price']:.2f}",
                f"[{diff_style}]{diff_str}[/{diff_style}]",
                f"[{stock_style}]{c['stock_status']}[/{stock_style}]"
            )

        console.print(table)
        console.print(f"\n[bold]Total anterior:[/bold] {format_currency(quote.total, quote.currency_symbol)}")
        console.print(f"[bold green]Nuevo Total:[/bold green]   {format_currency(updated_quote.total, updated_quote.currency_symbol)}")

        if Confirm.ask("\n¿Deseas regenerar los documentos (Cliente + Interno) con los precios actualizados?", default=True):
            with console.status("[bold green]Generando archivos actualizados...[/bold green]"):
                exp_res = self.exporter.export_all(updated_quote, self.config.business)
            self._mostrar_panel_documentos(exp_res, updated_quote.quote_id)

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
