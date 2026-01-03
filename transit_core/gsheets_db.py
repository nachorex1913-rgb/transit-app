# transit_core/gsheets_db.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, Any, Dict

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import streamlit as st
import time
import random

from .ids import next_case_id, next_vehicle_id, next_article_id, next_doc_id
from .validators import is_valid_vin, normalize_vin

# ✅ Import del decoder para que el guardado no dependa del UI/handler
from .vin_decode import decode_vin


SHEETS = {
    "clients": ["client_id","name","address","id_type","id_number","phone","email","country_destination","created_at","updated_at"],
    "cases": ["case_id","client_id","case_date","status","origin","destination","notes","drive_folder_id","created_at","updated_at","final_pdf_drive_id","final_pdf_uploaded_at"],

    # ✅ VEHICLES (solo vehículos)
    "vehicles": [
        "vehicle_id","case_id","vin","brand","model","year",
        "trim","engine","vehicle_type","body_class","plant_country",
        "gvwr","curb_weight","weight","value","description","source","created_at"
    ],

    # ✅ ARTICLES (solo artículos)
    "articles": [
        "article_id","case_id","seq",
        "item_type","ref","brand","model",
        "weight","condition","quantity","value",
        "is_vehicle_part","parent_vin",
        "description","source","created_at"
    ],

    "documents": ["doc_id","case_id","doc_type","drive_file_id","file_name","uploaded_at"],
    "audit_log": ["log_id","timestamp","user","action","entity","entity_id","details"],
    "oauth_tokens": ["key","value"],
}

DEFAULT_STATUS = "Borrador"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean(x: Any) -> str:
    return ("" if x is None else str(x)).strip()


@st.cache_resource
def _gc() -> gspread.Client:
    sa = st.secrets["gcp_service_account"]
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(sa, scopes=scopes)
    return gspread.authorize(creds)


@st.cache_resource
def _ss() -> gspread.Spreadsheet:
    sid = st.secrets.get("SPREADSHEET_ID")
    if not sid:
        raise RuntimeError("Falta SPREADSHEET_ID en secrets.")

    last_err = None
    for attempt in range(8):
        try:
            return _gc().open_by_key(sid)
        except gspread.exceptions.APIError as e:
            last_err = e
            time.sleep(min((2 ** attempt) + random.uniform(0, 0.5), 12))
        except Exception as e:
            raise RuntimeError(f"Error abriendo Google Sheet: {type(e).__name__}: {e}") from e

    raise RuntimeError(f"Google Sheets APIError persistente abriendo spreadsheet: {last_err}") from last_err


def _worksheets_map() -> dict[str, gspread.Worksheet]:
    ss = _ss()
    last_err = None
    for attempt in range(6):
        try:
            wss = ss.worksheets()
            return {ws.title: ws for ws in wss}
        except gspread.exceptions.APIError as e:
            last_err = e
            time.sleep(min((2 ** attempt) + random.uniform(0, 0.5), 10))
    raise RuntimeError(f"Error obteniendo worksheets metadata: {last_err}") from last_err


def _safe_get_row1(ws: gspread.Worksheet) -> list[str]:
    last_err = None
    for attempt in range(6):
        try:
            vals = ws.get("1:1")
            return vals[0] if vals and len(vals) > 0 else []
        except gspread.exceptions.APIError as e:
            last_err = e
            time.sleep(min((2 ** attempt) + random.uniform(0, 0.5), 10))
    raise RuntimeError(f"Error leyendo headers (1:1) en '{ws.title}': {last_err}") from last_err


def init_db(force: bool = False) -> None:
    if not force and st.session_state.get("_transit_db_inited", False):
        return

    ss = _ss()
    wmap = _worksheets_map()
    existing_titles = set(wmap.keys())

    for tab, headers in SHEETS.items():
        if tab not in existing_titles:
            last_err = None
            for attempt in range(6):
                try:
                    ws = ss.add_worksheet(title=tab, rows=2000, cols=max(10, len(headers) + 2))
                    ws.append_row(headers)
                    break
                except gspread.exceptions.APIError as e:
                    last_err = e
                    time.sleep(min((2 ** attempt) + random.uniform(0, 0.5), 10))
            else:
                raise RuntimeError(f"No se pudo crear worksheet '{tab}': {last_err}") from last_err
        else:
            ws = wmap[tab]
            first_row = _safe_get_row1(ws)
            if not first_row:
                ws.append_row(headers)
            else:
                missing = [h for h in headers if h not in first_row]
                if missing:
                    ws.update("1:1", [first_row + missing])

    st.session_state["_transit_db_inited"] = True


