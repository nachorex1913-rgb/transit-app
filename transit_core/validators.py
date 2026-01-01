# transit_core/validators.py (agrega esto)

import re

VIN_BASIC_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # sin I/O/Q

# Tabla ISO 3779
_VIN_TRANS = {
    **{str(i): i for i in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}

_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_check_digit(vin: str) -> str:
    """Calcula el check digit ISO 3779 (posición 9). Retorna '0'-'9' o 'X'."""
    vin = (vin or "").strip().upper()
    if len(vin) != 17:
        return ""

    total = 0
    for i, ch in enumerate(vin):
        if ch not in _VIN_TRANS:
            return ""
        total += _VIN_TRANS[ch] * _VIN_WEIGHTS[i]

    rem = total % 11
    return "X" if rem == 10 else str(rem)


def is_valid_vin_strict(vin: str) -> bool:
    """Valida formato + check digit."""
    vin = (vin or "").strip().upper()
    if not VIN_BASIC_RE.match(vin):
        return False
    expected = vin_check_digit(vin)
    if not expected:
        return False
    return vin[8] == expected
