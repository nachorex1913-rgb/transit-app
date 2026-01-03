# transit_core/vehicle_mapper.py
from __future__ import annotations
from typing import Dict, Any

# Mapea salida del decoder -> campos que TU app guarda
DECODER_TO_VEHICLE_FIELDS = {
    "brand": "brand",           # o "make" / "marca"
    "model": "model",           # o "modelo"
    "year": "year",             # o "anio"
    "trim": "trim",
    "engine": "engine",
    "vehicle_type": "vehicle_type",
    "body_class": "body_class",
    "plant_country": "plant_country",
    "curb_weight": "curb_weight",
    "gvwr": "gvwr",
    "wmi": "wmi",
    "source": "vin_source",
    "version": "vin_decode_version",
    "nhtsa_status": "nhtsa_status",
    "nhtsa_error_text": "nhtsa_error_text",
    "nhtsa_error_code": "nhtsa_error_code",
    "note": "vin_note",
}

def apply_vin_decode(vehicle: Dict[str, Any], decoded: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aplica los datos decodificados sobre el dict 'vehicle' usando un mapeo centralizado.
    Regla: solo sobre-escribe si decoded trae valor NO vacío.
    """
    out = dict(vehicle)  # copia

    for src_key, dst_key in DECODER_TO_VEHICLE_FIELDS.items():
        val = decoded.get(src_key, "")
        if val is None:
            continue

        # solo pisa si viene algo útil
        if isinstance(val, str):
            if val.strip() == "":
                continue
            out[dst_key] = val.strip()
        else:
            out[dst_key] = val

    # Asegura VIN en el record final
    if decoded.get("vin"):
        out["vin"] = decoded["vin"]
    elif out.get("vin"):
        pass

    return out
