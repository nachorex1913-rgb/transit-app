# transit_core/vin_ocr.py
from __future__ import annotations

import re
from io import BytesIO
from typing import Dict, Any, List, Tuple, Optional

from PIL import Image, ImageOps, ImageEnhance

# VIN estricto: 17 alfanum consecutivos, sin I/O/Q
VIN17_RE = re.compile(r"^[A-Z0-9]{17}$")
INVALID_VIN_CHARS = set("IOQ")

# Keywords (para priorizar, NO para “permitir”)
KW_PATTERNS = [
    re.compile(r"\bVIN\b", re.I),
    re.compile(r"\bVEHICLE\b", re.I),
    re.compile(r"\bID\b", re.I),
    re.compile(r"\bNUMBER\b", re.I),
    re.compile(r"\bVEHICLE\s*ID\b", re.I),
    re.compile(r"\bVEHICLE\s*ID\s*NUMBER\b", re.I),
    re.compile(r"\bVEHICLE\s*IDENTIFICATION\b", re.I),
    re.compile(r"\bVEHICLE\s*IDENTIFICATION\s*NUMBER\b", re.I),
]

def _is_valid_vin_strict(v: str) -> bool:
    if not v or len(v) != 17:
        return False
    v = v.strip().upper()
    if not VIN17_RE.match(v):
        return False
    if any(ch in INVALID_VIN_CHARS for ch in v):
        return False
    return True

def _clean_token(t: str) -> str:
    # SOLO A-Z0-9, sin espacios, sin símbolos
    t = (t or "").upper()
    t = re.sub(r"[^A-Z0-9]", "", t)
    return t

def _preprocess(img: Image.Image) -> Image.Image:
    """
    Preprocesado para VIN en documentos: contraste + nitidez + upscale.
    """
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    # upscale para mejorar OCR
    g = g.resize((g.size[0] * 2, g.size[1] * 2))
    g = ImageEnhance.Contrast(g).enhance(2.2)
    g = ImageEnhance.Sharpness(g).enhance(2.0)
    return g

def _contains_keywords(line_text: str) -> bool:
    if not line_text:
        return False
    for p in KW_PATTERNS:
        if p.search(line_text):
            return True
    return False

