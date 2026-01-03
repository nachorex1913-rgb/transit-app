# transit_core/pdf_builder.py
from __future__ import annotations

from typing import Any, Dict, Optional, List
from datetime import datetime

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch


# ----------------------------
# Config
# ----------------------------
PAGE_SIZE = letter
FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

TITLE_SIZE = 14
H2_SIZE = 11
BODY_SIZE = 10

MARGIN_L = 0.75 * inch
MARGIN_R = 0.75 * inch
MARGIN_T = 0.75 * inch
MARGIN_B = 0.75 * inch

LINE_GAP = 12  # leading base


# ----------------------------
# Helpers (drawing)
# ----------------------------
def _safe(s: Any) -> str:
    return ("" if s is None else str(s)).strip()


def _dt_now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_page(c: canvas.Canvas, page_w: float, page_h: float) -> float:
    c.showPage()
    return page_h - MARGIN_T


def _ensure_space(c: canvas.Canvas, y: float, need: float, page_w: float, page_h: float) -> float:
    if y - need < MARGIN_B:
        return _new_page(c, page_w, page_h)
    return y


def _line(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    font: str = FONT,
    size: int = BODY_SIZE
) -> float:
    c.setFont(font, size)
    c.drawString(x, y, text)
    return y - LINE_GAP


def _section_title(c: canvas.Canvas, text: str, y: float, page_w: float, page_h: float) -> float:
    y = _ensure_space(c, y, 28, page_w, page_h)
    c.setFont(FONT_BOLD, H2_SIZE)
    c.drawString(MARGIN_L, y, text)
    y -= 8
    c.setLineWidth(0.6)
    c.line(MARGIN_L, y, page_w - MARGIN_R, y)
    y -= 14
    return y


def _wrap_paragraph(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    max_width: float,
    page_w: float,
    page_h: float,
    font: str = FONT,
    size: int = BODY_SIZE,
    leading: int = 12
) -> float:
    """
    Word-wrap seguro para no salirse del margen.
    """
    text = _safe(text)
    if not text:
        return y

    c.setFont(font, size)
    words = text.split()
    line = ""
    lines: List[str] = []

    for w in words:
        test = (line + " " + w).strip()
        if c.stringWidth(test, font, size) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = w

    if line:
        lines.append(line)

    for ln in lines:
        y = _ensure_space(c, y, leading + 2, page_w, page_h)
        c.setFont(font, size)
        c.drawString(x, y, ln)
        y -= leading

    return y


# ----------------------------
# Normalización Documentos
# ----------------------------
DOC_TYPES = ["ID_CLIENTE", "TITULO_VEHICULO", "FACTURA_VEHICULO", "FACTURA_ARTICULO", "OTRO"]


def _looks_like_drive_id(s: str) -> bool:
    s = _safe(s)
    if len(s) < 18:
        return False
    # Drive id típico: letras/números/guiones/guion bajo
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]+", s))


def _doc_type_from_row(row: Dict[str, Any]) -> str:
    """
    Protege contra headers corridos o swaps:
    Queremos SIEMPRE doc_type humano.
    """
    dt = _safe(row.get("doc_type"))
    dfid = _safe(row.get("drive_file_id"))

    if dt in DOC_TYPES:
        return dt

    # si doc_type parece drive id y drive_file_id parece doc_type, swap
    if _looks_like_drive_id(dt) and (dfid in DOC_TYPES):
        return dfid

    # si drive_file_id sí es doc_type
    if dfid in DOC_TYPES:
        return dfid

    return dt or "OTRO"