def _ws(tab: str) -> gspread.Worksheet:
    init_db()
    wmap = _worksheets_map()
    if tab in wmap:
        return wmap[tab]
    init_db(force=True)
    wmap = _worksheets_map()
    if tab not in wmap:
        raise RuntimeError(f"No existe la pestaña '{tab}' en el spreadsheet.")
    return wmap[tab]


def _get_all_records(tab: str) -> list[dict[str, Any]]:
    ws = _ws(tab)
    last_err = None
    for attempt in range(6):
        try:
            return ws.get_all_records()
        except gspread.exceptions.APIError as e:
            last_err = e
            time.sleep(min((2 ** attempt) + random.uniform(0, 0.5), 10))
    header = _safe_get_row1(ws)
    raise RuntimeError(f"Error leyendo tab '{tab}'. Headers: {header}. Detalle: {last_err}") from last_err


def _append(tab: str, row: list[Any]) -> None:
    ws = _ws(tab)
    last_err = None
    for attempt in range(6):
        try:
            ws.append_row(row, value_input_option="USER_ENTERED")
            return
        except gspread.exceptions.APIError as e:
            last_err = e
            time.sleep(min((2 ** attempt) + random.uniform(0, 0.5), 10))
    raise RuntimeError(f"Error append_row en '{tab}': {last_err}") from last_err


def _col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _update_row_by_headers(ws: gspread.Worksheet, row_index: int, headers: list[str], record: dict[str, Any]):
    end_col = _col_letter(len(headers))
    ws.update(f"A{row_index}:{end_col}{row_index}", [[record.get(h, "") for h in headers]])


# -----------------------------
# CLIENTS
# -----------------------------
def list_clients() -> pd.DataFrame:
    init_db()
    return pd.DataFrame(_get_all_records("clients"))


def get_client(client_id: str) -> dict[str, Any] | None:
    init_db()
    for r in _get_all_records("clients"):
        if str(r.get("client_id","")) == str(client_id):
            return r
    return None


def search_clients(query: str) -> pd.DataFrame:
    df = list_clients()
    if df.empty:
        return df
    q = (query or "").strip().lower()
    if not q:
        return df
    mask = df.apply(lambda r: q in str(r.to_dict()).lower(), axis=1)
    return df[mask]


def upsert_client(
    name: str,
    address: str = "",
    id_type: str = "",
    id_number: str = "",
    phone: str = "",
    email: str = "",
    country_destination: str = "",
    client_id: Optional[str] = None,
) -> str:
    init_db()
    ws = _ws("clients")
    records = ws.get_all_records()
    now = _now_iso()
    headers = SHEETS["clients"]

    if client_id:
        for i, r in enumerate(records, start=2):
            if str(r.get("client_id","")) == client_id:
                updated = {
                    "client_id": client_id,
                    "name": name,
                    "address": address,
                    "id_type": id_type,
                    "id_number": id_number,
                    "phone": phone,
                    "email": email,
                    "country_destination": country_destination,
                    "created_at": r.get("created_at") or now,
                    "updated_at": now,
                }
                _update_row_by_headers(ws, i, headers, updated)
                return client_id

    max_n = 0
    for r in records:
        cid = str(r.get("client_id","")).strip()
        if cid.startswith("CL-") and cid[3:].isdigit():
            max_n = max(max_n, int(cid[3:]))

    new_id = f"CL-{max_n+1:06d}"
    row = [new_id, name, address, id_type, id_number, phone, email, country_destination, now, now]
    _append("clients", row)
    return new_id


