# transit_core/vin_ocr.py
from __future__ import annotations

import re
from io import BytesIO
from typing import Dict, Any, List, Optional, Tuple

from PIL import Image, ImageOps, ImageEnhance

VIN17_RE = re.compile(r"[A-Z0-9]{17}")

KEYWORDS = {
    "VIN",
    "VEHICLE",
    "ID",
    "NUMBER",
    "VEHICLEID",
    "VEHICLEIDNUMBER",
    "VEHICLEIDENTIFICATION",
    "VEHICLEIDENTIFICATIONNUMBER",
    "VEHICLEIDENTIFICATIONNO",
}

def _norm_word(w: str) -> str:
    w = (w or "").strip().upper()
    w = re.sub(r"[^A-Z0-9]", "", w)
    return w

def _preprocess_for_detect(img: Image.Image) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    g = ImageEnhance.Contrast(g).enhance(1.6)
    return g

def _preprocess_for_vin(img: Image.Image) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    g = g.resize((g.size[0] * 2, g.size[1] * 2))
    g = ImageEnhance.Contrast(g).enhance(2.2)
    g = ImageEnhance.Sharpness(g).enhance(2.0)
    return g

def _extract_vin17_only(raw: str) -> List[str]:
    if not raw:
        return []
    t = raw.upper()
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    # compacta para permitir VIN con espacios (pero el resultado final es 17 consecutivos)
    compact = re.sub(r"\s+", "", t)
    cands = [m.group(0) for m in VIN17_RE.finditer(compact)]
    out, seen = [], set()
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:10]

def _find_keyword_boxes(d: Dict[str, Any]) -> List[Tuple[int,int,int,int,str,float]]:
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
        if conf < 35:
            continue

        is_kw = (txt in KEYWORDS)
        if not is_kw and ("VIN" in txt):
            is_kw = True
        if not is_kw and ("VEHICLE" in txt and ("ID" in txt or "IDENTIFICATION" in txt)):
            is_kw = True

        if not is_kw:
            continue

        l = int(d["left"][i])
        t = int(d["top"][i])
        w = int(d["width"][i])
        h = int(d["height"][i])
        out.append((l, t, l+w, t+h, txt, conf))
    return out

def _safe_box(box: Tuple[int,int,int,int], W: int, H: int) -> Optional[Tuple[int,int,int,int]]:
    l, t, r, b = box
    l = max(0, min(W-1, l))
    t = max(0, min(H-1, t))
    r = max(0, min(W, r))
    b = max(0, min(H, b))
    if r - l < 40 or b - t < 25:
        return None
    return (l, t, r, b)

def _build_roi(W: int, H: int, anchors: List[Tuple[int,int,int,int,str,float]]) -> Optional[Tuple[int,int,int,int]]:
    if not anchors:
        return None
    anchors = sorted(anchors, key=lambda x: (-x[5], x[1]))  # conf desc, top asc
    l0, t0, r0, b0, txt0, conf0 = anchors[0]

    # agrupa palabras cercanas en la misma línea
    gl, gt, gr, gb = l0, t0, r0, b0
    for (l, t, r, b, txt, conf) in anchors[1:]:
        if abs(t - t0) <= 25:
            gl = min(gl, l); gt = min(gt, t)
            gr = max(gr, r); gb = max(gb, b)

    pad_x, pad_y = 25, 15
    roi_left = gl - pad_x
    roi_top = gt - pad_y
    roi_right = gr + int(W * 0.55)   # amplio a la derecha
    roi_bottom = gb + 200            # y abajo (por si VIN está en la siguiente línea)

    return _safe_box((roi_left, roi_top, roi_right, roi_bottom), W, H)

def extract_vin_from_image(image_bytes: bytes) -> dict:
    """
    Estricto:
    - Solo busca VIN si detecta keywords VIN / VEHICLE ID NUMBER.
    - Solo acepta 17 alfanum consecutivos (permitiendo espacios intermedios en OCR).
    - NUNCA rompe la app: captura TesseractError.
    """
    if not image_bytes:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": False, "error": ""}

    import pytesseract
    from pytesseract import Output
    from pytesseract.pytesseract import TesseractError

    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": False, "error": f"Image open error: {e}"}

    # Detect keywords (sobre imagen original, preprocess suave)
    detect_img = _preprocess_for_detect(img)
    try:
        d = pytesseract.image_to_data(detect_img, output_type=Output.DICT, config="--oem 3 --psm 6")
    except TesseractError as e:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": False, "error": f"Tesseract data error: {e}"}
    except Exception as e:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": False, "error": f"OCR data error: {e}"}

    anchors = _find_keyword_boxes(d)
    if not anchors:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": False, "error": ""}

    # Preprocess fuerte para VIN + ROI seguro
    prep = _preprocess_for_vin(img)
    W, H = prep.size
    roi_box = _build_roi(W, H, anchors)

    # Fallback ROI si por alguna razón falla el anchor ROI:
    # (zona media-izquierda típica de titles)
    if roi_box is None:
        roi_box = _safe_box((0, int(H*0.18), int(W*0.75), int(H*0.60)), W, H)

    if roi_box is None:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": True, "error": "ROI inválido"}

    l, t, r, b = roi_box
    roi = prep.crop((l, t, r, b))

    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    cfgs = [
        f"--oem 3 --psm 7 -c tessedit_char_whitelist={whitelist}",
        f"--oem 3 --psm 6 -c tessedit_char_whitelist={whitelist}",
    ]

    best_raw = ""
    for cfg in cfgs:
        try:
            raw = pytesseract.image_to_string(roi, config=cfg) or ""
        except TesseractError as e:
            # no rompas la app, devuelve error
            return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": True, "error": f"Tesseract string error: {e}"}
        except Exception as e:
            return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": True, "error": f"OCR string error: {e}"}

        if len(raw) > len(best_raw):
            best_raw = raw

        cands = _extract_vin17_only(raw)
        if cands:
            return {
                "vin": cands[0],
                "confidence": 0.92 if len(cands) == 1 else 0.88,
                "raw_text": raw,
                "candidates": cands,
                "found_keywords": True,
                "error": ""
            }

    return {"vin": "", "confidence": 0.0, "raw_text": best_raw, "candidates": [], "found_keywords": True, "error": ""}
