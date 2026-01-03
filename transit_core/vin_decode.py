from __future__ import annotations

from typing import Dict, Any, Optional, Tuple
import time
import hashlib
import requests

from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:
    Retry = None  # type: ignore

from .validators import normalize_vin, is_valid_vin

VIN_DECODE_VERSION = "VIN_DECODE_GENERIC_v4_2026-01-03"

_WMI_BRAND = {
    "JHM": "HONDA", "1HG": "HONDA", "2HG": "HONDA", "JH4": "ACURA",
    "JTD": "TOYOTA", "JT2": "TOYOTA", "JT3": "TOYOTA", "4T1": "TOYOTA", "4T3": "TOYOTA",
    "JTH": "LEXUS", "JTJ": "LEXUS",
    "JN1": "NISSAN", "JN8": "NISSAN", "1N4": "NISSAN",
    "1FA": "FORD", "1FM": "FORD",
    "1G1": "CHEVROLET", "1GC": "CHEVROLET", "2G1": "CHEVROLET",
}

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


# -------------------------
# Config “production-friendly”
# -------------------------
VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/decodevinvaluesextended/{vin}?format=json"

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 45.0

RETRIES_TOTAL = 3
BACKOFF_FACTOR = 0.6

CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 días

# Circuit breaker: si hay muchos fallos seguidos, no pegues a NHTSA por un rato
CB_FAIL_THRESHOLD = 5
CB_OPEN_SECONDS = 120


# -------------------------
# Cache simple en memoria
# -------------------------
class _TTLCache:
    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._data: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        item = self._data.get(key)
        if not item:
            return None
        ts, val = item
        if (time.time() - ts) > self.ttl:
            self._data.pop(key, None)
            return None
        return val

    def set(self, key: str, val: Dict[str, Any]) -> None:
        self._data[key] = (time.time(), val)


_cache = _TTLCache(CACHE_TTL_SECONDS)


# -------------------------
# HTTP Session con retries
# -------------------------
_session = requests.Session()