# -----------------------------
# CASES
# -----------------------------
def list_cases() -> pd.DataFrame:
    init_db()
    return pd.DataFrame(_get_all_records("cases"))


def create_case(
    client_id: str,
    origin: str = "USA",
    destination: str = "",
    notes: str = "",
    case_date: Optional[str] = None,
    status: str = DEFAULT_STATUS,
    drive_folder_id: str = "",
) -> str:
    init_db()
    ws = _ws("cases")
    records = ws.get_all_records()
    existing_ids = [r.get("case_id","") for r in records]
    year = datetime.now().year
    case_id = next_case_id(existing_ids, year=year)

    now = _now_iso()
    cdate = case_date or datetime.now().date().isoformat()

    row = [case_id, client_id, cdate, status, origin, destination, notes, drive_folder_id or "", now, now, "", ""]
    _append("cases", row)
    return case_id


def get_case(case_id: str) -> dict[str, Any] | None:
    init_db()
    for r in _get_all_records("cases"):
        if str(r.get("case_id","")) == str(case_id):
            return r
    return None


def update_case_fields(case_id: str, fields: dict) -> None:
    init_db()
    ws = _ws("cases")
    headers = _safe_get_row1(ws)
    if "case_id" not in headers:
        raise RuntimeError("La hoja 'cases' no tiene columna case_id.")

    col_case_id = headers.index("case_id") + 1
    cell = ws.find(case_id, in_column=col_case_id)
    if not cell:
        raise ValueError(f"case_id no encontrado: {case_id}")
    row = cell.row

    updates = []
    for k, v in fields.items():
        if k in headers:
            col = headers.index(k) + 1
            updates.append((row, col, "" if v is None else str(v)))

    if not updates:
        return

    payload = [{"range": f"{_col_letter(c)}{r}", "values": [[val]]} for (r, c, val) in updates]
    ws.batch_update(payload)


# -----------------------------
# VEHICLES
# -----------------------------
def list_vehicles(case_id: Optional[str] = None) -> pd.DataFrame:
    init_db()
    df = pd.DataFrame(_get_all_records("vehicles"))
    if df.empty:
        return df
    if case_id:
        return df[df["case_id"] == case_id]
    return df


def _vin_exists_global(vin: str) -> bool:
    v = normalize_vin(vin)
    for r in _get_all_records("vehicles"):
        if normalize_vin(str(r.get("vin",""))) == v:
            return True
    return False


def add_vehicle(
    case_id: str,
    vin: str,
    brand: str = "",
    model: str = "",
    year: str = "",
    trim: str = "",
    engine: str = "",
    vehicle_type: str = "",
    body_class: str = "",
    plant_country: str = "",
    gvwr: str = "",
    curb_weight: str = "",
    weight: str = "",
    value: str = "0",
    description: str = "",
    source: str = "vin_text",
) -> str:
    """
    ✅ Cambio clave:
    - Si vienen vacíos brand/model/year/etc, decodifica VIN aquí mismo y llena campos antes de guardar.
    - Así no dependemos de que el UI/handler pase los campos.
    """
    init_db()
    v = normalize_vin(vin)
    if not is_valid_vin(v) or len(v) != 17:
        raise ValueError("VIN inválido. Debe tener 17 caracteres y no incluir I/O/Q.")
    if _vin_exists_global(v):
        raise ValueError("Este VIN ya existe en el sistema (no se puede duplicar).")

    # 1) si faltan campos, intenta decode
    needs_decode = not any([
        _clean(brand), _clean(model), _clean(year),
        _clean(trim), _clean(engine), _clean(vehicle_type),
        _clean(body_class), _clean(plant_country),
        _clean(gvwr), _clean(curb_weight),
    ])

    decoded: Dict[str, Any] = {}
    if needs_decode:
        decoded = decode_vin(v) or {}

        # Solo llenamos si decoded trae algo útil
        if decoded.get("source") == "nhtsa" or decoded.get("brand") or decoded.get("model") or decoded.get("year"):
            brand = _clean(brand) or _clean(decoded.get("brand"))
            model = _clean(model) or _clean(decoded.get("model"))
            year = _clean(year) or _clean(decoded.get("year"))
            trim = _clean(trim) or _clean(decoded.get("trim"))
            engine = _clean(engine) or _clean(decoded.get("engine"))
            vehicle_type = _clean(vehicle_type) or _clean(decoded.get("vehicle_type"))
            body_class = _clean(body_class) or _clean(decoded.get("body_class"))
            plant_country = _clean(plant_country) or _clean(decoded.get("plant_country"))
            gvwr = _clean(gvwr) or _clean(decoded.get("gvwr"))
            curb_weight = _clean(curb_weight) or _clean(decoded.get("curb_weight"))

            # Fuente: respeta si caller manda una fuente específica, si no usa la del decoder
            if source in ("vin_text", "", None):
                source = _clean(decoded.get("source")) or "vin_text"

    ws = _ws("vehicles")
    records = ws.get_all_records()
    existing_ids = [r.get("vehicle_id","") for r in records]
    vehicle_id = next_vehicle_id(existing_ids)
    now = _now_iso()

    row = [
        vehicle_id, case_id, v,
        _clean(brand), _clean(model), _clean(year),
        _clean(trim), _clean(engine), _clean(vehicle_type), _clean(body_class), _clean(plant_country),
        _clean(gvwr), _clean(curb_weight),
        _clean(weight), _clean(value), _clean(description),
        _clean(source), now
    ]
    _append("vehicles", row)
    return vehicle_id


