# transit_core/vin_decode.py
from __future__ import annotations

import requests

NHTSA_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin}?format=json"


def decode_vin(vin: str) -> dict:
    """
    Decodifica VIN usando API pública de NHTSA (VPIC).
    Devuelve brand/make, model, year + extras si existen.
    """
    vin = (vin or "").strip().upper()
    if not vin:
        return {}

    url = NHTSA_URL.format(vin=vin)
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()

    results = (data or {}).get("Results") or []
    if not results:
        return {}

    row = results[0] or {}

    # Campos comunes
    brand = row.get("Make") or ""
    model = row.get("Model") or ""
    year = row.get("ModelYear") or ""
    trim = row.get("Trim") or row.get("Series") or ""
    engine = row.get("EngineModel") or row.get("EngineCylinders") or ""

    out = {
        "brand": brand,
        "model": model,
        "year": year,
        "trim": trim,
        "engine": engine,
    }

    # Limpieza básica
    return {k: str(v).strip() for k, v in out.items() if str(v).strip()}
