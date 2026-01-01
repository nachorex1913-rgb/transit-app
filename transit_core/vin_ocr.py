# transit_core/vin_ocr.py
from __future__ import annotations

import re
from typing import Dict, Any

from PIL import Image
from io import BytesIO


VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")  # excluye I/O/Q


def _clean_text(t: str) -> str:
    t = (t or "").upper()
    # reemplazos típicos OCR
    t = t.replace(" ", "").replace("\n", " ").replace("\r", " ")
    return t


def _best_vin_from_text(text: str) -> str:
    """
    Busca VIN en un texto OCR. Retorna el primero mejor candidato.
    """
    if not text:
        return ""
    # permitimos espacios en medio y luego limpiamos
    t = (text or "").upper()
    # quita caracteres raros pero deja alfanum
    t2 = re.sub(r"[^A-Z0-9]", " ", t)

    cands = VIN_RE.findall(t2)
    return cands[0] if cands else ""


def extract_vin_from_image(image_bytes: bytes) -> dict:
    """
    OCR VIN desde imagen.
    Return:
      {
        "vin": "1HGCM82633A004352" or "",
        "confidence": 0.0-1.0,
        "raw_text": "... opcional ..."
      }
    """
    if not image_bytes:
        return {"vin": "", "confidence": 0.0, "raw_text": ""}

    img = Image.open(BytesIO(image_bytes)).convert("RGB")

    # Intentar OCR con pytesseract si existe
    raw_text = ""
    vin = ""
    confidence = 0.0

    try:
        import pytesseract  # type: ignore

        # OCR simple. Puedes mejorar con preprocesado después.
        raw_text = pytesseract.image_to_string(img) or ""
        vin = _best_vin_from_text(raw_text)

        # confianza "heurística"
        confidence = 0.85 if vin else 0.20

        return {"vin": vin, "confidence": confidence, "raw_text": raw_text}

    except Exception:
        # Sin OCR disponible: no podemos leer texto real
        return {"vin": "", "confidence": 0.0, "raw_text": "OCR no disponible (pytesseract no instalado)."}
