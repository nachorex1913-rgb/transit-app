# transit_core/vin_ocr.py
from __future__ import annotations

import re
from io import BytesIO
from typing import Dict, Any, List

from PIL import Image, ImageOps, ImageEnhance

# VIN real: 17 chars, normalmente sin I/O/Q
VIN_STRICT = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def _preprocess(img: Image.Image) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    # agranda para VIN pequeño
    g = g.resize((g.size[0] * 2, g.size[1] * 2))
    g = ImageEnhance.Contrast(g).enhance(2.2)
    g = ImageEnhance.Sharpness(g).enhance(2.0)
    return g


def _normalize_ocr_text(t: str) -> str:
    t = (t or "").upper()
    # deja alfanum y separadores simples
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _fix_common_ocr_mistakes(s: str) -> str:
    """
    Correcciones típicas OCR:
      O->0, I->1, Q->0, S->5, B->8, Z->2
    OJO: no siempre es correcto, por eso generamos variantes.
    """
    return (
        s.replace("O", "0")
         .replace("Q", "0")
         .replace("I", "1")
         .replace("S", "5")
         .replace("B", "8")
         .replace("Z", "2")
    )


def _extract_candidates(text: str) -> List[str]:
    """
    Extrae candidatos VIN desde texto OCR:
    - busca grupos alfanuméricos largos
    - usa sliding window de 17
    - genera variantes con correcciones OCR
    """
    t = _normalize_ocr_text(text)

    # juntamos todo sin espacios para sliding
    compact = re.sub(r"\s+", "", t)

    candidates = []

    # 1) tokens alfanuméricos >= 17
    tokens = re.findall(r"[A-Z0-9]{8,}", compact)
    for tok in tokens:
        if len(tok) < 17:
            continue
        for i in range(0, len(tok) - 16):
            candidates.append(tok[i:i+17])

    # 2) también sliding sobre todo el texto compactado
    if len(compact) >= 17:
        for i in range(0, min(len(compact) - 16, 300)):  # límite para no explotar
            candidates.append(compact[i:i+17])

    # genera variantes corregidas
    expanded = []
    for c in candidates:
        expanded.append(c)
        expanded.append(_fix_common_ocr_mistakes(c))

    # filtra por formato VIN
    out = []
    seen = set()
    for c in expanded:
        c = c.strip().upper()
        if len(c) != 17:
            continue
        # intentamos estricto y también uno "casi" (si viene con I/O/Q lo arreglamos arriba)
        if VIN_STRICT.match(c):
            if c not in seen:
                seen.add(c)
                out.append(c)

    return out[:10]


def extract_vin_from_image(image_bytes: bytes) -> dict:
    """
    Return:
      {
        "vin": "...",
        "confidence": 0.0-1.0,
        "raw_text": "...",
        "candidates": [...]
      }
    """
    if not image_bytes:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": []}

    try:
        import pytesseract
    except Exception:
        return {"vin": "", "confidence": 0.0, "raw_text": "pytesseract no instalado.", "candidates": []}

    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img2 = _preprocess(img)

    # whitelist para VIN
    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    base_cfg = f"-c tessedit_char_whitelist={whitelist}"

    # probamos varios PSM (según layout de la foto)
    configs = [
        f"--oem 3 --psm 7 {base_cfg}",   # una línea
        f"--oem 3 --psm 6 {base_cfg}",   # bloque
        f"--oem 3 --psm 11 {base_cfg}",  # texto disperso
    ]

    best = {"vin": "", "raw_text": "", "candidates": [], "confidence": 0.0}

    for cfg in configs:
        try:
            raw = pytesseract.image_to_string(img2, config=cfg) or ""
            cands = _extract_candidates(raw)

            if cands:
                # si encontramos candidatos, el primero es el "mejor" por orden
                vin = cands[0]
                # confianza heurística: mejor si aparece varias veces
                conf = 0.92 if len(cands) >= 2 else 0.85
                return {"vin": vin, "confidence": conf, "raw_text": raw, "candidates": cands}

            # guarda el raw más “largo” para debug
            if len(raw) > len(best["raw_text"]):
                best["raw_text"] = raw

        except Exception as e:
            # seguimos intentando otros configs
            best["raw_text"] = best["raw_text"] or f"OCR error: {type(e).__name__}: {e}"

    return best
