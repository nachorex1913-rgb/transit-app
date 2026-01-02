# transit_core/vin_decode.py
from __future__ import annotations

from typing import Dict, Any, Optional
import requests

from .validators import normalize_vin, is_valid_vin

VIN_DECODE_VERSION = "VIN_DECODE_GENERIC_v1_2026-01-02"

# Tabla WMI mínima (ampliable). Si no está, devolvemos WMI y dejamos brand vacío.
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

def _brand_from_wmi(vin: str) -> str:
    return _WMI_BRAND.get(vin[:3], "")

def _year_candidates(vin: str) -> list[int]:
    code = vin[9]  # posición 10
    y1 = _YEAR_1980_2009.get(code)
    y2 = _YEAR_2010_2039.get(code)
    # Para dígitos preferimos 2001-2009 (y1) como primer candidato.
    # Para letras devolvemos ambos candidatos.
    out = []
    if y1 is not None:
        out.append(y1)
    if y2 is not None and y2 != y1:
        out.append(y2)
    return out

def _decode_nhtsa(vin: str) -> Dict[str, Any]:
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/decodevinvaluesextended/{vin}?format=json"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    payload = r.json()
    results = payload.get("Results") or []
    row = results[0] if results else {}

    make = (row.get("Make") or "").strip()
    model = (row.get("Model") or "").strip()
    year = (row.get("ModelYear") or "").strip()

    err_text = (row.get("ErrorText") or "").strip()
    err_code = (row.get("ErrorCode") or "").strip()

    # Si NO trae nada útil, tratamos como "sin data"
    if not (make or model or year):
        return {"error": "NHTSA_NO_DATA", "raw_error_text": err_text, "raw_error_code": err_code}

    return {
        "brand": make,
        "model": model,
        "year": year,
        "trim": (row.get("Trim") or row.get("Series") or "").strip(),
        "engine": (row.get("EngineModel") or row.get("EngineConfiguration") or "").strip(),
        "vehicle_type": (row.get("VehicleType") or "").strip(),
        "body_class": (row.get("BodyClass") or "").strip(),
        "plant_country": (row.get("PlantCountry") or "").strip(),
        "source": "nhtsa",
    }

def decode_vin(vin: str) -> Dict[str, Any]:
    """
    Pipeline genérico:
    1) NHTSA (vPIC) -> si trae algo útil, usarlo.
    2) Si NHTSA vacío/falla -> fallback OFFLINE:
       - brand por WMI (si la tabla lo conoce)
       - year_candidates por código de año (pos 10)
       - model siempre vacío (no se inventa)
    """
    v = normalize_vin(vin)

    if not v:
        return {"error": "VIN vacío", "version": VIN_DECODE_VERSION}
    if len(v) != 17:
        return {"error": f"VIN debe tener 17 caracteres. Actual: {len(v)}", "version": VIN_DECODE_VERSION}
    if not is_valid_vin(v):
        return {"error": "VIN inválido (A-Z/0-9, sin I/O/Q)", "version": VIN_DECODE_VERSION}

    # 1) NHTSA
    nhtsa_status = ""
    nhtsa_text = ""
    nhtsa_code = ""
    try:
        out = _decode_nhtsa(v)
        if not out.get("error"):
            out["version"] = VIN_DECODE_VERSION
            return out
        nhtsa_status = out.get("error", "")
        nhtsa_text = out.get("raw_error_text", "")
        nhtsa_code = out.get("raw_error_code", "")
    except Exception as e:
        nhtsa_status = f"NHTSA_FAIL: {type(e).__name__}: {e}"

    # 2) OFFLINE fallback
    brand = _brand_from_wmi(v)
    years = _year_candidates(v)

    # Si no podemos inferir nada, devolvemos error (y obligas manual)
    if not brand and not years:
        return {
            "error": "NHTSA sin datos y fallback offline sin inferencias. Ingresa manual.",
            "version": VIN_DECODE_VERSION,
            "nhtsa_status": nhtsa_status,
        }

    return {
        "brand": brand,              # puede venir vacío si WMI no está en tabla
        "model": "",                 # NO inventar
        "year": str(years[0]) if years else "",
        "year_candidates": [str(y) for y in years],
        "trim": "",
        "engine": "",
        "vehicle_type": "",
        "body_class": "",
        "plant_country": "",
        "source": "offline_fallback",
        "note": "NHTSA no devolvió datos completos. Se inferió marca/año por estructura VIN (si posible). Modelo manual.",
        "wmi": v[:3],
        "nhtsa_status": nhtsa_status,
        "nhtsa_error_text": nhtsa_text,
        "nhtsa_error_code": nhtsa_code,
        "version": VIN_DECODE_VERSION,
    }
