# transit_core/pdf_generator.py
from __future__ import annotations
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

def generate_case_pdf(case: dict, client: dict, items_df, documents_df) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter

    y = h - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "Transit - Exportación / Tránsito")
    y -= 25

    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"Trámite: {case.get('case_id','')}   Fecha: {case.get('case_date','')}   Estatus: {case.get('status','')}")
    y -= 18
    c.drawString(50, y, f"Origen: {case.get('origin','')}   Destino: {case.get('destination','')}")
    y -= 22

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Datos del Cliente")
    y -= 16
    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"Nombre: {client.get('name','')}")
    y -= 14
    c.drawString(50, y, f"Dirección: {client.get('address','')}")
    y -= 14
    c.drawString(50, y, f"Identificación: {client.get('id_type','')} {client.get('id_number','')}")
    y -= 14
    c.drawString(50, y, f"Tel: {client.get('phone','')}   Email: {client.get('email','')}")
    y -= 22

    # Vehículos
    vehicles = items_df[items_df["item_type"] == "vehicle"] if not items_df.empty else items_df
    articles = items_df[items_df["item_type"] == "article"] if not items_df.empty else items_df

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Vehículos")
    y -= 16
    c.setFont("Helvetica", 9)
    if vehicles is None or vehicles.empty:
        c.drawString(50, y, "Sin vehículos.")
        y -= 14
    else:
        for _, r in vehicles.iterrows():
            line = f"VIN: {r.get('unique_key','')} | {r.get('year','')} {r.get('brand','')} {r.get('model','')} | Peso: {r.get('weight','')}"
            c.drawString(50, y, line[:120])
            y -= 12
            if y < 80:
                c.showPage()
                y = h - 50
                c.setFont("Helvetica", 9)

    y -= 10
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Artículos")
    y -= 16
    c.setFont("Helvetica", 9)
    if articles is None or articles.empty:
        c.drawString(50, y, "Sin artículos.")
        y -= 14
    else:
        for _, r in articles.iterrows():
            line = f"ID: {r.get('unique_key','')} | {r.get('description','')} | Marca: {r.get('brand','')} | Peso: {r.get('weight','')}"
            c.drawString(50, y, line[:120])
            y -= 12
            if y < 80:
                c.showPage()
                y = h - 50
                c.setFont("Helvetica", 9)

    y -= 10
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Documentos adjuntos")
    y -= 16
    c.setFont("Helvetica", 9)
    if documents_df is None or documents_df.empty:
        c.drawString(50, y, "Sin documentos.")
        y -= 14
    else:
        for _, r in documents_df.iterrows():
            line = f"{r.get('doc_type','')}: {r.get('file_name','')}"
            c.drawString(50, y, line[:120])
            y -= 12
            if y < 80:
                c.showPage()
                y = h - 50
                c.setFont("Helvetica", 9)

    c.showPage()
    c.save()
    return buf.getvalue()

