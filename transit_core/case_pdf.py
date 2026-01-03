# transit_core/case_pdf.py
from __future__ import annotations

from typing import Dict, Any, List
from io import BytesIO
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas


def _safe(s: Any) -> str:
    return ("" if s is None else str(s)).strip()


def build_case_summary_pdf(
    case: Dict[str, Any],
    client_name: str,
    vehicles: List[Dict[str, Any]],
    articles: List[Dict[str, Any]],
    documents: List[Dict[str, Any]],
) -> bytes:
    """
    PDF simple tipo "cabecera + datos + partidas/items" (inspirado en tu ejemplo).
    """
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter

    left = 0.65 * inch
    right = w - 0.65 * inch
    y = h - 0.75 * inch

    case_id = _safe(case.get("case_id"))
    status = _safe(case.get("status"))
    origin = _safe(case.get("origin"))
    destination = _safe(case.get("destination"))
    created_at = _safe(case.get("created_at"))
    updated_at = _safe(case.get("updated_at"))

    # Header
    c.setFont("Helvetica-Bold", 14)
    c.drawString(left, y, "RESUMEN DEL TRÁMITE")
    y -= 18

    c.setFont("Helvetica", 10)
    c.drawString(left, y, f"Trámite: {case_id}")
    y -= 14
    c.drawString(left, y, f"Cliente: {client_name}")
    y -= 14
    c.drawString(left, y, f"Origen: {origin}    Destino: {destination}    Estatus: {status}")
    y -= 14
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.drawString(left, y, f"Generado: {now}    Creado: {created_at}    Actualizado: {updated_at}")
    y -= 18

    # Divider
    c.line(left, y, right, y)
    y -= 18

    def section(title: str):
        nonlocal y
        if y < 1.25 * inch:
            c.showPage()
            y = h - 0.75 * inch
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left, y, title)
        y -= 14
        c.setFont("Helvetica", 9)

    def row(label: str, value: str):
        nonlocal y
        if y < 1.05 * inch:
            c.showPage()
            y = h - 0.75 * inch
            c.setFont("Helvetica", 9)
        c.drawString(left, y, f"{label}: {value}")
        y -= 12

    # Vehicles
    section("1) Vehículos")
    if not vehicles:
        row("-", "No hay vehículos registrados.")
    else:
        for i, v in enumerate(vehicles, start=1):
            vin = _safe(v.get("unique_key") or v.get("vin") or "")
            desc = _safe(v.get("description"))
            brand = _safe(v.get("brand"))
            model = _safe(v.get("model"))
            year = _safe(v.get("year"))
            trim = _safe(v.get("trim"))
            engine = _safe(v.get("engine"))
            vtype = _safe(v.get("vehicle_type"))
            body = _safe(v.get("body_class"))
            plant = _safe(v.get("plant_country"))
            gvwr = _safe(v.get("gvwr"))
            curb = _safe(v.get("curb_weight"))
            weight = _safe(v.get("weight"))

            row(f"Vehículo #{i}", f"VIN: {vin}")
            row("  Marca/Modelo/Año", f"{brand} {model} {year}".strip())
            if trim: row("  Trim", trim)
            if engine: row("  Motor", engine)
            if vtype: row("  Tipo", vtype)
            if body: row("  Carrocería", body)
            if plant: row("  País planta", plant)
            if gvwr: row("  GVWR", gvwr)
            if curb: row("  Curb weight", curb)
            if weight: row("  Peso (opcional)", weight)
            if desc: row("  Nota", desc)
            y -= 6

    y -= 6
    c.line(left, y, right, y)
    y -= 14

    # Articles
    section("2) Artículos / Items")
    if not articles:
        row("-", "No hay artículos registrados.")
    else:
        for i, a in enumerate(articles, start=1):
            desc = _safe(a.get("description"))
            qty = _safe(a.get("quantity"))
            weight = _safe(a.get("weight"))
            value = _safe(a.get("value"))
            row(f"Item #{i}", desc or "(sin descripción)")
            if qty: row("  Cantidad", qty)
            if weight: row("  Peso", weight)
            if value: row("  Valor", value)
            y -= 6

    y -= 6
    c.line(left, y, right, y)
    y -= 14

    # Documents
    section("3) Documentos del trámite")
    if not documents:
        row("-", "No hay documentos subidos.")
    else:
        for i, d in enumerate(documents, start=1):
            dt = _safe(d.get("doc_type"))
            fn = _safe(d.get("file_name"))
            row(f"Documento #{i}", f"[{dt}] {fn}".strip())
        y -= 6

    c.showPage()
    c.save()
    return buf.getvalue()