# -----------------------------
# ARTICLES
# -----------------------------
def list_articles(case_id: Optional[str] = None) -> pd.DataFrame:
    init_db()
    df = pd.DataFrame(_get_all_records("articles"))
    if df.empty:
        return df
    if case_id:
        return df[df["case_id"] == case_id]
    return df


def _next_seq_for_case(case_id: str) -> str:
    ws = _ws("articles")
    records = ws.get_all_records()
    seqs = [r.get("seq","") for r in records if str(r.get("case_id","")) == case_id]
    mx = 0
    for s in seqs:
        s = str(s).strip()
        if s.startswith(f"A-{case_id}-"):
            try:
                mx = max(mx, int(s.split("-")[-1]))
            except Exception:
                pass
    return f"A-{case_id}-{mx+1:04d}"


def add_article(
    case_id: str,
    item_type: str,
    ref: str = "",
    brand: str = "",
    model: str = "",
    weight: str = "",
    condition: str = "",
    quantity: int = 1,
    value: str = "",
    is_vehicle_part: bool = False,
    parent_vin: str = "",
    description: str = "",
    source: str = "voice",
) -> str:
    init_db()
    ws = _ws("articles")
    records = ws.get_all_records()
    existing_ids = [r.get("article_id","") for r in records]
    article_id = next_article_id(existing_ids)
    seq = _next_seq_for_case(case_id)
    now = _now_iso()

    pv = normalize_vin(parent_vin) if is_vehicle_part else ""
    row = [
        article_id, case_id, seq,
        (item_type or "").strip(), (ref or "").strip(), (brand or "").strip(), (model or "").strip(),
        (weight or "").strip(), (condition or "").strip(), int(quantity or 1), (value or "").strip(),
        "SI" if is_vehicle_part else "NO", pv,
        (description or "").strip(), source, now
    ]
    _append("articles", row)
    return article_id


# -----------------------------
# DOCUMENTS (a nivel trámite)
# -----------------------------
def list_documents(case_id: str) -> pd.DataFrame:
    init_db()
    df = pd.DataFrame(_get_all_records("documents"))
    if df.empty:
        return df
    return df[df["case_id"] == case_id]


def add_document(
    case_id: str,
    drive_file_id: str,
    file_name: str,
    doc_type: str,
) -> str:
    init_db()
    ws = _ws("documents")
    existing_doc_ids = [r.get("doc_id","") for r in ws.get_all_records()]
    doc_id = next_doc_id(existing_doc_ids)
    now = _now_iso()
    row = [doc_id, case_id, doc_type, drive_file_id, file_name, now]
    _append("documents", row)
    return doc_id