# ----------------------------
# Public API
# ----------------------------
def build_case_summary_pdf_bytes(
    *,
    case: Dict[str, Any],
    vehicles_df,
    articles_df,
    documents_df,
    client: Optional[Dict[str, Any]] = None,
    client_name: Optional[str] = None,
    **kwargs
) -> bytes:
    """
    Genera PDF resumen del trámite.

    Compatibilidad:
    - client_name="..." (por si alguien lo llama así)
    - client={...} (dict)
    - kwargs ignorados a propósito para no romper llamadas viejas
    """

    # --- Resolver nombre cliente
    resolved_client_name = ""
    if client_name:
        resolved_client_name = _safe(client_name)
    elif client and isinstance(client, dict):
        resolved_client_name = _safe(client.get("name"))
    else:
        # intenta buscar en kwargs por si llega con otra llave
        resolved_client_name = _safe(kwargs.get("client_name") or kwargs.get("name") or "")

    case_id = _safe(case.get("case_id")) or _safe(case.get("id")) or "-"
    status = _safe(case.get("status")) or "-"
    origin = _safe(case.get("origin")) or "-"
    destination = _safe(case.get("destination")) or "-"
    drive_folder_id = _safe(case.get("drive_folder_id")) or "-"
    created_at = _safe(case.get("created_at")) or ""
    updated_at = _safe(case.get("updated_at")) or ""

    # --- DataFrames a lista de dicts
    vehicles = []
    if vehicles_df is not None:
        try:
            vehicles = vehicles_df.fillna("").to_dict("records")
        except Exception:
            vehicles = []

    articles = []
    if articles_df is not None:
        try:
            articles = articles_df.fillna("").to_dict("records")
        except Exception:
            articles = []

    documents = []
    if documents_df is not None:
        try:
            documents = documents_df.fillna("").to_dict("records")
        except Exception:
            documents = []

    # --- Canvas
    from io import BytesIO
    buff = BytesIO()
    c = canvas.Canvas(buff, pagesize=PAGE_SIZE)
    page_w, page_h = PAGE_SIZE
    usable_w = page_w - MARGIN_L - MARGIN_R

    y = page_h - MARGIN_T

    # ----------------------------
    # Header
    # ----------------------------
    c.setFont(FONT_BOLD, TITLE_SIZE)
    c.drawString(MARGIN_L, y, "RESUMEN DEL TRÁMITE")
    y -= 18

    c.setFont(FONT, BODY_SIZE)
    y = _line(c, f"Trámite: {case_id}", MARGIN_L, y, font=FONT_BOLD, size=BODY_SIZE)
    y = _line(c, f"Cliente: {resolved_client_name or '-'}", MARGIN_L, y)
    y = _line(c, f"Estatus: {status}", MARGIN_L, y)
    y = _line(c, f"Origen / Destino: {origin} → {destination}", MARGIN_L, y)
    y = _line(c, f"Carpeta Drive: {drive_folder_id}", MARGIN_L, y)

    if created_at:
        y = _line(c, f"Creado: {created_at}", MARGIN_L, y)
    if updated_at:
        y = _line(c, f"Actualizado: {updated_at}", MARGIN_L, y)

    y -= 6
    c.setLineWidth(0.6)
    c.line(MARGIN_L, y, page_w - MARGIN_R, y)
    y -= 16

    # ----------------------------
    # 1) Vehículos (NO tocar lógica — detallado)
    # ----------------------------
    y = _section_title(c, "1) Vehículos", y, page_w, page_h)

    if not vehicles:
        y = _line(c, "Sin vehículos registrados.", MARGIN_L, y)
    else:
        for idx, v in enumerate(vehicles, start=1):
            # Espacio mínimo por vehículo
            y = _ensure_space(c, y, 130, page_w, page_h)

            vin = _safe(v.get("vin"))
            brand = _safe(v.get("brand"))
            model = _safe(v.get("model"))
            year = _safe(v.get("year"))

            trim = _safe(v.get("trim"))
            engine = _safe(v.get("engine"))
            vehicle_type = _safe(v.get("vehicle_type"))
            body_class = _safe(v.get("body_class"))
            plant_country = _safe(v.get("plant_country"))
            gvwr = _safe(v.get("gvwr"))
            weight = _safe(v.get("weight"))
            # curb_weight NO se imprime (lo quitaron)
            created = _safe(v.get("created_at") or v.get("registered_at") or v.get("added_at"))

            y = _line(c, f"Vehículo #{idx}: VIN: {vin}", MARGIN_L, y, font=FONT_BOLD, size=BODY_SIZE)
            y = _line(c, f"Marca/Modelo/Año: {brand} {model} {year}".strip(), MARGIN_L, y)
            if trim:
                y = _line(c, f"Trim: {trim}", MARGIN_L, y)
            if engine:
                y = _line(c, f"Motor: {engine}", MARGIN_L, y)
            if vehicle_type:
                y = _line(c, f"Tipo: {vehicle_type}", MARGIN_L, y)
            if body_class:
                y = _line(c, f"Carrocería: {body_class}", MARGIN_L, y)
            if plant_country:
                y = _line(c, f"País planta: {plant_country}", MARGIN_L, y)
            if gvwr:
                y = _wrap_paragraph(
                    c, f"GVWR: {gvwr}",
                    MARGIN_L, y,
                    usable_w, page_w, page_h,
                    font=FONT, size=BODY_SIZE, leading=12
                )
            if weight:
                y = _line(c, f"Peso: {weight}", MARGIN_L, y)
            if created:
                y = _line(c, f"Registrado: {created}", MARGIN_L, y)

            y -= 8
            c.setLineWidth(0.4)
            c.line(MARGIN_L, y, page_w - MARGIN_R, y)
            y -= 14

    # ----------------------------
    # 2) Artículos / Items (SOLO descripción + wrap)
    # ----------------------------
    y = _section_title(c, "2) Artículos / Items", y, page_w, page_h)

    if not articles:
        y = _line(c, "Sin artículos registrados.", MARGIN_L, y)
    else:
        for idx, a in enumerate(articles, start=1):
            # Espacio mínimo por item (con wrap)
            y = _ensure_space(c, y, 70, page_w, page_h)

            desc = _safe(a.get("description"))
            created = _safe(a.get("created_at") or a.get("registered_at") or a.get("added_at"))

            y = _line(c, f"Artículo #{idx}:", MARGIN_L, y, font=FONT_BOLD, size=BODY_SIZE)

            # ✅ La descripción manda (NO repetir fields)
            y = _wrap_paragraph(
                c,
                f"Descripción: {desc}",
                MARGIN_L,
                y,
                usable_w,
                page_w,
                page_h,
                font=FONT,
                size=BODY_SIZE,
                leading=12,
            )

            if created:
                y = _line(c, f"Registrado: {created}", MARGIN_L, y)

            y -= 6
            c.setLineWidth(0.4)
            c.line(MARGIN_L, y, page_w - MARGIN_R, y)
            y -= 14

    # ----------------------------
    # 3) Documentos del trámite (Tipo + Archivo, todos)
    # ----------------------------
    y = _section_title(c, "3) Documentos del trámite", y, page_w, page_h)

    if not documents:
        y = _line(c, "Sin documentos registrados.", MARGIN_L, y)
    else:
        for idx, d in enumerate(documents, start=1):
            y = _ensure_space(c, y, 45, page_w, page_h)

            doc_type = _doc_type_from_row(d)   # ✅ tipo correcto
            file_name = _safe(d.get("file_name"))
            uploaded_at = _safe(d.get("uploaded_at") or d.get("created_at") or d.get("registered_at"))

            # ✅ NO mostramos drive_file_id como “titulo”
            y = _line(c, f"Documento #{idx}: {doc_type}", MARGIN_L, y, font=FONT_BOLD, size=BODY_SIZE)

            if file_name:
                y = _wrap_paragraph(
                    c,
                    f"Archivo: {file_name}",
                    MARGIN_L,
                    y,
                    usable_w,
                    page_w,
                    page_h,
                    font=FONT,
                    size=BODY_SIZE,
                    leading=12,
                )

            if uploaded_at:
                y = _line(c, f"Registrado: {uploaded_at}", MARGIN_L, y)

            y -= 6
            c.setLineWidth(0.4)
            c.line(MARGIN_L, y, page_w - MARGIN_R, y)
            y -= 14

    # ----------------------------
    # Footer
    # ----------------------------
    y = _ensure_space(c, y, 28, page_w, page_h)
    c.setFont(FONT, 8)
    c.drawString(MARGIN_L, MARGIN_B - 10, f"Generado: {_dt_now_str()}")

    c.save()
    pdf_bytes = buff.getvalue()
    buff.close()
    return pdf_bytes
