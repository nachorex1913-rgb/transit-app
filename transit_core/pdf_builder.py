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
    Genera PDF (bytes) con el resumen del trámite.
    Nota: Este PDF es parte del proceso (no final de aduana todavía).
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
        vdf = vehicles_df.copy().fillna("").reset_index(drop=True)

        for i, row in vdf.iterrows():
            ensure_space(1.25 * inch)
            no = i + 1

            # ✅ Corrección: en tu sheet existe "vin" (no "unique_key")
            vin = _safe(row.get("vin"))
            vehicle_id = _safe(row.get("vehicle_id"))

            brand = _safe(row.get("brand"))
            model = _safe(row.get("model"))
            year = _safe(row.get("year"))
            desc = _safe(row.get("description"))
            source = _safe(row.get("source"))
            created_item = _safe(row.get("created_at"))

            trim = _safe(row.get("trim"))
            engine = _safe(row.get("engine"))
            vtype = _safe(row.get("vehicle_type"))
            body = _safe(row.get("body_class"))
            plant = _safe(row.get("plant_country"))
            gvwr = _safe(row.get("gvwr"))
            curb = _safe(row.get("curb_weight"))
            peso = _safe(row.get("weight"))
            value = _safe(row.get("value"))

            write(f"Vehículo #{no}: VIN: {vin}", bold=True)
            if vehicle_id:
                write(f"ID: {vehicle_id}")
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
            if value:
                write(f"Valor (opcional): {value}")
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
        adf = articles_df.copy().fillna("").reset_index(drop=True)

        for i, row in adf.iterrows():
            ensure_space(1.25 * inch)
            no = i + 1

            # ✅ Corrección: en tu sheet existe "seq" (no "unique_key")
            seq = _safe(row.get("seq"))
            article_id = _safe(row.get("article_id"))

            item_type = _safe(row.get("item_type"))
            ref = _safe(row.get("ref"))
            brand = _safe(row.get("brand"))
            model = _safe(row.get("model"))
            qty = _safe(row.get("quantity"))
            weight = _safe(row.get("weight"))
            value = _safe(row.get("value"))
            desc = _safe(row.get("description"))
            condition = _safe(row.get("condition"))
            is_part = _safe(row.get("is_vehicle_part"))
            parent_vin = _safe(row.get("parent_vin"))
            source = _safe(row.get("source"))
            created_item = _safe(row.get("created_at"))

            title = seq or article_id or f"Item #{no}"
            write(f"Item #{no}: {title}", bold=True)

            meta = []
            if item_type:
                meta.append(f"Tipo: {item_type}")
            if ref:
                meta.append(f"Ref: {ref}")
            if meta:
                write(" | ".join(meta))

            if desc:
                write(f"Descripción: {desc}")
            else:
                write("Descripción: (sin descripción)")

            line = []
            if brand:
                line.append(f"Marca: {brand}")
            if model:
                line.append(f"Modelo: {model}")
            if condition:
                line.append(f"Condición: {condition}")
            if qty:
                line.append(f"Cantidad: {qty}")
            if weight:
                line.append(f"Peso: {weight}")
            if value:
                line.append(f"Valor: {value}")

            if line:
                write(" | ".join(line))

            if is_part:
                write(f"Parte de vehículo: {is_part}")
            if parent_vin:
                write(f"VIN relacionado: {parent_vin}")

            if source:
                write(f"Fuente: {source}")
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
        ddf = documents_df.copy().fillna("").reset_index(drop=True)

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
