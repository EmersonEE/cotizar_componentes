import csv
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Iterator
from jinja2 import Environment, FileSystemLoader
from src.models import Quote, BusinessInfo
from src.config import AppConfig

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"

@dataclass
class ExportResult:
    csv: Path
    client_html: Path
    client_pdf: Optional[Path]
    internal_html: Path
    internal_pdf: Optional[Path]

    def __iter__(self) -> Iterator:
        """Enables backward-compatible unpacking: csv, html, pdf = exporter.export_all(...)"""
        yield self.csv
        yield self.client_html
        yield self.client_pdf

class QuoteExporter:
    def __init__(self, output_dir: Path = OUTPUT_DIR, templates_dir: Path = TEMPLATES_DIR):
        self.output_dir = output_dir
        self.templates_dir = templates_dir
        self.jinja_env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
        
        self.html_dir = output_dir / "quotes_html"
        self.pdf_dir = output_dir / "quotes_pdf"
        self.csv_dir = output_dir / "quotes_csv"

        self._ensure_directories()

    def _ensure_directories(self):
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.csv_dir.mkdir(parents=True, exist_ok=True)

    def export_csv(self, quote: Quote) -> Path:
        """Exports quote details to a CSV file for internal records."""
        file_path = self.csv_dir / f"{quote.quote_id}.csv"
        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            # Metadata header
            writer.writerow(["COTIZACIÓN", quote.quote_id, "VERSIÓN", quote.version, "FECHA", quote.date, "VIGENCIA", quote.valid_until])
            writer.writerow(["CLIENTE", quote.customer.name, "TEL", quote.customer.phone or "N/A", "EMAIL", quote.customer.email or "N/A"])
            if quote.customer.notes:
                writer.writerow(["NOTAS CLIENTE", quote.customer.notes])
            writer.writerow([])
            # Items table
            writer.writerow(["#", "Componente", "Tienda", "Cantidad", "Precio Unitario (GTQ)", "Subtotal (GTQ)", "URL Origen", "Stock"])
            for idx, item in enumerate(quote.items, start=1):
                writer.writerow([
                    idx,
                    item.product.name,
                    item.product.store_name,
                    item.quantity,
                    f"{item.unit_price:.2f}",
                    f"{item.subtotal:.2f}",
                    item.product.url,
                    item.product.stock_status
                ])
            writer.writerow([])
            # Financial summary
            writer.writerow(["", "", "", "", "SUBTOTAL COMPONENTES:", f"{quote.items_subtotal:.2f}"])
            writer.writerow(["", "", "", "", f"SERVICIO COMPRA ({quote.service_fee_percent}%):", f"{quote.service_fee_amount:.2f}"])
            
            # Shipping breakdown
            if quote.shipping_details:
                for sd in quote.shipping_details:
                    cost_val = sd.status_label if (sd.qualifies_free or sd.is_pickup_only) else f"{sd.shipping_cost:.2f}"
                    writer.writerow(["", "", "", "", f"ENVÍO {sd.store_name}:", cost_val])
            
            writer.writerow(["", "", "", "", "TOTAL GENERAL:", f"{quote.total:.2f}"])

        return file_path

    def render_html_string(self, quote: Quote, business: Optional[BusinessInfo] = None, is_internal: bool = False) -> str:
        """Renders the HTML template into a string."""
        if business is None:
            config = AppConfig.load()
            business = config.business

        template = self.jinja_env.get_template("quote_template.html")
        return template.render(
            quote=quote,
            business=business,
            is_internal=is_internal
        )

    def export_html(self, quote: Quote, business: Optional[BusinessInfo] = None, is_internal: bool = False) -> Path:
        """Renders and saves the HTML quote to disk."""
        rendered_html = self.render_html_string(quote, business, is_internal=is_internal)
        suffix = "_Interna" if is_internal else "_Cliente"
        file_path = self.html_dir / f"{quote.quote_id}{suffix}.html"
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(rendered_html)

        # Also maintain a default {quote_id}.html alias for client version
        if not is_internal:
            default_path = self.html_dir / f"{quote.quote_id}.html"
            with open(default_path, "w", encoding="utf-8") as f:
                f.write(rendered_html)

        return file_path

    def export_pdf(self, quote: Quote, business: Optional[BusinessInfo] = None, is_internal: bool = False) -> Optional[Path]:
        """Generates PDF using WeasyPrint from HTML."""
        html_path = self.export_html(quote, business, is_internal=is_internal)
        suffix = "_Interna" if is_internal else "_Cliente"
        pdf_path = self.pdf_dir / f"{quote.quote_id}{suffix}.pdf"

        try:
            from weasyprint import HTML
            HTML(filename=str(html_path)).write_pdf(str(pdf_path))
            
            # Also maintain a default {quote_id}.pdf alias for client version
            if not is_internal:
                default_pdf = self.pdf_dir / f"{quote.quote_id}.pdf"
                try:
                    import shutil
                    shutil.copy2(pdf_path, default_pdf)
                except Exception:
                    pass

            return pdf_path
        except Exception as e:
            print(f"[Aviso] No se pudo generar PDF automáticamente con WeasyPrint: {e}")
            return None

    def export_all(self, quote: Quote, business: Optional[BusinessInfo] = None) -> ExportResult:
        """
        Generates both Client and Internal versions simultaneously:
        - CSV file for internal tracking
        - Client HTML & PDF (clean without external links)
        - Internal HTML & PDF (with clickable shop hyperlinks)
        """
        csv_file = self.export_csv(quote)
        
        client_html = self.export_html(quote, business, is_internal=False)
        client_pdf = self.export_pdf(quote, business, is_internal=False)
        
        internal_html = self.export_html(quote, business, is_internal=True)
        internal_pdf = self.export_pdf(quote, business, is_internal=True)

        return ExportResult(
            csv=csv_file,
            client_html=client_html,
            client_pdf=client_pdf,
            internal_html=internal_html,
            internal_pdf=internal_pdf
        )
