# transit_core/pdf_builder.py
from __future__ import annotations

from io import BytesIO
from datetime import datetime
from typing import Any, Dict, Optional, List

import pandas as pd

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics


def _safe(x: Any) -> str:
    return ("" if x is None else str(x)).strip()


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _wrap_lines(text: str, font_name: str, font_size: int, max_width: float) -> List[str]:
    """
    Wrap simple por ancho en puntos para ReportLab.
    """
    t = _safe(text)
    if not t:
        return [""]

    words = t.split()
    lines: List[str] = []
    cur = ""

    for w in words:
        candidate = w if not cur else f"{cur} {w}"
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = w

    if cur:
        lines.append(cur)
    return lines if lines else [""]


def build_case_summary_pdf_bytes(
    case: Dict[str, Any],
    client: Dict[str, Any],
    vehicles_df: Optional[pd.DataFrame],
    articles_df: Optional[pd.DataFrame],
    documents_df: Optional[pd.DataFrame],
) -> bytes:
    """
    PDF resumen estilo aduana.
    - Vehículos: numerados + campos principales
    - Artículos: numerados + SOLO descripción (sin repetir)
    - Documentos: numerados + doc_type + file_name (wrap)
    """

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER

    left = 0.75 * inch
    right = width - 0.75 * inch
    usable_w = right - left

    y = height - 0.75 * inch
    line_h = 13

    def ensure_space(min_y=1.0 * inch):
        nonlocal y
        if y < min_y:
            c.showPage()
            y = height - 0.75 * inch

    def hr():
        nonlocal y
        y -= 6
        c.line(left, y, right, y)
        y -= 14

    def write_line(text: str, size=10, bold=False, extra_space=0):
        nonlocal y
        ensure_space()
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(left, y, text)
        y -= (line_h + extra_space)

    def write_wrapped(text: str, size=10, bold=False, indent=0):
        nonlocal y
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, size)
        max_w = usable_w - indent
        lines = _wrap_lines(text, font, size, max_w)
        for ln in lines:
            ensure_space()
            c.drawString(left + indent, y, ln)
            y -= line_h

    # -------------------------
    # Header
    # -------------------------
    case_id = _safe(case.get("case_id"))
    client_name = _safe(client.get("name"))

    write_line("RESUMEN DEL TRÁMITE", size=14, bold=True, extra_space=4)
    write_line(f"Trámite: {case_id}")
    write_line(f"Cliente: {client_name}")

    origin = _safe(case.get("origin"))
    destination = _safe(case.get("destination"))
    status = _safe(case.get("status"))
    created_at = _safe(case.get("created_at"))
    updated_at = _safe(case.get("updated_at"))

    write_line(f"Origen: {origin}    Destino: {destination}    Estatus: {status}")
    write_line(f"Generado: {_ts()}    Creado: {created_at}    Actualizado: {updated_at}")

    hr()

    # -------------------------
    # 1) Vehículos
    # -------------------------
    write_line("1) Vehículos", bold=True, size=12, extra_space=2)

    if vehicles_df is None or vehicles_df.empty:
        write_line("(sin vehículos)")
    else:
        vdf = vehicles_df.copy().fillna("").reset_index(drop=True)
        for i, row in vdf.iterrows():
            ensure_space(1.25 * inch)
            no = i + 1

            vin = _safe(row.get("vin"))
            brand = _safe(row.get("brand"))
            model = _safe(row.get("model"))
            year = _safe(row.get("year"))
            trim = _safe(row.get("trim"))
            engine = _safe(row.get("engine"))
            vtype = _safe(row.get("vehicle_type"))
            body = _safe(row.get("body_class"))
            plant = _safe(row.get("plant_country"))
            gvwr = _safe(row.get("gvwr"))
            peso = _safe(row.get("weight"))
            desc = _safe(row.get("description"))
            created_item = _safe(row.get("created_at"))

            write_line(f"Vehículo #{no}: VIN: {vin}", bold=True)
            write_line(f"Marca/Modelo/Año: {brand} {model} {year}".strip())

            if trim: write_line(f"Trim: {trim}")
            if engine: write_line(f"Motor: {engine}")
            if vtype: write_line(f"Tipo: {vtype}")
            if body: write_line(f"Carrocería: {body}")
            if plant: write_line(f"País planta: {plant}")
            if gvwr: write_wrapped(f"GVWR: {gvwr}", size=10, indent=0)
            if peso: write_line(f"Peso (opcional): {peso}")

            if desc:
                write_wrapped(f"Nota/Descripción: {desc}", size=10, indent=0)

            if created_item:
                write_line(f"Registrado: {created_item}")

            y -= 4

    hr()

    # -------------------------
    # 2) Artículos / Items
    # -------------------------
    write_line("2) Artículos / Items", bold=True, size=12, extra_space=2)

    if articles_df is None or articles_df.empty:
        write_line("(sin artículos)")
    else:
        adf = articles_df.copy().fillna("").reset_index(drop=True)
        for i, row in adf.iterrows():
            ensure_space(1.25 * inch)
            no = i + 1

            seq = _safe(row.get("seq"))
            desc = _safe(row.get("description"))
            created_item = _safe(row.get("created_at"))

            # ✅ Identificación clara
            write_line(f"Artículo #{no}: {seq}".strip(), bold=True)

            # ✅ SOLO descripción (sin repetir)
            if desc:
                write_wrapped(f"Descripción: {desc}", size=10, indent=0)
            else:
                write_line("Descripción: (sin descripción)")

            if created_item:
                write_line(f"Registrado: {created_item}")

            y -= 4

    hr()

    # -------------------------
    # 3) Documentos del trámite
    # -------------------------
    write_line("3) Documentos del trámite", bold=True, size=12, extra_space=2)

    if documents_df is None or documents_df.empty:
        write_line("(sin documentos)")
    else:
        ddf = documents_df.copy().fillna("").reset_index(drop=True)
        for i, row in ddf.iterrows():
            ensure_space(1.25 * inch)
            no = i + 1

            doc_type = _safe(row.get("doc_type"))
            file_name = _safe(row.get("file_name"))
            uploaded_at = _safe(row.get("uploaded_at"))
            drive_file_id = _safe(row.get("drive_file_id"))

            # ✅ Identifica tipo explícito: ID_CLIENTE, etc.
            write_wrapped(f"Documento #{no}: [{doc_type}] {file_name}", size=10, bold=True, indent=0)
            if uploaded_at:
                write_line(f"Subido: {uploaded_at}")
            if drive_file_id:
                write_wrapped(f"Drive file_id: {drive_file_id}", size=9, indent=0)

            y -= 2

    c.showPage()
    c.save()
    return buf.getvalue()
