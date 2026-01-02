# transit_core/validators.py
from __future__ import annotations
import re

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # excluye I,O,Q

def normalize_vin(vin: str) -> str:
    """
    Normaliza un VIN:
    - uppercase
    - elimina espacios, guiones, saltos, etc.
    - deja solo A-Z y 0-9
    """
    v = (vin or "").strip().upper()
    v = re.sub(r"[^A-Z0-9]", "", v)
    return v

def is_valid_vin(vin: str) -> bool:
    v = normalize_vin(vin)
    return bool(VIN_RE.fullmatch(v))
