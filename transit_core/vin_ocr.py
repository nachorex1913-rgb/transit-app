# transit_core/vin_ocr.py
from __future__ import annotations

from typing import Dict, Any, List, Tuple
import re
import io

from PIL import Image, ImageOps, ImageEnhance, ImageFilter

try:
    import pytesseract
except Exception as e:
    pytesseract = None


VIN_REGEX = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
VIN_OCR_VERSION = "VIN_OCR_ROBUST_v2_2026-01-02"


def _preprocess_variants(img: Image.Image) -> List[Image.Image]:
    """
    Genera varias versiones preprocesadas para aumentar probabilidad de lectura.
    No recorta (porque Streamlit no tiene crop nativo), pero mejora contraste, nitidez, binarización, upscale.
    """
    variants: List[Image.Image] = []

    # Normalize orientation & convert
    im = ImageOps.exif_transpose(img)
    im = im.convert("RGB")

    # Base grayscale
    gray = ImageOps.grayscale(im)

    # Upscale (x2, x3)
    for scale in (2, 3):
        up = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)

        # Contrast boost
        c = ImageEnhance.Contrast(up).enhance(2.0)
        s = ImageEnhance.Sharpness(c).enhance(2.0)

        # Light denoise + sharpen
        s2 = s.filter(ImageFilter.MedianFilter(size=3))
        s2 = s2.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=3))

        # Binarization (two thresholds)
        for thr in (150, 180):
            bw = s2.point(lambda p: 255 if p > thr else 0).convert("L")
            variants.append(bw)

        # Also keep sharpened grayscale
        variants.append(s2)

    return variants


def _tesseract_text_and_conf(im: Image.Image) -> Tuple[str, float]:
    """
    Extrae texto y una confianza aproximada usando image_to_data (si está disponible).
    """
    if pytesseract is None:
        return "", 0.0

    config = (
        "--oem 3 "
        "--psm 6 "
        "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    )

    try:
        data = pytesseract.image_to_data(im, config=config, output_type=pytesseract.Output.DICT)
        words = data.get("text", []) or []
        confs = data.get("conf", []) or []
        # reconstruir texto
        txt = " ".join([w for w in words if w and w.strip()])
        # confianza promedio de los tokens válidos
        vals = []
        for c in confs:
            try:
                v = float(c)
                if v >= 0:
                    vals.append(v)
            except Exception:
                pass
        avg_conf = (sum(vals) / len(vals)) if vals else 0.0
        return txt, avg_conf
    except Exception:
        # fallback simple
        try:
            txt = pytesseract.image_to_string(im, config=config) or ""
            return txt, 0.0
        except Exception:
            return "", 0.0


def extract_vin_from_image(image_bytes: bytes) -> Dict[str, Any]:
    """
    Devuelve:
    {
      vin: "" (mejor candidato),
      candidates: [VIN...],
      confidence: float,
      raw_text: texto OCR,
      error: ""
    }

    Reglas:
    - NO inventa VIN
    - Solo acepta regex VIN 17 chars sin I/O/Q
    """
    out = {
        "vin": "",
        "candidates": [],
        "confidence": 0.0,
        "raw_text": "",
        "error": "",
        "version": VIN_OCR_VERSION,
    }

    if pytesseract is None:
        out["error"] = "pytesseract no está disponible en el entorno."
        return out

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        out["error"] = f"No se pudo abrir la imagen: {type(e).__name__}: {e}"
        return out

    variants = _preprocess_variants(img)

    best_candidates: List[str] = []
    best_conf = 0.0
    best_raw = ""

    # Probar varias variantes y escoger la que entregue más/ mejores candidatos
    for v in variants:
        raw, conf = _tesseract_text_and_conf(v)
        raw_up = (raw or "").upper()

        # Normalizar: quitar espacios y separadores raros que rompen VIN
        compact = re.sub(r"[^A-Z0-9]", "", raw_up)

        # Buscar VIN en raw y en compact
        found = set(VIN_REGEX.findall(raw_up)) | set(VIN_REGEX.findall(compact))
        cands = sorted(found)

        # Score: prioriza más candidatos y mayor conf aproximada
        score = (len(cands) * 100.0) + conf

        best_score = (len(best_candidates) * 100.0) + best_conf
        if score > best_score:
            best_candidates = cands
            best_conf = conf
            best_raw = raw

    out["raw_text"] = best_raw or ""
    out["confidence"] = float(best_conf or 0.0)
    out["candidates"] = best_candidates

    if best_candidates:
        out["vin"] = best_candidates[0]
        return out

    out["error"] = "No se encontró ningún VIN válido de 17 caracteres (sin I/O/Q) en la imagen."
    return out
