# transit_core/vin_decode.py
from __future__ import annotations

from typing import Dict, Any, Optional, List
import requests

from .validators import normalize_vin, is_valid_vin

VIN_DECODE_VERSION = "VIN_DECODE_GENERIC_v2_2026-01-02"

# Tabla WMI mínima (ampliable). Si no está, devolvemos WMI y dejamos brand/make vacío.
_WMI_BRAND = {
    # HONDA/ACURA
    "JHM": "HONDA", "1HG": "HONDA", "2HG": "HONDA", "JH4": "ACURA",
    # TOYOTA/LEXUS
    "JTD": "TOYOTA", "JT2": "TOYOTA", "JT3": "TOYOTA", "4T1": "TOYOTA", "4T3": "TOYOTA",
    "JTH": "LEXUS", "JTJ": "LEXUS",
    # NISSAN
    "JN1": "NISSAN", "JN8": "NISSAN", "1N4": "NISSAN",
    # FORD
    "1FA": "FORD", "1FM": "FORD",
    # GM (general)
    "1G1": "CHEVROLET", "1GC": "CHEVROLET", "2G1": "CHEVROLET",
}

# Año por código en posición 10 (se repite cada 30 años).
# Estrategia: devolver ambos candidatos cuando es letra (ej A=1980 o 2010)
# y para dígitos devolver 2001-2009 (más común que 2031-2039 hoy).
_YEAR_1980_2009 = {
    "A": 1980, "B": 1981, "C": 1982, "D": 1983, "E": 1984, "F": 1985, "G": 1986,
    "H": 1987, "J": 1988, "K": 1989, "L": 1990, "M": 1991, "N": 1992, "P": 1993,
    "R": 1994, "S": 1995, "T": 1996, "V": 1997, "W": 1998, "X": 1999, "Y": 2000,
    "1": 2001, "2": 2002, "3": 2003, "4": 2004, "5": 2005, "6": 2006, "7": 2007,
    "8": 2008, "9": 2009,
}
_YEAR_2010_2039 = {
    "A": 2010, "B": 2011, "C": 2012, "D": 2013, "E": 2014, "F": 2015, "G": 2016,
    "H": 2017, "J": 2018, "K": 2019, "L": 2020, "M": 2021, "N": 2022, "P": 2023,
    "R": 2024, "S": 2025, "T": 2026, "V": 2027, "W": 2028, "X": 2029, "Y": 2030,
    "1": 2031, "2": 2032, "3": 2033, "4": 2034, "5": 2035, "6": 2036, "7": 2037,
    "8": 2038, "9": 2039,
}

def _clean(v: Optional[str]) -> str:
    """Limpia strings típicos de vPIC ('null', 'N/A', etc.)"""
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    low = s.lower()
    if low in {"null", "none", "n/a", "na", "not applicable", "unknown"}:
        return ""
    return s

def _brand_from_wmi(vin: str) -> str:
    return _WMI_BRAND.get(vin[:3], "")

def _year_candidates(vin: str) -> List[int]:
    code = vin[9]  # posición 10
    y1 = _YEAR_1980_2009.get(code)
    y2 = _YEAR_2010_2039.get(code)
    out: List[int] = []
    if y1 is not None:
        out.append(y1)
    if y2 is not None and y2 != y1:
        out.append(y2)
    return out

def _decode_nhtsa(vin: str) -> Dict[str, Any]:
    """
    NHTSA vPIC:
    - Usamos DecodeVinValuesExtended
    - Devolvemos llaves normalizadas y compatibilidad (make/brand)
    """
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/decodevinvaluesextended/{vin}?format=json"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    payload = r.json()

    results = payload.get("Results") or []
    row = results[0] if results else {}

    make = _clean(row.get("Make"))
    model = _clean(row.get("Model"))
    year = _clean(row.get("ModelYear"))

    err_text = _clean(row.get("ErrorText"))
    err_code = _clean(row.get("ErrorCode"))

    # Si NO trae nada útil, tratamos como "sin data"
    if not (make or model or year):
        return {
            "error": "NHTSA_NO_DATA",
            "raw_error_text": err_text,
            "raw_error_code": err_code,
        }

    trim = _clean(row.get("Trim")) or _clean(row.get("Series"))
    engine = _clean(row.get("EngineModel")) or _clean(row.get("EngineConfiguration"))

    return {
        "vin": vin,
        "make": make,       # COMPAT UI
        "brand": make,      # TU convención
        "model": model,
        "year": year,
        "trim": trim,
        "engine": engine,
        "vehicle_type": _clean(row.get("VehicleType")),
        "body_class": _clean(row.get("BodyClass")),
        "plant_country": _clean(row.get("PlantCountry")),
        "source": "nhtsa",
        "nhtsa_error_text": err_text,
        "nhtsa_error_code": err_code,
    }

def decode_vin(vin: str) -> Dict[str, Any]:
    """
    Pipeline genérico:
    1) NHTSA (vPIC) -> si trae algo útil, usarlo.
    2) Si NHTSA vacío/falla -> fallback OFFLINE:
       - make/brand por WMI (si la tabla lo conoce)
       - year_candidates por código de año (pos 10)
       - model siempre vacío (no se inventa)
    """

    v = normalize_vin(vin)

    base: Dict[str, Any] = {
        "vin": v or "",
        "make": "",
        "brand": "",
        "model": "",
        "year": "",
        "trim": "",
        "engine": "",
        "vehicle_type": "",
        "body_class": "",
        "plant_country": "",
        "source": "none",
        "version": VIN_DECODE_VERSION,
        "error": "",
    }

    if not v:
        base["error"] = "VIN vacío"
        return base
    if len(v) != 17:
        base["error"] = f"VIN debe tener 17 caracteres. Actual: {len(v)}"
        return base
    if not is_valid_vin(v):
        base["error"] = "VIN inválido (A-Z/0-9, sin I/O/Q)"
        return base

    # 1) NHTSA
    nhtsa_status = ""
    try:
        out = _decode_nhtsa(v)
        if not out.get("error"):
            out["version"] = VIN_DECODE_VERSION
            # Garantizar compatibilidad: make + brand siempre
            out["make"] = out.get("make", "") or out.get("brand", "")
            out["brand"] = out.get("brand", "") or out.get("make", "")
            out["source"] = "nhtsa"
            out["error"] = ""
            return out

        nhtsa_status = out.get("error", "")
        base["nhtsa_status"] = nhtsa_status
        base["nhtsa_error_text"] = out.get("raw_error_text", "")
        base["nhtsa_error_code"] = out.get("raw_error_code", "")

    except Exception as e:
        nhtsa_status = f"NHTSA_FAIL: {type(e).__name__}: {e}"
        base["nhtsa_status"] = nhtsa_status

    # 2) OFFLINE fallback
    inferred_brand = _brand_from_wmi(v)
    years = _year_candidates(v)

    # Si no podemos inferir nada, devolvemos error (y obligas manual)
    if not inferred_brand and not years:
        base["error"] = "NHTSA sin datos y fallback offline sin inferencias. Ingresa manual."
        base["source"] = "none"
        return base

    base["make"] = inferred_brand
    base["brand"] = inferred_brand
    base["model"] = ""  # NO inventar
    base["year"] = str(years[0]) if years else ""
    base["year_candidates"] = [str(y) for y in years]
    base["source"] = "offline_fallback"
    base["note"] = (
        "NHTSA no devolvió datos completos. "
        "Se infirió marca/año por estructura VIN (si posible). Modelo manual."
    )
    base["wmi"] = v[:3]
    base["error"] = ""  # no es error fatal si hay inferencia útil
    return base
