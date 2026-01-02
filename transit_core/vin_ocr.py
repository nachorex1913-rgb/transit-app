# transit_core/vin_ocr.py
from __future__ import annotations

import re
from io import BytesIO
from typing import List, Dict, Any

from PIL import Image, ImageOps, ImageEnhance

from transit_core.validators import normalize_vin, is_valid_vin_strict, is_valid_vin

VIN17_RE = re.compile(r"[A-Z0-9]{17}")

def _preprocess(img: Image.Image) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    g = g.resize((g.size[0] * 2, g.size[1] * 2))
    g = ImageEnhance.Contrast(g).enhance(2.2)
    g = ImageEnhance.Sharpness(g).enhance(2.0)
    return g

def _fix_common_ocr(s: str) -> str:
    # variantes típicas OCR
    return (
        s.replace("O", "0")
         .replace("Q", "0")
         .replace("I", "1")
         .replace("S", "5")
         .replace("B", "8")
         .replace("Z", "2")
    )

def _extract_candidates(raw: str) -> List[str]:
    if not raw:
        return []
    t = raw.upper()
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    compact = re.sub(r"\s+", "", t)

    cands = [m.group(0) for m in VIN17_RE.finditer(compact)]
    expanded = []
    for c in cands:
        expanded.append(c)
        expanded.append(_fix_common_ocr(c))

    out = []
    seen = set()
    for c in expanded:
        c = normalize_vin(c)
        if len(c) != 17:
            continue
        # filtro básico VIN (sin I/O/Q)
        if not is_valid_vin(c):
            continue
        if c not in seen:
            seen.add(c)
            out.append(c)

    return out[:15]

def extract_vin_from_image(image_bytes: bytes) -> dict:
    """
    Return:
      {
        "vin": "",
        "confidence": 0.0-1.0,
        "raw_text": "",
        "candidates": []
      }
    """
    if not image_bytes:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": []}

    import pytesseract

    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img2 = _preprocess(img)

    # ✅ ROI para títulos/registro: zona donde típicamente aparece el VIN
    # (funciona muy bien para CA Title como el tuyo)
    w, h = img2.size
    roi = img2.crop((0, int(h * 0.18), int(w * 0.75), int(h * 0.60)))

    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    base_cfg = f"-c tessedit_char_whitelist={whitelist}"

    configs = [
        f"--oem 3 --psm 7 {base_cfg}",
        f"--oem 3 --psm 6 {base_cfg}",
        f"--oem 3 --psm 11 {base_cfg}",
    ]

    best_raw = ""
    best_cands: List[str] = []

    for cfg in configs:
        raw = pytesseract.image_to_string(roi, config=cfg) or ""
        if len(raw) > len(best_raw):
            best_raw = raw

        cands = _extract_candidates(raw)
        if cands:
            best_cands = cands
            # Prioriza strict (check digit)
            strict = [c for c in cands if is_valid_vin_strict(c)]
            if strict:
                return {"vin": strict[0], "confidence": 0.92, "raw_text": raw, "candidates": strict[:10]}
            return {"vin": cands[0], "confidence": 0.65, "raw_text": raw, "candidates": cands[:10]}

    return {"vin": "", "confidence": 0.0, "raw_text": best_raw, "candidates": best_cands}
