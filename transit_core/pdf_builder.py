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


def _looks_like_drive_id(s: str) -> bool:
    s = (s or "").strip()
    if len(s) < 18:
        return False
    # Drive id típico: letras/números/guiones/guion bajo
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]+", s))


def _doc_type_clean(row: dict) -> str:
    """
    Queremos imprimir SIEMPRE el tipo humano (ID_CLIENTE, FACTURA_VEHICULO, etc).
    Si la hoja quedó “corrida” y doc_type trae el drive id, intentamos corregir.
    """
    dt = _safe(row.get("doc_type"))
    dfid = _safe(row.get("drive_file_id"))

    # Si doc_type parece drive id y drive_file_id NO parece drive id => asumimos que drive_file_id trae el tipo
    if _looks_like_drive_id(dt) and dfid and (not _looks_like_drive_id(dfid)):
        return dfid

    # Si doc_type está vacío pero drive_file_id trae algo legible
    if not dt and dfid and (not _looks_like_drive_id(dfid)):
        return dfid

    # Normal
    return dt or "OTRO"


def build_case_summary_pdf_bytes(
    case: Dict[str, Any],
    client: Optional[Dict[str, Any]] = None,
    vehicles_df: Optional[pd.DataFrame] = None,
    articles_df: Optional[pd.DataFrame] = None,
    documents_df: Optional[pd.DataFrame] = None,
) -> bytes:
    """
    Genera PDF (bytes) con el resumen del trámite en un formato tipo “aduana”.
    - vehicles_df: vehículos del case
    - articles_df: artículos del case
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
    client_name = _safe((client or {}).get("name"))

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
    # 1) Vehículos  (NO TOCAR)
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

            trim = _safe(row.get("trim")) if "trim" in vdf.columns else ""
            engine = _safe(row.get("engine")) if "engine" in vdf.columns else ""
            vtype = _safe(row.get("vehicle_type")) if "vehicle_type" in vdf.columns else ""
            body = _safe(row.get("body_class")) if "body_class" in vdf.columns else ""
            plant = _safe(row.get("plant_country")) if "plant_country" in vdf.columns else ""
            gvwr = _safe(row.get("gvwr")) if "gvwr" in vdf.columns else ""
            curb = _safe(row.get("curb_weight")) if "curb_weight" in vdf.columns else ""
            peso = _safe(row.get("weight"))

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

# ==========================
# 2) Artículos / Items
# ==========================
y = _section_title(c, "2) Artículos / Items", y)

items = []
if articles_df is not None:
    items = articles_df.fillna("").to_dict("records")

for i, row in enumerate(items, start=1):
    desc = str(row.get("description", "") or "").strip()

    # ✅ SOLO DESCRIPCIÓN (NO repetir campos)
    y = _line(c, f"Artículo #{i}:", x_left, y, bold=True)

    # wrap para no salirse del margen
    y = _wrap_paragraph(
        c,
        f"Descripción: {desc}",
        x_left,
        y,
        max_width=usable_width,
        leading=12
    )

    created = str(row.get("created_at", "") or row.get("registered_at", "") or "").strip()
    if created:
        y = _line(c, f"Registrado: {created}", x_left, y)

    y -= 6

    # -------------------------
    # 3) Documentos del trámite  ✅ (CAMBIO AQUÍ)
    # - NO mostrar Drive id como “tipo”
    # - NO imprimir drive_file_id como línea (link/id)
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

            row_dict = row.to_dict() if hasattr(row, "to_dict") else dict(row)

            doc_type = _doc_type_clean(row_dict)  # ✅ tipo humano
            file_name = _safe(row_dict.get("file_name"))
            uploaded_at = _safe(row_dict.get("uploaded_at"))

            # Formato: Documento #n: [TIPO] archivo   fecha
            title = f"Documento #{no}: [{doc_type}] {file_name}".strip()
            write(title, bold=True)

            if uploaded_at:
                write(f"Subido: {uploaded_at}")

            y -= 2

    c.showPage()
    c.save()

    return buf.getvalue()