def _group_lines(data: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    """
    Agrupa palabras por línea usando (block_num, par_num, line_num) de pytesseract.image_to_data.
    Devuelve: lista de líneas, cada línea es lista de tokens con bbox/conf/text.
    """
    lines_map: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
    n = len(data.get("text", []))

    for i in range(n):
        text = data["text"][i]
        if not text or not str(text).strip():
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1.0
        # baja el umbral, porque VIN a veces viene con conf medio; luego filtramos por forma
        if conf < 25:
            continue

        token = {
            "text": str(text),
            "clean": _clean_token(str(text)),
            "conf": conf,
            "left": int(data["left"][i]),
            "top": int(data["top"][i]),
            "width": int(data["width"][i]),
            "height": int(data["height"][i]),
            "block": int(data.get("block_num", [0]*n)[i]),
            "par": int(data.get("par_num", [0]*n)[i]),
            "line": int(data.get("line_num", [0]*n)[i]),
            "word": int(data.get("word_num", [0]*n)[i]),
        }

        key = (token["block"], token["par"], token["line"])
        lines_map.setdefault(key, []).append(token)

    # Ordena tokens dentro de cada línea por X (left)
    lines = []
    for key, toks in lines_map.items():
        toks = sorted(toks, key=lambda t: t["left"])
        lines.append(toks)

    # Ordena líneas por Y (top)
    lines.sort(key=lambda toks: min(t["top"] for t in toks))
    return lines

def _bbox_union(tokens: List[Dict[str, Any]]) -> Tuple[int,int,int,int]:
    l = min(t["left"] for t in tokens)
    t = min(t["top"] for t in tokens)
    r = max(tk["left"] + tk["width"] for tk in tokens)
    b = max(tk["top"] + tk["height"] for tk in tokens)
    return (l, t, r, b)

def _line_text(tokens: List[Dict[str, Any]]) -> str:
    return " ".join(str(t["text"]) for t in tokens)

def _generate_vin_candidates_from_line(tokens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Genera candidatos VIN SOLO desde la línea, concatenando tokens adyacentes (máx 3 tokens),
    para evitar “adivinanzas” por concatenación global.
    """
    cands: List[Dict[str, Any]] = []

    # tokens limpios no vacíos
    toks = [t for t in tokens if t["clean"]]
    if not toks:
        return cands

    # 1) token único que ya sea 17
    for t in toks:
        if len(t["clean"]) == 17 and _is_valid_vin_strict(t["clean"]):
            cands.append({
                "vin": t["clean"],
                "tokens": [t],
                "avg_conf": t["conf"],
            })

    # 2) concatenación de 2-3 tokens contiguos en la línea
    # (pytesseract puede separar un VIN en 2 pedazos)
    max_join = 3
    for i in range(len(toks)):
        joined = ""
        used: List[Dict[str, Any]] = []
        confs: List[float] = []
        for j in range(i, min(len(toks), i + max_join)):
            # sólo concatenamos tokens “cercanos” en X
            if used:
                prev = used[-1]
                gap = toks[j]["left"] - (prev["left"] + prev["width"])
                # si el gap es muy grande, rompe (probablemente no es parte del mismo VIN)
                if gap > 120:
                    break

            joined += toks[j]["clean"]
            used.append(toks[j])
            confs.append(float(toks[j]["conf"]))

            if len(joined) == 17 and _is_valid_vin_strict(joined):
                cands.append({
                    "vin": joined,
                    "tokens": used.copy(),
                    "avg_conf": sum(confs) / max(1, len(confs)),
                })
            elif len(joined) > 17:
                break

    return cands

def extract_vin_from_image(image_bytes: bytes) -> Dict[str, Any]:
    """
    Aduana-grade:
    - NO “adivina”: solo devuelve VIN si proviene de una línea OCR real (token o 2-3 tokens contiguos).
    - Keywords sólo aumentan prioridad/ confianza, pero no inventan VIN.
    """
    if not image_bytes:
        return {"vin": "", "confidence": 0.0, "candidates": [], "found_keywords": False, "raw_text": "", "error": ""}

    import pytesseract
    from pytesseract import Output
    from pytesseract.pytesseract import TesseractError

    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        return {"vin": "", "confidence": 0.0, "candidates": [], "found_keywords": False, "raw_text": "", "error": f"Image open error: {e}"}

    proc = _preprocess(img)

    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    cfg_data = "--oem 3 --psm 6"
    cfg_text = "--oem 3 --psm 6 -c tessedit_char_whitelist=" + whitelist

    try:
        data = pytesseract.image_to_data(proc, output_type=Output.DICT, config=cfg_data)
        raw_text = pytesseract.image_to_string(proc, config=cfg_text) or ""
    except TesseractError as e:
        return {"vin": "", "confidence": 0.0, "candidates": [], "found_keywords": False, "raw_text": "", "error": f"Tesseract error: {e}"}
    except Exception as e:
        return {"vin": "", "confidence": 0.0, "candidates": [], "found_keywords": False, "raw_text": "", "error": f"OCR error: {e}"}

    lines = _group_lines(data)

    # detecta líneas con keywords
    keyword_lines_idx = []
    for idx, ln in enumerate(lines):
        if _contains_keywords(_line_text(ln)):
            keyword_lines_idx.append(idx)

    found_keywords = bool(keyword_lines_idx)

    # genera candidatos por línea
    all_cands: List[Dict[str, Any]] = []
    for idx, ln in enumerate(lines):
        line_cands = _generate_vin_candidates_from_line(ln)
        if not line_cands:
            continue
        for c in line_cands:
            c["line_idx"] = idx
            c["line_text"] = _line_text(ln)
            c["bbox"] = _bbox_union(c["tokens"])
            all_cands.append(c)

    if not all_cands:
        # NO inventar, no compactar global
        return {"vin": "", "confidence": 0.0, "candidates": [], "found_keywords": found_keywords, "raw_text": raw_text, "error": ""}

    # scoring: prioridad por cercanía a línea keyword (misma línea o siguiente)
    def score(c: Dict[str, Any]) -> float:
        base = c["avg_conf"] / 100.0  # 0..1 aprox
        s = base

        if found_keywords:
            li = c["line_idx"]
            # mejor: misma línea que keyword
            if li in keyword_lines_idx:
                s += 0.65
            # segundo: línea inmediatamente abajo de keyword
            if (li - 1) in keyword_lines_idx or (li + 1) in keyword_lines_idx:
                s += 0.35

        # bonus pequeño si en el texto de la línea aparecen palabras VIN/VEHICLE/ID
        if _contains_keywords(c.get("line_text", "")):
            s += 0.25

        # penaliza si conf muy baja
        if c["avg_conf"] < 35:
            s -= 0.20

        return s

    scored = [(score(c), c) for c in all_cands]
    scored.sort(key=lambda x: x[0], reverse=True)

    best = scored[0][1]
    best_score = float(scored[0][0])

    # candidates para UI
    top_vins = []
    seen = set()
    for sc, c in scored[:10]:
        v = c["vin"]
        if v not in seen:
            seen.add(v)
            top_vins.append(v)

    # confianza final:
    # - si keywords presentes y está cerca -> alta
    # - si no keywords -> media (pero válido y NO inventado)
    if found_keywords and best_score >= 1.0:
        conf = 0.93
    elif found_keywords:
        conf = 0.86
    else:
        conf = 0.72

    return {
        "vin": best["vin"],
        "confidence": conf,
        "candidates": top_vins,
        "found_keywords": found_keywords,
        "raw_text": raw_text,
        "error": "",
        # debug útil (no rompe tu UI si no lo usas)
        "debug": {
            "best_line_text": best.get("line_text", ""),
            "best_bbox": best.get("bbox", None),
            "keyword_lines_count": len(keyword_lines_idx),
        }
    }
