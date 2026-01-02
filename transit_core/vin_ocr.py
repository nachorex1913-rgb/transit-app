# transit_core/vin_ocr.py
from __future__ import annotations

import re
from io import BytesIO
from typing import Dict, Any, List, Optional, Tuple

from PIL import Image, ImageOps, ImageEnhance

# 17 alfanum consecutivos (permitimos todo A-Z0-9 y luego filtramos I/O/Q si quieres)
VIN17_RE = re.compile(r"[A-Z0-9]{17}")

# Keywords que aceptamos para anclar ROI
# (VIN) o (VEHICLE ID NUMBER) o variantes comunes
KEYWORDS = {
    "VIN",
    "VEHICLE",
    "ID",
    "NUMBER",
    "VEHICLEID",
    "VEHICLEIDNUMBER",
    "VEHICLEIDNO",
    "VEHICLEIDENTIFICATION",
    "VEHICLEIDENTIFICATIONNUMBER",
    "VEHICLEIDENTIFICATIONNO",
    "VEHICLEIDENTIFICATION#",
}

# Para hacer match más flexible
def _norm_word(w: str) -> str:
    w = (w or "").strip().upper()
    w = re.sub(r"[^A-Z0-9#]", "", w)
    return w

def _preprocess_for_detect(img: Image.Image) -> Image.Image:
    """
    Preprocesado suave para detectar palabras clave.
    """
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    g = ImageEnhance.Contrast(g).enhance(1.6)
    return g

def _preprocess_for_vin(img: Image.Image) -> Image.Image:
    """
    Preprocesado fuerte para leer VIN en ROI.
    """
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    g = g.resize((g.size[0] * 2, g.size[1] * 2))
    g = ImageEnhance.Contrast(g).enhance(2.2)
    g = ImageEnhance.Sharpness(g).enhance(2.0)
    return g

def _find_keyword_anchor_boxes(d: Dict[str, Any]) -> List[Tuple[int,int,int,int,str,float]]:
    """
    Retorna cajas (l,t,r,b,text,conf) de palabras clave.
    """
    out = []
    n = len(d.get("text", []))
    for i in range(n):
        txt = _norm_word(d["text"][i])
        if not txt:
            continue
        try:
            conf = float(d["conf"][i])
        except Exception:
            conf = -1.0
        # baja basura
        if conf < 35:
            continue

        # match keyword directo o por contener (ej: VEHICLEIDNUMBER)
        is_kw = (txt in KEYWORDS)
        if not is_kw:
            # match flexible: contiene VIN o contiene VEHICLE y ID
            if "VIN" == txt:
                is_kw = True
            elif "VEHICLE" in txt and ("ID" in txt or "IDENTIFICATION" in txt):
                is_kw = True

        if not is_kw:
            continue

        l = int(d["left"][i])
        t = int(d["top"][i])
        w = int(d["width"][i])
        h = int(d["height"][i])
        out.append((l, t, l + w, t + h, txt, conf))
    return out

def _build_roi_from_anchors(img_w: int, img_h: int, anchors: List[Tuple[int,int,int,int,str,float]]) -> Optional[Tuple[int,int,int,int]]:
    """
    Construye ROI alrededor de la zona donde típicamente aparece el VIN:
    - al lado derecho de 'VIN' o
    - debajo / a la derecha de 'VEHICLE ID NUMBER'
    """
    if not anchors:
        return None

    # Ordena por "mejor" (conf alto y posición superior)
    anchors = sorted(anchors, key=lambda x: (-x[5], x[1]))

    # Tomamos el mejor anchor y expandimos con anchors cercanos (misma línea)
    l0, t0, r0, b0, txt0, conf0 = anchors[0]

    # Caja base agrupada (misma línea)
    gl, gt, gr, gb = l0, t0, r0, b0
    for (l, t, r, b, txt, conf) in anchors[1:]:
        # agrupa si está cerca verticalmente (misma línea)
        if abs(t - t0) <= 25:
            gl = min(gl, l)
            gt = min(gt, t)
            gr = max(gr, r)
            gb = max(gb, b)

    # ROI: normalmente VIN aparece a la derecha de la etiqueta
    # y/o inmediatamente debajo (según formato del documento).
    pad_x = 25
    pad_y = 15

    roi_left = max(0, gl - pad_x)
    roi_top = max(0, gt - pad_y)

    # Preferencia: derecha amplia y un poco abajo
    roi_right = min(img_w, gr + int(img_w * 0.55))  # amplia derecha
    roi_bottom = min(img_h, gb + int(img_h * 0.20)) # baja un poco

    # También incluimos una franja debajo por si el VIN está en la siguiente línea
    roi_bottom = min(img_h, max(roi_bottom, gb + 180))

    # Asegura ROI mínimo razonable
    if (roi_right - roi_left) < 200 or (roi_bottom - roi_top) < 80:
        return None

    return (roi_left, roi_top, roi_right, roi_bottom)

