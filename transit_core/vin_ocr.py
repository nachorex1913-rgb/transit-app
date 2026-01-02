# transit_core/vin_ocr.py
from __future__ import annotations

import re
from io import BytesIO
from typing import Dict, Any, List, Tuple, Optional

from PIL import Image, ImageOps, ImageEnhance

# VIN: 17 alfanum consecutivos
VIN17_RE = re.compile(r"[A-Z0-9]{17}")

# VIN real no usa I, O, Q (estricto)
INVALID_VIN_CHARS = set("IOQ")

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

def _preprocess(img: Image.Image, strong: bool) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    if strong:
        g = g.resize((g.size[0] * 2, g.size[1] * 2))
        g = ImageEnhance.Contrast(g).enhance(2.2)
        g = ImageEnhance.Sharpness(g).enhance(2.0)
    else:
        g = ImageEnhance.Contrast(g).enhance(1.6)
    return g

def _extract_vin17_candidates(text: str) -> List[str]:
    """
    Extrae VIN17:
    - Permite espacios/saltos en OCR, luego compacta.
    - Retorna SOLO 17 consecutivos.
    """
    if not text:
        return []
    t = text.upper()
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    compact = re.sub(r"\s+", "", t)
    cands = [m.group(0) for m in VIN17_RE.finditer(compact)]
    # únicos en orden
    out, seen = [], set()
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:20]

def _is_valid_vin_strict(v: str) -> bool:
    if not v or len(v) != 17:
        return False
    if any(ch in INVALID_VIN_CHARS for ch in v):
        return False
    return bool(re.fullmatch(r"[A-Z0-9]{17}", v))

def _safe_box(box: Tuple[int,int,int,int], W: int, H: int) -> Optional[Tuple[int,int,int,int]]:
    l, t, r, b = box
    l = max(0, min(W - 1, int(l)))
    t = max(0, min(H - 1, int(t)))
    r = max(0, min(W, int(r)))
    b = max(0, min(H, int(b)))
    if (r - l) < 40 or (b - t) < 25:
        return None
    return (l, t, r, b)

def _keyword_boxes(data: Dict[str, Any]) -> List[Tuple[int,int,int,int,str,float]]:
    """
    Retorna cajas de palabras clave detectadas.
    """
    out = []
    n = len(data.get("text", []))
    for i in range(n):
        txt = _norm_word(data["text"][i])
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1.0
        if conf < 35:
            continue

        is_kw = (txt in KEYWORDS) or ("VIN" in txt) or ("VEHICLE" in txt and ("ID" in txt or "IDENTIFICATION" in txt))
        if not is_kw:
            continue

        l = int(data["left"][i])
        t = int(data["top"][i])
        w = int(data["width"][i])
        h = int(data["height"][i])
        out.append((l, t, l + w, t + h, txt, conf))
    return out

def _score_candidate(vin: str, vin_box: Tuple[int,int,int,int], kw_boxes: List[Tuple[int,int,int,int,str,float]]) -> float:
    """
    Scoring:
    - Base: 1.0 si VIN estricto (sin I/O/Q), 0.6 si no estricto pero 17 válido.
    - Bonus por cercanía a keywords.
    """
    base = 1.0 if _is_valid_vin_strict(vin) else 0.6

    if not kw_boxes:
        return base

    vl, vt, vr, vb = vin_box
    vcx = (vl + vr) / 2
    vcy = (vt + vb) / 2

    best = 0.0
    for (kl, kt, kr, kb, txt, conf) in kw_boxes:
        kcx = (kl + kr) / 2
        kcy = (kt + kb) / 2
        # distancia normalizada
        dist = ((vcx - kcx) ** 2 + (vcy - kcy) ** 2) ** 0.5
        # si está relativamente cerca, bonus
        if dist < 300:       # cerca
            best = max(best, 0.35)
        elif dist < 600:     # medio
            best = max(best, 0.20)

    return base + best

def extract_vin_from_image(image_bytes: bytes) -> Dict[str, Any]:
    """
    Nuevo flujo (como pediste):
    1) OCR general -> buscar VIN17 (sin depender de keywords)
    2) Luego keywords -> rank/confianza
    3) Si no hay keywords, igual devuelve el VIN17
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

    # Preprocess fuerte para maximizar lectura
    proc = _preprocess(img, strong=True)

    # 1) OCR a nivel documento (texto)
    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    cfg_text = "--oem 3 --psm 6 -c tessedit_char_whitelist=" + whitelist

    try:
        raw_full = pytesseract.image_to_string(proc, config=cfg_text) or ""
    except TesseractError as e:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": False, "error": f"Tesseract string error: {e}"}
    except Exception as e:
        return {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "found_keywords": False, "error": f"OCR string error: {e}"}

    # candidatos VIN17 desde OCR global
    cands = _extract_vin17_candidates(raw_full)
    # filtra ruido: mantener los que son estrictos primero
    cands_sorted = sorted(cands, key=lambda v: (0 if _is_valid_vin_strict(v) else 1))

    # 2) Para scoring por cercanía a keywords, necesitamos boxes:
    #   - boxes de keywords
    #   - boxes de cada palabra, para aproximar el box del VIN candidato
    try:
        data = pytesseract.image_to_data(proc, output_type=Output.DICT, config="--oem 3 --psm 6")
    except Exception:
        data = {"text": [], "conf": [], "left": [], "top": [], "width": [], "height": []}

    kw_boxes = _keyword_boxes(data)
    found_keywords = bool(kw_boxes)

    # Construye “vin boxes” aproximados desde tokens del data:
    # Tomamos cada token del OCR data y buscamos tokens con 17 chars.
    vin_boxes = []
    n = len(data.get("text", []))
    for i in range(n):
        tok = _norm_word(data["text"][i])
        if len(tok) == 17 and re.fullmatch(r"[A-Z0-9]{17}", tok):
            l = int(data["left"][i])
            t = int(data["top"][i])
            w = int(data["width"][i])
            h = int(data["height"][i])
            vin_boxes.append((tok, (l, t, l + w, t + h)))

    # Score: si tenemos box real, usamos scoring, si no, score base
    scored = []
    for vin in cands_sorted:
        box = None
        for (v_tok, v_box) in vin_boxes:
            if v_tok == vin:
                box = v_box
                break
        if box is None:
            # box desconocido -> score base
            score = 1.0 if _is_valid_vin_strict(vin) else 0.6
        else:
            score = _score_candidate(vin, box, kw_boxes)
        scored.append((score, vin))

    scored.sort(reverse=True, key=lambda x: x[0])

    best_vin = scored[0][1] if scored else (cands_sorted[0] if cands_sorted else "")
    best_score = float(scored[0][0]) if scored else 0.0

    # Confidence final:
    # - Si hay keywords y score alto => 0.90+
    # - Si no hay keywords pero VIN estricto => 0.75
    # - Si no hay keywords y no estricto => 0.55
    if not best_vin:
        return {"vin": "", "confidence": 0.0, "raw_text": raw_full, "candidates": cands_sorted[:10], "found_keywords": found_keywords, "error": ""}

    if found_keywords and best_score >= 1.2:
        conf = 0.92
    elif found_keywords:
        conf = 0.85
    else:
        conf = 0.75 if _is_valid_vin_strict(best_vin) else 0.55

    return {
        "vin": best_vin,
        "confidence": conf,
        "raw_text": raw_full,
        "candidates": [v for (_, v) in scored[:10]] if scored else cands_sorted[:10],
        "found_keywords": found_keywords,
        "error": ""
    }
