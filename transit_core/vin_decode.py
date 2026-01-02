# transit_core/vin_decode.py
from __future__ import annotations
from typing import Dict, Any
import requests

from .validators import normalize_vin, is_valid_vin

def decode_vin(vin: str) -> Dict[str, Any]:
    """
    Decodifica VIN usando NHTSA vPIC (API pública).
    Devuelve SIEMPRE:
      - datos cuando hay
      - o {"error": "..."} con el motivo exacto
    """
    v = normalize_vin(vin)

    if not v:
        return {"error": "VIN vacío"}
    if len(v) != 17:
        return {"error": f"VIN debe tener 17 caracteres. Actual: {len(v)}"}
    if not is_valid_vin(v):
        return {"error": "VIN inválido (debe ser A-Z/0-9 y NO incluir I/O/Q)"}

    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/decodevinvaluesextended/{v}?format=json"

    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        payload = r.json()
    except requests.exceptions.Timeout:
        return {"error": "Timeout consultando NHTSA (vPIC). Reintenta."}
    except requests.exceptions.RequestException as e:
        return {"error": f"Error de red consultando NHTSA: {e}"}
    except Exception as e:
        return {"error": f"Error inesperado parseando respuesta: {type(e).__name__}: {e}"}

    results = payload.get("Results") or []
    if not results:
        return {"error": "NHTSA devolvió Results vacío"}

    row = results[0] or {}

    make = (row.get("Make") or "").strip()
    model = (row.get("Model") or "").strip()
    year = (row.get("ModelYear") or "").strip()

    # vPIC manda a veces ErrorText / ErrorCode
    err_text = (row.get("ErrorText") or "").strip()
    err_code = (row.get("ErrorCode") or "").strip()

    if not (make or model or year):
        msg = "NHTSA no devolvió Marca/Modelo/Año."
        if err_text:
            msg += f" {err_text}"
        if err_code:
            msg += f" (code {err_code})"
        return {"error": msg}

    return {
        "brand": make,
        "model": model,
        "year": year,
        "trim": (row.get("Trim") or row.get("Series") or "").strip(),
        "engine": (row.get("EngineModel") or row.get("EngineConfiguration") or "").strip(),
        "vehicle_type": (row.get("VehicleType") or "").strip(),
        "body_class": (row.get("BodyClass") or "").strip(),
        "plant_country": (row.get("PlantCountry") or "").strip(),
    }
