# transit_core/pdf_generator.py
from __future__ import annotations

from io import BytesIO
import re
from typing import Any, Dict, Optional

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors


# -----------------------------
# Helpers
# -----------------------------
def _safe(v: Any) -> str:
    if v is None:
        return ""
    return str(v)

def _parse_weight_lb(value: Any) -> float:
    """
    Convierte pesos tipo:
      "3200 lb", "18 lb", "1450", 1450, "1450lbs"
    a float (libras). Si no puede, retorna 0.
    """
    if value is None:
        return 0.0
    s = str(value).strip().lower()
    if not s:
        return 0.0
    # extrae primer número
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except:
        return 0.0

def _parse_qty(value: Any) -> float:
    try:
        if value is None or str(value).strip() == "":
            return 0.0
        return float(str(value).strip())
    except:
        return 0.0

def _parse_money(value: Any) -> float:
    """
    Convierte "$1,200", "1200", 1200 a float.
    """
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s:
        return 0.0
    s = s.replace("$", "").replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except:
        return 0.0

def _wrap_text(c: canvas.Canvas, text: str, x: float, y: float, max_width: float, font_name="Helvetica", font_size=9) -> list[str]:
    """
    Wrap simple por palabras usando stringWidth.
    """
    c.setFont(font_name, font_size)
    words = (text or "").split()
    if not words:
        return [""]
    lines = []
    cur = words[0]
    for w in words[1:]:
        test = cur + " " + w
        if c.stringWidth(test, font_name, font_size) <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines

# -----------------------------
# Drawing primitives
# -----------------------------
def _draw_header(c: canvas.Canvas, case: dict, page_w: float, page_h: float):
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, page_h - 50, "Transit - Exportación / Tránsito")

    c.setFont("Helvetica", 10)
    c.drawString(
        50,
        page_h - 70,
        f"Trámite: {_safe(case.get('case_id'))}   Fecha: {_safe(case.get('case_date'))}   Estatus: {_safe(case.get('status'))}",
    )
    c.drawString(
        50,
        page_h - 85,
        f"Origen: {_safe(case.get('origin'))}   Destino: {_safe(case.get('destination'))}",
    )

    # Línea separadora
    c.setStrokeColor(colors.lightgrey)
    c.line(50, page_h - 95, page_w - 50, page_h - 95)
    c.setStrokeColor(colors.black)

def _new_page(c: canvas.Canvas, case: dict, page_w: float, page_h: float) -> float:
    c.showPage()
    _draw_header(c, case, page_w, page_h)
    return page_h - 120

def _section_title(c: canvas.Canvas, title: str, x: float, y: float) -> float:
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, title)
    return y - 16

def _draw_kv_block(c: canvas.Canvas, kv: list[tuple[str, str]], x: float, y: float, line_h=14) -> float:
    c.setFont("Helvetica", 10)
    for k, v in kv:
        c.drawString(x, y, f"{k}: {v}")
        y -= line_h
    return y

