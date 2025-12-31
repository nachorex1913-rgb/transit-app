# transit_core/pdf_generator.py
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas


def generate_case_pdf(
    case: dict[str, Any],
    client: dict[str, Any] | None,
    items_df,
    documents_df,
) -> bytes:
    """
    Genera un PDF simple (MVP) y devuelve bytes.
    """
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    w, h = LETTER

    y = h - 40
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "Transit — PDF Final")
    y -= 20

    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Generado: {datetime.utcnow().isoformat(timespec='seconds')}Z")
    y -= 18

    case_id = case.get("case_id", "")
    c.drawString(40, y, f"Trámite: {case_id}")
    y -= 14

    if client:
        c.drawString(40, y, f"Cliente: {client.get('name','')}")
        y -= 14
        c.drawString(40, y, f"Tel: {client.get('phone','')}  Email: {client.get('email','')}")
        y -= 14

    c.drawString(40, y, f"Origen: {case.get('origin','')}  Destino: {case.get('destination','')}")
    y -= 14
    c.drawString(40, y, f"Estatus: {case.get('status','')}")
    y -= 18

    # Items (resumen)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Items")
    y -= 14
    c.setFont("Helvetica", 9)

    if items_df is None or getattr(items_df, "empty", True):
        c.drawString(40, y, "— Sin items —")
        y -= 12
    else:
        cols = [c for c in ["item_type", "unique_key", "brand", "model", "year", "description", "quantity", "weight", "value"] if c in items_df.columns]
        rows = items_df[cols].fillna("").to_dict("records")[:25]  # limita para MVP

        for r in rows:
            line = f"{r.get('item_type','')} | {r.get('unique_key','')} | {r.get('brand','')} {r.get('model','')} {r.get('year','')} | qty:{r.get('quantity','')} wt:{r.get('weight','')} val:{r.get('value','')}"
            if y < 60:
                c.showPage()
                y = h - 40
                c.setFont("Helvetica", 9)
            c.drawString(40, y, line[:120])
            y -= 11

    # Documents (resumen)
    y -= 8
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Documentos registrados")
    y -= 14
    c.setFont("Helvetica", 9)

    if documents_df is None or getattr(documents_df, "empty", True):
        c.drawString(40, y, "— Sin documentos —")
        y -= 12
    else:
        cols = [c for c in ["doc_type", "file_name", "drive_file_id", "uploaded_at"] if c in documents_df.columns]
        rows = documents_df[cols].fillna("").to_dict("records")[:25]

        for r in rows:
            line = f"{r.get('doc_type','')} | {r.get('file_name','')} | {r.get('uploaded_at','')}"
            if y < 60:
                c.showPage()
                y = h - 40
                c.setFont("Helvetica", 9)
            c.drawString(40, y, line[:120])
            y -= 11

    c.showPage()
    c.save()

    return buf.getvalue()
