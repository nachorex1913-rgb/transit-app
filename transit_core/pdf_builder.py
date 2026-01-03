# transit_core/pdf_builder.py
from __future__ import annotations

from io import BytesIO
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas


def _safe(x: Any) -> str:
    return ("" if x is None else str(x)).strip()


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_case_summary_pdf_bytes(
    case: Dict[str, Any],
    client: Dict[str, Any],
    vehicles_df: Optional[pd.DataFrame],
    articles_df: Optional[pd.DataFrame],
    documents_df: Optional[pd.DataFrame],
) -> bytes:
    """
    Genera PDF (bytes) con el resumen del trámite en un formato tipo “aduana”.
    - vehicles_df: items filtrados item_type == 'vehicle'
    - articles_df: items filtrados item_type == 'article'
    - documents_df: documentos del case
    """

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER

    left = 0.75 * inch
    right = width - 0.75 * inch
    y = height - 0.75 * inch
    line_h = 14

    def hr():
        nonlocal y
        y -= 6
        c.line(left, y, right, y)
        y -= 14

    def ensure_space(min_y=1.0 * inch):
        nonlocal y
        if y < min_y:
            c.showPage()
            y = height - 0.75 * inch

    def write(text: str, size=10, bold=False, extra_space=0):
        nonlocal y
        ensure_space()
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(left, y, text)
        y -= (line_h + extra_space)

    # -------------------------
    # Header
    # -------------------------
    case_id = _safe(case.get("case_id"))
    client_name = _safe(client.get("name"))

    write("RESUMEN DEL TRÁMITE", size=14, bold=True, extra_space=4)

    write(f"Trámite: {case_id}")
    write(f"Cliente: {client_name}")

    origin = _safe(case.get("origin"))
    destination = _safe(case.get("destination"))
    status = _safe(case.get("status"))
    created_at = _safe(case.get("created_at"))
    updated_at = _safe(case.get("updated_at"))

    write(f"Origen: {origin}    Destino: {destination}    Estatus: {status}")
    write(f"Generado: {_ts()}    Creado: {created_at}    Actualizado: {updated_at}")

    hr()

    # -------------------------
    # 1) Vehículos
    # -------------------------
    write("1) Vehículos", bold=True, size=12, extra_space=2)

    if vehicles_df is None or vehicles_df.empty:
        write("(sin vehículos)")
    else:
        vdf = vehicles_df.copy().fillna("")
        vdf = vdf.reset_index(drop=True)

        for i, row in vdf.iterrows():
            ensure_space(1.25 * inch)
            no = i + 1

            vin = _safe(row.get("unique_key"))
            brand = _safe(row.get("brand"))
            model = _safe(row.get("model"))
            year = _safe(row.get("year"))
            desc = _safe(row.get("description"))
            source = _safe(row.get("source"))
            created_item = _safe(row.get("created_at"))

            # campos extra si existen en tu sheet
            trim = _safe(row.get("trim")) if "trim" in vdf.columns else ""
            engine = _safe(row.get("engine")) if "engine" in vdf.columns else ""
            vtype = _safe(row.get("vehicle_type")) if "vehicle_type" in vdf.columns else ""
            body = _safe(row.get("body_class")) if "body_class" in vdf.columns else ""
            plant = _safe(row.get("plant_country")) if "plant_country" in vdf.columns else ""
            gvwr = _safe(row.get("gvwr")) if "gvwr" in vdf.columns else ""
            curb = _safe(row.get("curb_weight")) if "curb_weight" in vdf.columns else ""
            peso = _safe(row.get("weight"))  # “Peso (opcional)” texto libre

            write(f"Vehículo #{no}: VIN: {vin}", bold=True)
            write(f"Marca/Modelo/Año: {brand} {model} {year}")
            if trim:
                write(f"Trim: {trim}")
            if engine:
                write(f"Motor: {engine}")
            if vtype:
                write(f"Tipo: {vtype}")
            if body:
                write(f"Carrocería: {body}")
            if plant:
                write(f"País planta: {plant}")
            if gvwr:
                write(f"GVWR: {gvwr}")
            if curb:
                write(f"Curb weight: {curb}")
            if peso:
                write(f"Peso (opcional): {peso}")
            if desc:
                write(f"Nota/Descripción: {desc}")
            if source:
                write(f"Fuente: {source}")
            if created_item:
                write(f"Registrado: {created_item}")

            y -= 4

    hr()

    # -------------------------
    # 2) Artículos / Items
    # -------------------------
    write("2) Artículos / Items", bold=True, size=12, extra_space=2)

    if articles_df is None or articles_df.empty:
        write("(sin artículos)")
    else:
        adf = articles_df.copy().fillna("")
        adf = adf.reset_index(drop=True)

        for i, row in adf.iterrows():
            ensure_space(1.25 * inch)
            no = i + 1

            uk = _safe(row.get("unique_key"))  # A-CASE-0001
            brand = _safe(row.get("brand"))
            model = _safe(row.get("model"))
            qty = _safe(row.get("quantity"))
            weight = _safe(row.get("weight"))
            value = _safe(row.get("value"))
            desc = _safe(row.get("description"))
            created_item = _safe(row.get("created_at"))

            write(f"Item #{no}: {uk}", bold=True)
            if desc:
                write(f"Descripción: {desc}")
            else:
                write("Descripción: (sin descripción)")

            line = []
            if brand:
                line.append(f"Marca: {brand}")
            if model:
                line.append(f"Modelo: {model}")
            if qty:
                line.append(f"Cantidad: {qty}")
            if weight:
                line.append(f"Peso: {weight}")
            if value:
                line.append(f"Valor: {value}")

            if line:
                write(" | ".join(line))

            if created_item:
                write(f"Registrado: {created_item}")

            y -= 4

    hr()

    # -------------------------
    # 3) Documentos del trámite
    # -------------------------
    write("3) Documentos del trámite", bold=True, size=12, extra_space=2)

    if documents_df is None or documents_df.empty:
        write("(sin documentos)")
    else:
        ddf = documents_df.copy().fillna("")
        ddf = ddf.reset_index(drop=True)

        for i, row in ddf.iterrows():
            ensure_space(1.25 * inch)
            no = i + 1
            doc_type = _safe(row.get("doc_type"))
            file_name = _safe(row.get("file_name"))
            uploaded_at = _safe(row.get("uploaded_at"))
            drive_file_id = _safe(row.get("drive_file_id"))
            write(f"Documento #{no}: [{doc_type}] {file_name}")
            if uploaded_at:
                write(f"Subido: {uploaded_at}")
            if drive_file_id:
                write(f"Drive file_id: {drive_file_id}")
            y -= 2

    c.showPage()
    c.save()

    return buf.getvalue()