def _draw_table_header(c: canvas.Canvas, cols: list[tuple[str, float]], x: float, y: float, row_h: float = 14):
    """
    cols: [(title, width), ...]
    """
    c.setFillColor(colors.whitesmoke)
    c.rect(x, y - row_h + 2, sum(w for _, w in cols), row_h, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    cx = x
    for title, w in cols:
        c.drawString(cx + 4, y - 10, title)
        cx += w
    c.setStrokeColor(colors.lightgrey)
    c.line(x, y - row_h + 2, x + sum(w for _, w in cols), y - row_h + 2)
    c.setStrokeColor(colors.black)

def _draw_table_row(c: canvas.Canvas, cols: list[tuple[str, float]], values: list[str], x: float, y: float, row_h: float = 14):
    c.setFont("Helvetica", 9)
    cx = x
    for (_, w), v in zip(cols, values):
        c.drawString(cx + 4, y - 10, (v or "")[:200])
        cx += w
    c.setStrokeColor(colors.lightgrey)
    c.line(x, y - row_h + 2, x + sum(w for _, w in cols), y - row_h + 2)
    c.setStrokeColor(colors.black)

# -----------------------------
# Main PDF generator
# -----------------------------
def generate_case_pdf(case: dict, client: dict, items_df, documents_df) -> bytes:
    """
    Genera PDF profesional para un trámite.
    Recibe:
      - case: dict
      - client: dict
      - items_df: DataFrame con item_type ("vehicle"/"article") y campos
      - documents_df: DataFrame con doc_type, file_name
    Retorna bytes del PDF.
    """
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    page_w, page_h = letter

    # Header
    _draw_header(c, case, page_w, page_h)
    y = page_h - 120

    # -----------------------------
    # Cliente
    # -----------------------------
    y = _section_title(c, "Datos del Cliente", 50, y)
    kv = [
        ("Nombre", _safe(client.get("name"))),
        ("Dirección", _safe(client.get("address"))),
        ("Identificación", f"{_safe(client.get('id_type'))} {_safe(client.get('id_number'))}".strip()),
        ("Tel / Email", f"{_safe(client.get('phone'))}   {_safe(client.get('email'))}".strip()),
    ]
    y = _draw_kv_block(c, kv, 50, y, line_h=14)
    y -= 8

    if y < 120:
        y = _new_page(c, case, page_w, page_h)

    # -----------------------------
    # Separar items
    # -----------------------------
    vehicles = items_df[items_df.get("item_type") == "vehicle"] if items_df is not None and not items_df.empty and "item_type" in items_df.columns else items_df
    articles = items_df[items_df.get("item_type") == "article"] if items_df is not None and not items_df.empty and "item_type" in items_df.columns else items_df

    # -----------------------------
    # Vehículos (tabla)
    # -----------------------------
    y = _section_title(c, "Vehículos", 50, y)

    v_cols = [
        ("VIN / ID", 160),
        ("Año", 40),
        ("Marca", 80),
        ("Modelo", 120),
        ("Peso (lb)", 70),
    ]
    _draw_table_header(c, v_cols, 50, y)
    y -= 16

    total_weight = 0.0
    total_qty = 0.0
    total_value = 0.0

    if vehicles is None or getattr(vehicles, "empty", True):
        c.setFont("Helvetica", 9)
        c.drawString(50, y - 10, "Sin vehículos.")
        y -= 18
    else:
        for _, r in vehicles.iterrows():
            vin = _safe(r.get("unique_key") or r.get("vin") or r.get("item_id"))
            year = _safe(r.get("year"))
            brand = _safe(r.get("brand"))
            model = _safe(r.get("model"))
            wlb = _parse_weight_lb(r.get("weight"))

            qty = _parse_qty(r.get("quantity") or r.get("qty") or 1)
            val = _parse_money(r.get("value"))

            total_weight += wlb
            total_qty += qty
            total_value += val

            if y < 110:
                y = _new_page(c, case, page_w, page_h)
                y = _section_title(c, "Vehículos (continuación)", 50, y)
                _draw_table_header(c, v_cols, 50, y)
                y -= 16

            _draw_table_row(
                c,
                v_cols,
                [vin, year, brand, model, f"{wlb:.0f}" if wlb else ""],
                50,
                y,
            )
            y -= 14

    y -= 10
    if y < 120:
        y = _new_page(c, case, page_w, page_h)

    # -----------------------------
    # Artículos (tabla con wrap en descripción)
    # -----------------------------
    y = _section_title(c, "Artículos", 50, y)

    a_cols = [
        ("ID", 110),
        ("Descripción", 240),
        ("Marca", 90),
        ("Cant.", 45),
        ("Peso (lb)", 70),
    ]
    _draw_table_header(c, a_cols, 50, y)
    y -= 16

    if articles is None or getattr(articles, "empty", True):
        c.setFont("Helvetica", 9)
        c.drawString(50, y - 10, "Sin artículos.")
        y -= 18
    else:
        for _, r in articles.iterrows():
            uid = _safe(r.get("unique_key") or r.get("item_id"))
            desc = _safe(r.get("description"))
            brand = _safe(r.get("brand"))
            qty = _parse_qty(r.get("quantity") or r.get("qty") or 1)
            wlb = _parse_weight_lb(r.get("weight"))
            val = _parse_money(r.get("value"))

            total_weight += wlb
            total_qty += qty
            total_value += val

            # Wrap de description dentro de 240px
            desc_lines = _wrap_text(c, desc, x=0, y=0, max_width=230, font_name="Helvetica", font_size=9)
            row_height = max(14, 12 * len(desc_lines))

            if y < 110 + row_height:
                y = _new_page(c, case, page_w, page_h)
                y = _section_title(c, "Artículos (continuación)", 50, y)
                _draw_table_header(c, a_cols, 50, y)
                y -= 16

            # Dibuja primera línea con toda la fila
            _draw_table_row(
                c,
                a_cols,
                [uid, desc_lines[0] if desc_lines else "", brand, f"{qty:.0f}" if qty else "", f"{wlb:.0f}" if wlb else ""],
                50,
                y,
            )

            # Líneas extra de la descripción (solo en columna descripción)
            if len(desc_lines) > 1:
                # Ajuste manual: reimprimir solo la parte de descripción debajo
                yy = y - 12
                for extra in desc_lines[1:]:
                    if yy < 110:
                        y = _new_page(c, case, page_w, page_h)
                        y = _section_title(c, "Artículos (continuación)", 50, y)
                        _draw_table_header(c, a_cols, 50, y)
                        y -= 16
                        yy = y

                    # Recuadro “invisible”: escribe en la segunda columna
                    c.setFont("Helvetica", 9)
                    c.drawString(50 + a_cols[0][1] + 4, yy - 10, extra[:200])
                    yy -= 12

            y -= row_height

    y -= 10
    if y < 120:
        y = _new_page(c, case, page_w, page_h)

    # -----------------------------
    # Totales
    # -----------------------------
    y = _section_title(c, "Totales", 50, y)
    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"Cantidad total (ítems): {total_qty:.0f}" if total_qty else "Cantidad total (ítems):")
    y -= 14
    c.drawString(50, y, f"Peso total estimado (lb): {total_weight:.0f}" if total_weight else "Peso total estimado (lb):")
    y -= 14
    if total_value:
        c.drawString(50, y, f"Valor total declarado: ${total_value:,.2f}")
        y -= 14
    y -= 6

    if y < 140:
        y = _new_page(c, case, page_w, page_h)

    # -----------------------------
    # Documentos adjuntos
    # -----------------------------
    y = _section_title(c, "Documentos adjuntos", 50, y)
    c.setFont("Helvetica", 9)

    if documents_df is None or getattr(documents_df, "empty", True):
        c.drawString(50, y, "Sin documentos.")
        y -= 14
    else:
        for _, r in documents_df.iterrows():
            line = f"{_safe(r.get('doc_type'))}: {_safe(r.get('file_name'))}"
            if y < 110:
                y = _new_page(c, case, page_w, page_h)
                y = _section_title(c, "Documentos adjuntos (continuación)", 50, y)
                c.setFont("Helvetica", 9)
            c.drawString(50, y, line[:140])
            y -= 12

    # -----------------------------
    # Firmas
    # -----------------------------
    if y < 160:
        y = _new_page(c, case, page_w, page_h)

    y = _section_title(c, "Firmas", 50, y)
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "Responsable / Agencia: ________________________________")
    y -= 22
    c.drawString(50, y, "Cliente: _____________________________________________")
    y -= 22
    c.drawString(50, y, f"Fecha: {datetime.now().strftime('%Y-%m-%d')}")
    y -= 10

    c.save()
    return buf.getvalue()