def _extract_vin17_only(raw: str) -> List[str]:
    """
    Extrae SOLO 17 alfanum consecutivos.
    No inventa, no corrige, no hace sliding global.
    """
    if not raw:
        return []
    t = raw.upper()
    # quita separadores, deja solo alfanum y espacios
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    # compacta por si viene separado por espacios
    compact = re.sub(r"\s+", "", t)

    # Busca 17 consecutivos en el compacto
    cands = [m.group(0) for m in VIN17_RE.finditer(compact)]

    # Unicos, en orden
    out = []
    seen = set()
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)

    return out[:10]

def extract_vin_from_image(image_bytes: bytes) -> dict:
    """
    Regla estricta:
    - Solo se considera VIN si se encuentra cerca de keywords VIN / VEHICLE ID NUMBER.
    - Solo acepta tokens 17 consecutivos A-Z0-9.
    Return:
      {
        "vin": "",
        "confidence": 0.0-1.0,
        "raw_text": "",
        "candidates": [],
        "found_keywords": True/False
      }
    """
    if not image_bytes:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": False}

    import pytesseract
    from pytesseract import Output

    img = Image.open(BytesIO(image_bytes)).convert("RGB")

    # 1) Detecta keywords (OCR amplio pero solo para palabras)
    detect_img = _preprocess_for_detect(img)
    try:
        d = pytesseract.image_to_data(
            detect_img,
            output_type=Output.DICT,
            config="--oem 3 --psm 6"
        )
    except Exception as e:
        return {"vin": "", "confidence": 0.0, "raw_text": f"OCR data error: {e}", "candidates": [], "found_keywords": False}

    anchors = _find_keyword_anchor_boxes(d)
    if not anchors:
        # Estricto: sin keywords => no aceptamos VIN
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": False}

    # 2) Construye ROI desde keywords
    prep = _preprocess_for_vin(img)
    w, h = prep.size
    roi_box = _build_roi_from_anchors(w, h, anchors)
    if not roi_box:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": True}

    l, t, r, b = roi_box
    roi = prep.crop((l, t, r, b))

    # 3) OCR final SOLO en ROI
    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    cfgs = [
        f"--oem 3 --psm 7 -c tessedit_char_whitelist={whitelist}",
        f"--oem 3 --psm 6 -c tessedit_char_whitelist={whitelist}",
    ]

    best_raw = ""
    best_cands: List[str] = []

    for cfg in cfgs:
        raw = pytesseract.image_to_string(roi, config=cfg) or ""
        if len(raw) > len(best_raw):
            best_raw = raw

        cands = _extract_vin17_only(raw)
        if cands:
            best_cands = cands
            # confianza alta porque viene anclado a keywords + longitud exacta
            return {
                "vin": cands[0],
                "confidence": 0.92 if len(cands) == 1 else 0.88,
                "raw_text": raw,
                "candidates": cands,
                "found_keywords": True
            }

    return {"vin": "", "confidence": 0.0, "raw_text": best_raw, "candidates": best_cands, "found_keywords": True}