if Retry is not None:
    retry = Retry(
        total=RETRIES_TOTAL,
        connect=RETRIES_TOTAL,
        read=RETRIES_TOTAL,
        status=RETRIES_TOTAL,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    _session.mount("https://", adapter)
    _session.mount("http://", adapter)


# -------------------------
# Circuit breaker state (simple)
# -------------------------
_fail_count = 0
_break_until_ts = 0.0


def _circuit_open() -> bool:
    return time.time() < _break_until_ts


def _trip_circuit() -> None:
    global _fail_count, _break_until_ts
    _fail_count += 1
    if _fail_count >= CB_FAIL_THRESHOLD:
        _break_until_ts = time.time() + CB_OPEN_SECONDS


def _reset_circuit() -> None:
    global _fail_count, _break_until_ts
    _fail_count = 0
    _break_until_ts = 0.0


# -------------------------
# Helpers
# -------------------------
def _brand_from_wmi(vin: str) -> str:
    return _WMI_BRAND.get(vin[:3], "")


def _year_candidates(vin: str) -> list[int]:
    code = vin[9]  # posición 10
    y1 = _YEAR_1980_2009.get(code)
    y2 = _YEAR_2010_2039.get(code)
    out: list[int] = []
    if y1 is not None:
        out.append(y1)
    if y2 is not None and y2 != y1:
        out.append(y2)
    return out


def _as_clean_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return str(v).strip()


def _first_nonempty(row: dict, keys: list[str]) -> str:
    for k in keys:
        v = _as_clean_str(row.get(k))
        if v:
            return v
    return ""


def _cache_key(vin: str) -> str:
    # SHA1 por simplicidad y porque es interno
    return hashlib.sha1(vin.encode("utf-8")).hexdigest()


# -------------------------
# NHTSA decode (robusto)
# -------------------------
def _decode_nhtsa(vin: str) -> Dict[str, Any]:
    """
    Devuelve dict con datos si hay make/model/year,
    o dict con {"error": ...} si no hay datos útiles.
    """
    url = VPIC_URL.format(vin=vin)

    # Circuit breaker: si está abierto, no golpeamos NHTSA
    if _circuit_open():
        return {
            "error": "NHTSA_CIRCUIT_OPEN",
            "raw_error_text": "Circuit open (fallos repetidos).",
            "raw_error_code": "",
        }

    try:
        r = _session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    except requests.exceptions.Timeout as e:
        _trip_circuit()
        return {"error": "NHTSA_TIMEOUT", "raw_error_text": f"{type(e).__name__}: {e}", "raw_error_code": ""}
    except requests.exceptions.RequestException as e:
        _trip_circuit()
        return {"error": "NHTSA_REQUEST_EXCEPTION", "raw_error_text": f"{type(e).__name__}: {e}", "raw_error_code": ""}

    if r.status_code != 200:
        _trip_circuit()
        return {"error": f"NHTSA_HTTP_{r.status_code}", "raw_error_text": (r.text or "")[:600], "raw_error_code": ""}

    try:
        payload = r.json()
    except Exception as e:
        _trip_circuit()
        return {"error": "NHTSA_BAD_JSON", "raw_error_text": f"{type(e).__name__}: {e}", "raw_error_code": ""}

    results = payload.get("Results") or []
    row = results[0] if results else {}

    make = _as_clean_str(row.get("Make"))
    model = _as_clean_str(row.get("Model"))
    year = _as_clean_str(row.get("ModelYear"))

    err_text = _as_clean_str(row.get("ErrorText"))
    err_code = _as_clean_str(row.get("ErrorCode"))

    # Si NO trae nada útil, tratamos como "sin data"
    if not (make or model or year):
        # Ojo: aquí NO necesariamente es “falla”; puede ser VIN raro o incompleto.
        # Igual consideramos esto como “sin data”.
        _trip_circuit()
        return {"error": "NHTSA_NO_DATA", "raw_error_text": err_text, "raw_error_code": err_code}

    # Si llegó aquí, NHTSA respondió con algo útil: cerramos circuito
    _reset_circuit()

    curb_weight = _first_nonempty(row, ["CurbWeight", "CurbWt", "Curb Weight"])
    gvwr = _first_nonempty(row, ["GVWR", "GVWRFrom", "GVWRTo"])

    return {
        "brand": make,
        "model": model,
        "year": year,
        "trim": _first_nonempty(row, ["Trim", "Series"]),
        "engine": _first_nonempty(row, ["EngineModel", "EngineConfiguration"]),
        "vehicle_type": _as_clean_str(row.get("VehicleType")),
        "body_class": _as_clean_str(row.get("BodyClass")),
        "plant_country": _as_clean_str(row.get("PlantCountry")),
        "curb_weight": curb_weight,
        "gvwr": gvwr,
        "source": "nhtsa",
    }


# -------------------------
# Public API
# -------------------------
def decode_vin(vin: str) -> Dict[str, Any]:
    """
    Pipeline:
    1) Cache -> si existe, retorna.
    2) NHTSA vPIC -> si trae make/model/year retorna.
    3) Fallback offline -> marca por WMI + año por pos 10 (modelo vacío)
    """
    v = normalize_vin(vin)

    if not v:
        return {"error": "VIN vacío", "version": VIN_DECODE_VERSION}
    if len(v) != 17:
        return {"error": f"VIN debe tener 17 caracteres. Actual: {len(v)}", "version": VIN_DECODE_VERSION}
    if not is_valid_vin(v):
        return {"error": "VIN inválido (A-Z/0-9, sin I/O/Q)", "version": VIN_DECODE_VERSION}

    # 1) cache
    ck = _cache_key(v)
    cached = _cache.get(ck)
    if cached:
        return cached

    nhtsa_status = ""
    nhtsa_text = ""
    nhtsa_code = ""

    # 2) NHTSA
    out = _decode_nhtsa(v)
    if not out.get("error"):
        out["version"] = VIN_DECODE_VERSION
        # cacheamos éxito
        _cache.set(ck, out)
        return out

    nhtsa_status = out.get("error", "")
    nhtsa_text = out.get("raw_error_text", "")
    nhtsa_code = out.get("raw_error_code", "")

    # 3) OFFLINE fallback
    brand = _brand_from_wmi(v)
    years = _year_candidates(v)

    if not brand and not years:
        final = {
            "error": "NHTSA sin datos y fallback offline sin inferencias. Ingresa manual.",
            "version": VIN_DECODE_VERSION,
            "nhtsa_status": nhtsa_status,
        }
        # no cacheamos errores definitivos
        return final

    final = {
        "brand": brand,
        "model": "",
        "year": str(years[0]) if years else "",
        "year_candidates": [str(y) for y in years],
        "trim": "",
        "engine": "",
        "vehicle_type": "",
        "body_class": "",
        "plant_country": "",
        "curb_weight": "",
        "gvwr": "",
        "source": "offline_fallback",
        "note": "NHTSA no devolvió datos completos. Marca/año inferidos si posible. Modelo manual.",
        "wmi": v[:3],
        "nhtsa_status": nhtsa_status,
        "nhtsa_error_text": nhtsa_text,
        "nhtsa_error_code": nhtsa_code,
        "version": VIN_DECODE_VERSION,
    }

    # cacheamos fallback para que no esté pegando a NHTSA repetidamente cuando está lento
    _cache.set(ck, final)
    return final
