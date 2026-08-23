import sys
import subprocess
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt, FloatPrompt
from rich.text import Text
from rich import box

from src.models import Product, QuoteItem, Quote, Customer
from src.config import AppConfig
from src.scrapers import scrape_product, StoreNotSupportedError, ScraperError
from src.core.calculator import QuoteCalculator, format_currency
from src.core.history_manager import HistoryManager
from src.core.exporter import QuoteExporter

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
        banner_text.append(f"Margen configurado: {self.config.service_fee_percent}% • Moneda: {self.config.currency_code} ({self.config.currency_symbol})", style="bold yellow")
        
        console.print(Panel(banner_text, box=box.ROUNDED, expand=False, border_style="cyan"))

    def run(self):
        while True:
            console.clear()
            self.show_banner()
            console.print("\n[bold green]MENÚ PRINCIPAL[/bold green]")
            console.print("  [bold cyan]1.[/bold cyan] Crear Nueva Cotización")
            console.print("  [bold cyan]2.[/bold cyan] Ver Historial de Cotizaciones")
            console.print("  [bold cyan]3.[/bold cyan] Re-verificar Precios de una Cotización Guardada")
            console.print("  [bold cyan]4.[/bold cyan] Configuración de Margen y Negocio")
            console.print("  [bold cyan]5.[/bold cyan] Salir")

            choice = Prompt.ask("\nSelecciona una opción", choices=["1", "2", "3", "4", "5"], default="1")

            if choice == "1":
                self.crear_nueva_cotizacion()
            elif choice == "2":
                self.ver_historial()
            elif choice == "3":
                self.reverificar_cotizacion()
            elif choice == "4":
                self.configuracion_menu()
            elif choice == "5":
                console.print("\n[bold green]¡Hasta pronto![/bold green] 👋\n")
                sys.exit(0)

            Prompt.ask("\n[dim]Presiona Enter para continuar...[/dim]")

    def crear_nueva_cotizacion(self):
        console.print("\n[bold cyan]=== NUEVA COTIZACIÓN ===[/bold cyan]")
        
        # 1. Datos del cliente
        client_name = Prompt.ask("Nombre del cliente", default="Cliente General")
        client_phone = Prompt.ask("Teléfono / WhatsApp (opcional)", default="")

        customer = Customer(
            name=client_name,
            phone=client_phone
        )

        items: List[QuoteItem] = []

        # 2. Agregar componentes en bucle
        while True:
            console.print("\n[bold yellow]-- Agregar Componente --[/bold yellow]")
            url = Prompt.ask("Pega la URL del producto").strip()

            if not url:
                console.print("[red]La URL no puede estar vacía.[/red]")
                continue

            with console.status("[bold green]Extrayendo datos de la tienda...[/bold green]", spinner="dots"):
                try:
                    product = scrape_product(url)
                except StoreNotSupportedError as e:
                    console.print(f"[bold red]❌ Error de Tienda:[/bold red] {e}")
                    if Confirm.ask("¿Deseas intentar con otra URL?", default=True):
                        continue
                    else:
                        break
                except Exception as e:
                    console.print(f"[bold red]❌ Error al extraer producto:[/bold red] {e}")
                    if Confirm.ask("¿Deseas intentar con otra URL?", default=True):
                        continue
                    else:
                        break

            # Mostrar producto extraído
            p_table = Table(box=box.SIMPLE, show_header=False)
            p_table.add_row("Producto:", f"[bold white]{product.name}[/bold white]")
            p_table.add_row("Tienda:", f"[cyan]{product.store_name}[/cyan]")
            p_table.add_row("Precio Unitario:", f"[bold green]{format_currency(product.unit_price, self.config.currency_symbol)}[/bold green]")
            
            stock_style = "green" if product.in_stock else "red"
            p_table.add_row("Disponibilidad:", f"[{stock_style}]{product.stock_status}[/{stock_style}]")

            console.print(Panel(p_table, title="[bold]Componente Detectado[/bold]", border_style="green"))

            if not product.in_stock:
                console.print("[bold yellow]⚠️ Advertencia: Este producto aparece sin stock o agotado en la tienda.[/bold yellow]")
                if not Confirm.ask("¿Deseas agregarlo a la cotización de todas formas?", default=False):
                    continue

            # Pedir cantidad
            qty = IntPrompt.ask("Cantidad deseada", default=1)
            while qty <= 0:
                console.print("[red]La cantidad debe ser al menos 1.[/red]")
                qty = IntPrompt.ask("Cantidad deseada", default=1)

            quote_item = QuoteCalculator.create_quote_item(product, qty)
            items.append(quote_item)
            console.print(f"[green]✔ Agregado:[/green] {qty}x {product.name} = [bold]{format_currency(quote_item.subtotal, self.config.currency_symbol)}[/bold]")

            # Mostrar tabla actual de items
            self._mostrar_resumen_items(items)

            if not Confirm.ask("\n¿Deseas agregar otro componente?", default=True):
                break

        if not items:
            console.print("[yellow]No se agregaron componentes. Cancelando cotización.[/yellow]")
            return

        # 3. Margen de servicio
        margin = self.config.service_fee_percent
        if Confirm.ask(f"\n¿Deseas usar el margen de compra predeterminado de {margin}%?", default=True):
            fee_percent = margin
        else:
            fee_percent = FloatPrompt.ask("Ingresa el porcentaje de margen deseado (%)", default=margin)

        # 4. Construir cotización
        quote_id = self.history_mgr.get_next_quote_id(self.config.quote_prefix)
        quote = QuoteCalculator.build_quote(
            quote_id=quote_id,
            items=items,
            customer=customer,
            service_fee_percent=fee_percent,
            validity_days=self.config.validity_days,
            currency_symbol=self.config.currency_symbol,
            currency_code=self.config.currency_code
        )

        # 5. Mostrar resumen final
        self._mostrar_cotizacion_completa(quote)

        # 6. Confirmar y exportar
        if Confirm.ask("\n¿Deseas guardar la cotización y generar los documentos (HTML, PDF, CSV)?", default=True):
            self.history_mgr.save_quote(quote)
            with console.status("[bold green]Generando archivos PDF, HTML y CSV...[/bold green]", spinner="dots"):
                csv_path, html_path, pdf_path = self.exporter.export_all(quote, self.config.business)

            console.print(Panel(
                f"[bold green]✔ Cotización guardada con éxito![/bold green]\n\n"
                f"📄 [bold]ID Cotización:[/bold] {quote.quote_id}\n"
                f"📊 [bold]CSV (Control interno):[/bold] {csv_path}\n"
                f"🌐 [bold]HTML (Cliente):[/bold]        {html_path}\n"
                f"📑 [bold]PDF (Cliente):[/bold]         {pdf_path if pdf_path else 'No generado (usa HTML)'}",
                title="[bold cyan]Documentos Generados[/bold cyan]",
                border_style="green"
            ))

            if pdf_path and Confirm.ask("¿Deseas abrir el archivo PDF ahora?", default=True):
                try:
                    subprocess.Popen(["xdg-open", str(pdf_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
            elif html_path and Confirm.ask("¿Deseas abrir el archivo HTML en el navegador?", default=False):
                try:
                    subprocess.Popen(["xdg-open", str(html_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

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
        console.print(f"[bold]Subtotal acumulado:[/bold] [green]{format_currency(subtotal_sum, self.config.currency_symbol)}[/green]")

    def _mostrar_cotizacion_completa(self, quote: Quote):
        table = Table(title=f"COTIZACIÓN: {quote.quote_id}", box=box.HEAVY_EDGE)
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
        summary_table.add_row("Subtotal Componentes:", format_currency(quote.subtotal, quote.currency_symbol))
        summary_table.add_row(f"Cargo por Gestión/Servicio ({quote.service_fee_percent}%):", format_currency(quote.service_fee_amount, quote.currency_symbol))
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
        table.add_column("Fecha", style="dim")
        table.add_column("Cliente", style="white")
        table.add_column("Ítems", justify="center")
        table.add_column("Total (GTQ)", justify="right", style="bold green")

        for q in quotes:
            table.add_row(
                q.quote_id,
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
                if Confirm.ask("¿Deseas re-generar los archivos (PDF/HTML/CSV)?", default=False):
                    with console.status("[bold green]Exportando...[/bold green]"):
                        csv_p, html_p, pdf_p = self.exporter.export_all(quote, self.config.business)
                    console.print(f"[green]✔ Archivos re-generados en {self.exporter.output_dir}[/green]")
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

        # Mostrar tabla comparativa
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

        if Confirm.ask("\n¿Deseas regenerar los documentos PDF/HTML con los precios actualizados?", default=True):
            with console.status("[bold green]Generando archivos actualizados...[/bold green]"):
                self.exporter.export_all(updated_quote, self.config.business)
            console.print("[bold green]✔ Cotización actualizada y exportada con éxito.[/bold green]")

    def configuracion_menu(self):
        console.print("\n[bold cyan]=== CONFIGURACIÓN DE PARÁMETROS ===[/bold cyan]")
        console.print(f"1. Margen de compra predeterminado: [bold green]{self.config.service_fee_percent}%[/bold green]")
        console.print(f"2. Vigencia de cotización: [bold green]{self.config.validity_days} días[/bold green]")
        console.print(f"3. Nombre de negocio: [bold white]{self.config.business.name}[/bold white]")
        console.print(f"4. Teléfono/WhatsApp: [bold white]{self.config.business.phone}[/bold white]")
        console.print(f"5. Email: [bold white]{self.config.business.email}[/bold white]")
        console.print(f"6. Términos de pago: [dim]{self.config.business.payment_terms}[/dim]")

        if Confirm.ask("\n¿Deseas editar algún valor de la configuración?", default=False):
            self.config.service_fee_percent = FloatPrompt.ask("Nuevo margen predeterminado (%)", default=self.config.service_fee_percent)
            self.config.validity_days = IntPrompt.ask("Nueva vigencia en días", default=self.config.validity_days)
            self.config.business.name = Prompt.ask("Nombre de tu negocio", default=self.config.business.name)
            self.config.business.phone = Prompt.ask("Teléfono / WhatsApp", default=self.config.business.phone)
            self.config.business.email = Prompt.ask("Email de contacto", default=self.config.business.email)
            self.config.business.payment_terms = Prompt.ask("Términos de pago", default=self.config.business.payment_terms)

            self.config.save()
            console.print("[bold green]✔ Configuración guardada exitosamente.[/bold green]")
