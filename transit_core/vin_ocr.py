# transit_core/vin_ocr.py
from __future__ import annotations

import re
from io import BytesIO
from typing import List, Dict, Any, Optional, Tuple

from PIL import Image, ImageOps, ImageEnhance

from transit_core.validators import is_valid_vin_strict

VIN_CAND_RE = re.compile(r"[A-Z0-9]{17}")


def _preprocess(img: Image.Image) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    g = g.resize((g.size[0] * 2, g.size[1] * 2))
    g = ImageEnhance.Contrast(g).enhance(2.2)
    g = ImageEnhance.Sharpness(g).enhance(2.0)
    return g


def _normalize_text(t: str) -> str:
    t = (t or "").upper()
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


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


def _extract_candidates(raw_text: str) -> List[str]:
    t = _normalize_text(raw_text)
    compact = re.sub(r"\s+", "", t)

    cands = []
    for m in VIN_CAND_RE.finditer(compact):
        cands.append(m.group(0))

    # variantes corregidas
    expanded = []
    for c in cands:
        expanded.append(c)
        expanded.append(_fix_common_ocr(c))

    # unique + estrictas
    out = []
    seen = set()
    for c in expanded:
        c = c.strip().upper()
        if len(c) == 17 and c not in seen:
            seen.add(c)
            out.append(c)

    return out[:20]


def _find_roi_box_from_ocr_data(data_rows: List[Dict[str, Any]]) -> Optional[Tuple[int, int, int, int]]:
    """
    Busca las palabras 'VEHICLE', 'ID', 'NUMBER' para ubicar el área y luego
    devolver un ROI debajo donde usualmente está el VIN.
    """
    words = []
    for r in data_rows:
        txt = (r.get("text") or "").strip().upper()
        if not txt:
            continue
        try:
            conf = float(r.get("conf", -1))
        except Exception:
            conf = -1
        if conf < 40:  # descarta basura
            continue
        words.append({
            "text": txt,
            "left": int(r["left"]),
            "top": int(r["top"]),
            "width": int(r["width"]),
            "height": int(r["height"]),
        })

    if not words:
        return None

    # encuentra tokens clave
    key_idxs = []
    for i, w in enumerate(words):
        if w["text"] in ("VEHICLE", "VEHICLEID", "VEHICLEIDNUMBER"):
            key_idxs.append(i)

    # Plan A: si aparece VEHICLE, construimos ROI con las siguientes palabras cercanas
    if key_idxs:
        i0 = key_idxs[0]
        base = words[i0]
        x1 = base["left"]
        y1 = base["top"]
        x2 = base["left"] + base["width"]
        y2 = base["top"] + base["height"]

        # expande buscando palabras cerca en la misma línea
        for w in words:
            if abs(w["top"] - base["top"]) <= 20:
                x1 = min(x1, w["left"])
                x2 = max(x2, w["left"] + w["width"])
                y1 = min(y1, w["top"])
                y2 = max(y2, w["top"] + w["height"])

        # ROI debajo de esa línea (donde está el VIN)
        roi_top = y2 + 5
        roi_bottom = roi_top + 140  # suficiente para 1-2 líneas
        roi_left = max(0, x1 - 30)
        roi_right = x2 + 450  # VIN suele estar a la derecha
        return (roi_left, roi_top, roi_right, roi_bottom)

    return None


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

    try:
        import pytesseract
        from pytesseract import Output
    except Exception as e:
        return {"vin": "", "confidence": 0.0, "raw_text": f"OCR import error: {e}", "candidates": []}

    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img2 = _preprocess(img)

    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    base_cfg = f"-c tessedit_char_whitelist={whitelist}"

    # 1) Primero obtenemos data para ubicar ROI
    try:
        d = pytesseract.image_to_data(img2, config=f"--oem 3 --psm 6 {base_cfg}", output_type=Output.DICT)
        rows = []
        n = len(d.get("text", []))
        for i in range(n):
            rows.append({
                "text": d["text"][i],
                "conf": d["conf"][i],
                "left": d["left"][i],
                "top": d["top"][i],
                "width": d["width"][i],
                "height": d["height"][i],
            })
        roi = _find_roi_box_from_ocr_data(rows)
    except Exception:
        roi = None

    # 2) OCR en ROI si existe; si no, OCR global
    ocr_targets = []
    if roi:
        l, t, r, b = roi
        # clamp
        l = max(0, l); t = max(0, t)
        r = min(img2.size[0], r); b = min(img2.size[1], b)
        if r > l and b > t:
            ocr_targets.append(img2.crop((l, t, r, b)))

    if not ocr_targets:
        ocr_targets.append(img2)

    configs = [
        f"--oem 3 --psm 7 {base_cfg}",   # 1 línea
        f"--oem 3 --psm 6 {base_cfg}",   # bloque
        f"--oem 3 --psm 11 {base_cfg}",  # disperso
    ]

    best_raw = ""
    best_candidates = []

    for target in ocr_targets:
        for cfg in configs:
            raw = ""
            try:
                raw = pytesseract.image_to_string(target, config=cfg) or ""
            except Exception:
                continue

            if len(raw) > len(best_raw):
                best_raw = raw

            cands = _extract_candidates(raw)
            if cands:
                best_candidates = cands
                # prioriza estrictos (check digit)
                strict = [c for c in cands if is_valid_vin_strict(c)]
                if strict:
                    return {"vin": strict[0], "confidence": 0.92, "raw_text": raw, "candidates": strict[:10]}
                # si no hay estrictos, devuelve candidatos igual
                return {"vin": cands[0], "confidence": 0.60, "raw_text": raw, "candidates": cands[:10]}

    return {"vin": "", "confidence": 0.0, "raw_text": best_raw, "candidates": best_candidates}
