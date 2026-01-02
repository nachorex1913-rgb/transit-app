# transit_core/vin_ocr.py
from __future__ import annotations

import re
from io import BytesIO
from typing import Dict, Any, List

from PIL import Image, ImageOps, ImageEnhance

# EXACTAMENTE 17 alfanum consecutivos
VIN17_INLINE = re.compile(r"[A-Z0-9]{17}")

# (Opcional recomendado) VIN real NO usa I/O/Q
INVALID = set("IOQ")


def _preprocess(img: Image.Image) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    g = g.resize((g.size[0] * 2, g.size[1] * 2))
    g = ImageEnhance.Contrast(g).enhance(2.2)
    g = ImageEnhance.Sharpness(g).enhance(2.0)
    return g


def _clean_text(s: str) -> str:
    s = (s or "").upper()
    # deja solo A-Z, 0-9 y espacios/saltos para no “pegar” texto de distintos lugares
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    # colapsa espacios múltiples (pero NO elimina todos los espacios)
    s = re.sub(r"[ \t]+", " ", s)
    return s


def _valid_17(x: str) -> bool:
    if not x or len(x) != 17:
        return False
    if not re.fullmatch(r"[A-Z0-9]{17}", x):
        return False
    # recomendado: evita falsos VIN con I/O/Q
    if any(ch in INVALID for ch in x):
        return False
    return True


def extract_vin_from_image(image_bytes: bytes) -> Dict[str, Any]:
    """
    Regla estricta:
    - Buscar SOLO alfanuméricos consecutivos de 17 (A-Z/0-9)
    - No keywords, no scoring, no “mejor candidato” inventado
    - Si no hay: vin = ""
    """
    if not image_bytes:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "error": ""}

    import pytesseract
    from pytesseract.pytesseract import TesseractError

    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "error": f"Image open error: {e}"}

    proc = _preprocess(img)

    # whitelist para que tesseract NO meta símbolos raros
    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    cfg = "--oem 3 --psm 6 -c tessedit_char_whitelist=" + whitelist

    try:
        raw = pytesseract.image_to_string(proc, config=cfg) or ""
    except TesseractError as e:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "error": f"Tesseract error: {e}"}
    except Exception as e:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "error": f"OCR error: {e}"}

    cleaned = _clean_text(raw)

    # 🔥 CLAVE: buscar en cleaned SIN compactar todo, para no crear “VINs” pegando basura
    found = []
    for m in VIN17_INLINE.finditer(cleaned):
        cand = m.group(0)
        if _valid_17(cand):
            found.append(cand)

    # únicos en orden
    cands: List[str] = []
    seen = set()
    for v in found:
        if v not in seen:
            seen.add(v)
            cands.append(v)

    if not cands:
        return {"vin": "", "confidence": 0.0, "raw_text": raw, "candidates": [], "error": ""}

    # No “adivinar”: devuelve el primero encontrado y lista de candidatos
    return {
        "vin": cands[0],
        "confidence": 0.75,  # fijo, porque no estamos usando contexto
        "raw_text": raw,
        "candidates": cands[:10],
        "error": ""
    }
