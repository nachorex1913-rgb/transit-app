# transit_core/vin_decode.py
from __future__ import annotations

from typing import Dict, Any, Optional
import requests

from .validators import normalize_vin, is_valid_vin

# --------------------------
# OFFLINE: año por posición 10 (VIN year code)
# --------------------------
_YEAR_CODES = {
    # 1980-2009
    "A": 1980, "B": 1981, "C": 1982, "D": 1983, "E": 1984, "F": 1985, "G": 1986,
    "H": 1987, "J": 1988, "K": 1989, "L": 1990, "M": 1991, "N": 1992, "P": 1993,
    "R": 1994, "S": 1995, "T": 1996, "V": 1997, "W": 1998, "X": 1999, "Y": 2000,
    "1": 2001, "2": 2002, "3": 2003, "4": 2004, "5": 2005, "6": 2006, "7": 2007,
    "8": 2008, "9": 2009,
    # 2010-2039 (se repite el ciclo)
    "A2": 2010, "B2": 2011, "C2": 2012, "D2": 2013, "E2": 2014, "F2": 2015, "G2": 2016,
    "H2": 2017, "J2": 2018, "K2": 2019, "L2": 2020, "M2": 2021, "N2": 2022, "P2": 2023,
    "R2": 2024, "S2": 2025, "T2": 2026, "V2": 2027, "W2": 2028, "X2": 2029, "Y2": 2030,
    "1_2": 2031, "2_2": 2032, "3_2": 2033, "4_2": 2034, "5_2": 2035, "6_2": 2036,
    "7_2": 2037, "8_2": 2038, "9_2": 2039,
}

# --------------------------
# OFFLINE: WMI (3 primeros) -> marca aproximada (tabla mínima, ampliable)
# --------------------------
_WMI_BRAND = {
    # Toyota / Lexus (ejemplos)
    "4T1": "TOYOTA", "4T3": "TOYOTA", "4TA": "TOYOTA",
    "JTD": "TOYOTA", "JT2": "TOYOTA", "JT3": "TOYOTA",
    "JTH": "LEXUS", "JTJ": "LEXUS",

    # Honda / Acura
    "1HG": "HONDA", "2HG": "HONDA", "JHM": "HONDA",
    "JH4": "ACURA",

    # Ford
    "1FA": "FORD", "1FB": "FORD", "1FM": "FORD",

    # GM (muy general)
    "1G1": "CHEVROLET", "1G2": "PONTIAC", "1GC": "CHEVROLET",
    "1GN": "CHEVROLET", "2G1": "CHEVROLET",

    # Nissan
    "1N4": "NISSAN", "JN1": "NISSAN", "JN8": "NISSAN",

    # etc... (ampliable con el tiempo)
}

def _year_from_vin(vin: str) -> Optional[int]:
    """
    Posición 10 (index 9) da el año, pero se repite cada 30 años.
    Aquí damos el ciclo más probable:
    - Si es letra A-Y: puede ser 1980-2000 o 2010-2030
    - Si es 1-9: 2001-2009 o 2031-2039
    Estrategia: si el VIN parece viejo por contexto, puedes ajustar, pero aquí damos el ciclo moderno si aplica.
    """
    code = vin[9]
    if code in "ABCDEFGHJKLMNPRSTVWXY":
        # preferir 2010+ si posible
        y2 = _YEAR_CODES.get(code + "2")
        if y2:
            return y2
        return _YEAR_CODES.get(code)
    if code.isdigit():
        # preferir 2001-2009
        return _YEAR_CODES.get(code)
    return None

def _brand_from_wmi(vin: str) -> str:
    wmi = vin[:3]
    return _WMI_BRAND.get(wmi, "")

# --------------------------
# Fuente 1: NHTSA vPIC
# --------------------------
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

    if not (make and model and year):
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

# --------------------------
# Decoder PRO
# --------------------------
def decode_vin(vin: str) -> Dict[str, Any]:
    """
    PRO pipeline:
    1) NHTSA (gratis) -> si trae datos, listo
    2) Si no trae, fallback OFFLINE -> marca aproximada (WMI) + año (pos 10)
       y obliga validación manual de modelo.
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
    except requests.exceptions.Timeout:
        # sigue a offline
        out = {"error": "NHTSA_TIMEOUT"}
    except Exception as e:
        out = {"error": f"NHTSA_FAIL: {type(e).__name__}: {e}"}

    # 2) OFFLINE fallback (no inventa modelo)
    year = _year_from_vin(v)
    brand = _brand_from_wmi(v)

    if not year and not brand:
        return {
            "error": "NO_DATA_SOURCES",
            "detail": "NHTSA no devolvió datos y offline no pudo inferir año/marca. Ingresa manual.",
        }

    return {
        "brand": brand,
        "model": "",  # NO inventar
        "year": str(year) if year else "",
        "trim": "",
        "engine": "",
        "vehicle_type": "",
        "body_class": "",
        "plant_country": "",
        "source": "offline_fallback",
        "note": "NHTSA no devolvió datos; se llenó parcialmente (marca/año). Modelo debe ser manual y validado.",
        "nhtsa_status": out.get("error"),
        "nhtsa_error_text": out.get("raw_error_text", ""),
        "nhtsa_error_code": out.get("raw_error_code", ""),
    }
