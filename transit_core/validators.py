# transit_core/validators.py
import re

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # sin I,O,Q

def is_valid_vin(vin: str) -> bool:
    if not vin:
        return False
    v = vin.strip().upper()
    return bool(VIN_RE.match(v))

def normalize_vin(vin: str) -> str:
    return (vin or "").strip().upper()

