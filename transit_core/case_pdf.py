# transit_core/case_pdf.py
from __future__ import annotations

from io import BytesIO
from datetime import datetime
from typing import Optional

import pandas as pd

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def build_case_summary_pdf(
    case_id: str,
    client_name: str,
    client_id: str,
    case_status: str,
    origin: str,
    destination: str,
    drive_folder_id: str,
    items_df: Optional[pd.DataFrame],
    docs_df: Optional[pd.DataFrame],
) -> bytes:
    """
    Genera un PDF simple (tipo reporte) con el resumen del trámite.
    Retorna bytes del PDF.
    """
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    w, h = letter

    left = 40
    y = h - 45
    line_h = 14

    def draw_line(text: str, bold: bool = False):
        nonlocal y
        if y < 60:
            c.showPage()
            y = h - 45
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        c.drawString(left, y, text[:1300])
        y -= line_h

    # Header
    draw_line("RESUMEN DE TRÁMITE (Para revisión Aduanas)", bold=True)
    draw_line(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    draw_line("")

    # Case info
    draw_line("Datos del trámite", bold=True)
    draw_line(f"Trámite (Case ID): {case_id}")
    draw_line(f"Estatus: {case_status}")
    draw_line(f"Cliente: {client_name}  |  ID: {client_id}")
    draw_line(f"Origen: {origin}  |  Destino: {destination}")
    draw_line(f"Drive folder_id: {drive_folder_id}")
    draw_line("")

    # Items
    draw_line("Items registrados (vehículos y artículos)", bold=True)
    if items_df is None or items_df.empty:
        draw_line("No hay items registrados.")
    else:
        # Intentar columnas clave si existen
        cols_pref = [cname for cname in ["item_type", "unique_key", "brand", "model", "year", "quantity", "weight", "value", "description", "item_id"] if cname in items_df.columns]
        if not cols_pref:
            cols_pref = list(items_df.columns)[:10]

        # Recortar filas
        df = items_df.copy()
        df = df.fillna("")
        df = df[cols_pref]

        # Escribir filas
        max_rows = min(len(df), 120)  # para no hacer un pdf infinito
        for i in range(max_rows):
            row = df.iloc[i].to_dict()
            # linea compacta
            parts = []
            for k in cols_pref:
                v = str(row.get(k, "")).strip()
                if v:
                    parts.append(f"{k}={v}")
            draw_line(f"- {i+1}. " + " | ".join(parts))
        if len(df) > max_rows:
            draw_line(f"... ({len(df) - max_rows} filas más omitidas por tamaño)")

    draw_line("")

    # Documents
    draw_line("Documentos del trámite", bold=True)
    if docs_df is None or docs_df.empty:
        draw_line("No hay documentos registrados.")
    else:
        df = docs_df.copy().fillna("")
        cols_pref = [cname for cname in ["doc_id", "doc_type", "file_name", "drive_file_id", "created_at"] if cname in df.columns]
        if not cols_pref:
            cols_pref = list(df.columns)[:8]

        max_rows = min(len(df), 200)
        for i in range(max_rows):
            row = df.iloc[i].to_dict()
            parts = []
            for k in cols_pref:
                v = str(row.get(k, "")).strip()
                if v:
                    parts.append(f"{k}={v}")
            draw_line(f"- {i+1}. " + " | ".join(parts))
        if len(df) > max_rows:
            draw_line(f"... ({len(df) - max_rows} filas más omitidas por tamaño)")

    c.showPage()
    c.save()
    return buffer.getvalue()
