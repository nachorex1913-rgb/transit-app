# transit_core/vin_decode.py
from __future__ import annotations

from typing import Dict, Any, Optional
import requests

from .validators import normalize_vin, is_valid_vin

# Tabla mínima WMI (ampliable)
_WMI_BRAND = {
    "JHM": "HONDA",
    "JH4": "ACURA",
    "1HG": "HONDA",
    "2HG": "HONDA",
}

# Año por posición 10 (index 9). Ciclo 30 años.
# Para tu operación real, asumimos años 1980-2029 (no 2030+).
_YEAR_MAP_1980_2009 = {
    "A": 1980, "B": 1981, "C": 1982, "D": 1983, "E": 1984, "F": 1985, "G": 1986,
    "H": 1987, "J": 1988, "K": 1989, "L": 1990, "M": 1991, "N": 1992, "P": 1993,
    "R": 1994, "S": 1995, "T": 1996, "V": 1997, "W": 1998, "X": 1999, "Y": 2000,
    "1": 2001, "2": 2002, "3": 2003, "4": 2004, "5": 2005, "6": 2006, "7": 2007,
    "8": 2008, "9": 2009,
}
_YEAR_MAP_2010_2029 = {
    "A": 2010, "B": 2011, "C": 2012, "D": 2013, "E": 2014, "F": 2015, "G": 2016,
    "H": 2017, "J": 2018, "K": 2019, "L": 2020, "M": 2021, "N": 2022, "P": 2023,
    "R": 2024, "S": 2025, "T": 2026, "V": 2027, "W": 2028, "X": 2029,
}

def _brand_from_wmi(vin: str) -> str:
    return _WMI_BRAND.get(vin[:3], "")

def _year_from_vin(vin: str) -> str:
    code = vin[9]
    # Heurística práctica: si es dígito 1-9 -> 2001-2009 (tu caso '7' => 2007)
    if code.isdigit():
        y = _YEAR_MAP_1980_2009.get(code)
        return str(y) if y else ""
    # Letras: por operación, preferimos 2010-2029 (más común hoy), pero si quieres, lo cambiamos.
    y = _YEAR_MAP_2010_2029.get(code) or _YEAR_MAP_1980_2009.get(code)
    return str(y) if y else ""

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
    Regla:
    - Si NHTSA trae algo útil: úsalo
    - Si no: fallback OFFLINE (marca por WMI + año por dígito 10)
    - Si ni eso: error claro
    """
    v = normalize_vin(vin)

    if not v:
        return {"error": "VIN vacío"}
    if len(v) != 17:
        return {"error": f"VIN debe tener 17 caracteres. Actual: {len(v)}"}
    if not is_valid_vin(v):
        return {"error": "VIN inválido (A-Z/0-9, sin I/O/Q)"}

    # 1) NHTSA
    try:
        out = _decode_nhtsa(v)
        if not out.get("error"):
            return out
    except Exception as e:
        out = {"error": f"NHTSA_FAIL: {type(e).__name__}: {e}"}

    # 2) OFFLINE fallback (NO inventa modelo)
    brand = _brand_from_wmi(v)
    year = _year_from_vin(v)

    if brand or year:
        return {
            "brand": brand,
            "model": "",
            "year": year,
            "trim": "",
            "engine": "",
            "vehicle_type": "",
            "body_class": "",
            "plant_country": "",
            "source": "offline_fallback",
            "note": "NHTSA no devolvió datos completos. Marca/Año estimados por estructura del VIN. Modelo debe ser manual.",
            "nhtsa_status": out.get("error", ""),
            "nhtsa_error_text": out.get("raw_error_text", ""),
            "nhtsa_error_code": out.get("raw_error_code", ""),
        }

    return {"error": "No se pudo obtener datos (NHTSA vacío y fallback sin marca/año). Ingresa manual."}
