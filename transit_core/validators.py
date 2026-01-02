# transit_core/validators.py
from __future__ import annotations

import re

# -----------------------------
# Normalización y validación básica (lo que tu app YA usa)
# -----------------------------
VIN_BASIC_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # sin I/O/Q

def normalize_vin(vin: str) -> str:
    """
    Normaliza VIN: uppercase, elimina espacios y caracteres no alfanuméricos.
    """
    if not vin:
        return ""
    v = vin.strip().upper()
    v = re.sub(r"[^A-Z0-9]", "", v)
    return v


def is_valid_vin(vin: str) -> bool:
    """
    Validación básica: 17 chars y excluye I/O/Q.
    (Esto es lo que tu gsheets_db.py y add_vehicle_item ya esperan)
    """
    v = normalize_vin(vin)
    return bool(VIN_BASIC_RE.match(v))


# -----------------------------
# Validación estricta ISO 3779 (check digit) - NUEVO
# -----------------------------
_VIN_TRANS = {
    **{str(i): i for i in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}
_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_check_digit(vin: str) -> str:
    """
    Calcula el check digit ISO 3779 (posición 9).
    Retorna '0'-'9' o 'X'. Si no es calculable retorna ''.
    """
    v = normalize_vin(vin)
    if len(v) != 17:
        return ""

    total = 0
    for i, ch in enumerate(v):
        val = _VIN_TRANS.get(ch)
        if val is None:
            return ""
        total += val * _VIN_WEIGHTS[i]

    rem = total % 11
    return "X" if rem == 10 else str(rem)


def is_valid_vin_strict(vin: str) -> bool:
    """
    Valida VIN con formato básico + check digit ISO 3779.
    """
    v = normalize_vin(vin)
    if not VIN_BASIC_RE.match(v):
        return False
    expected = vin_check_digit(v)
    if not expected:
        return False
    return v[8] == expected
