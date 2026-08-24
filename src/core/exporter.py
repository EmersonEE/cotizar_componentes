import csv
import os
from pathlib import Path
from typing import Optional, Tuple
from jinja2 import Environment, FileSystemLoader
from src.models import Quote, BusinessInfo
from src.config import AppConfig

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"

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
            writer.writerow(["CLIENTE", quote.customer.name, "TEL", quote.customer.phone])
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

    def export_html(self, quote: Quote, business: Optional[BusinessInfo] = None) -> Path:
        """Renders the HTML quote using Jinja2."""
        if business is None:
            config = AppConfig.load()
            business = config.business

        template = self.jinja_env.get_template("quote_template.html")
        rendered_html = template.render(
            quote=quote,
            business=business
        )

        file_path = self.html_dir / f"{quote.quote_id}.html"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(rendered_html)

        return file_path

    def export_pdf(self, quote: Quote, business: Optional[BusinessInfo] = None) -> Optional[Path]:
        """Generates PDF using WeasyPrint from the generated HTML."""
        html_path = self.export_html(quote, business)
        pdf_path = self.pdf_dir / f"{quote.quote_id}.pdf"

        try:
            from weasyprint import HTML
            HTML(filename=str(html_path)).write_pdf(str(pdf_path))
            return pdf_path
        except Exception as e:
            print(f"[Aviso] No se pudo generar PDF automáticamente con WeasyPrint: {e}")
            return None

    def export_all(self, quote: Quote, business: Optional[BusinessInfo] = None) -> Tuple[Path, Path, Optional[Path]]:
        """Generates CSV, HTML and PDF for a quote."""
        csv_file = self.export_csv(quote)
        html_file = self.export_html(quote, business)
        pdf_file = self.export_pdf(quote, business)
        return csv_file, html_file, pdf_file
