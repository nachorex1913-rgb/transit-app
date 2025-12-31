# transit_core/gsheets_db.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, Any

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import streamlit as st
import time
import random

from .ids import next_case_id, next_article_seq, next_item_id, next_doc_id
from .validators import is_valid_vin, normalize_vin

SHEETS = {
    "clients": ["client_id","name","address","id_type","id_number","phone","email","country_destination","created_at","updated_at"],
    # 👇 agrega columnas nuevas (si no existen aún, init_db las crea al final)
    "cases": ["case_id","client_id","case_date","status","origin","destination","notes","drive_folder_id","created_at","updated_at","final_pdf_drive_id","final_pdf_uploaded_at"],
    "items": ["item_id","case_id","item_type","unique_key","brand","model","year","description","quantity","weight","value","source","created_at"],
    "documents": ["doc_id","case_id","item_id","doc_type","drive_file_id","file_name","uploaded_at"],
    "audit_log": ["log_id","timestamp","user","action","entity","entity_id","details"],
    "oauth_tokens": ["key","value"],  # por si la usas
}

DEFAULT_STATUS = "Borrador"

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

@st.cache_resource
def _gc() -> gspread.Client:
    sa = st.secrets["gcp_service_account"]
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(sa, scopes=scopes)
    return gspread.authorize(creds)

def _ss():
    sid = st.secrets.get("SPREADSHEET_ID")
    if not sid:
        raise RuntimeError("Falta SPREADSHEET_ID en secrets.")

    last_err = None
    for attempt in range(6):
        try:
            return _gc().open_by_key(sid)
        except gspread.exceptions.APIError as e:
            last_err = e
            sleep_s = (2 ** attempt) + random.uniform(0, 0.5)
            time.sleep(min(sleep_s, 10))
        except Exception as e:
            raise RuntimeError(f"Error abriendo Google Sheet: {type(e).__name__}: {e}") from e

    raise RuntimeError(f"Google Sheets APIError persistente: {last_err}") from last_err

def _ws(tab: str):
    return _ss().worksheet(tab)

def init_db() -> None:
    ss = _ss()
    existing = {ws.title for ws in ss.worksheets()}
    for tab, headers in SHEETS.items():
        if tab not in existing:
            ws = ss.add_worksheet(title=tab, rows=2000, cols=max(10, len(headers) + 2))
            ws.append_row(headers)
        else:
            ws = ss.worksheet(tab)
            first_row = ws.row_values(1)
            if len(first_row) == 0:
                ws.append_row(headers)
            else:
                missing = [h for h in headers if h not in first_row]
                if missing:
                    ws.update("1:1", [first_row + missing])

def _get_all_records(tab: str) -> list[dict[str, Any]]:
    ws = _ws(tab)
    try:
        return ws.get_all_records()
    except Exception as e:
        header = ws.row_values(1)
        raise RuntimeError(
            f"Error leyendo tab '{tab}'. Revisa headers en fila 1.\n"
            f"Headers actuales: {header}\n"
            f"Detalle: {type(e).__name__}: {e}"
        )

def _append(tab: str, row: list[Any]) -> None:
    _ws(tab).append_row(row, value_input_option="USER_ENTERED")

def _col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def _update_row_by_headers(ws, row_index: int, headers: list[str], record: dict[str, Any]):
    # actualiza desde col A hasta la última columna de headers
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
) -> str:
    init_db()
    ws = _ws("cases")
    records = ws.get_all_records()
    existing_ids = [r.get("case_id","") for r in records]
    year = datetime.now().year
    case_id = next_case_id(existing_ids, year=year)
    now = _now_iso()
    cdate = case_date or datetime.now().date().isoformat()

    # respeta headers actuales
    row = [
        case_id, client_id, cdate, status, origin, destination, notes, "", now, now,
        "", ""  # final_pdf_drive_id, final_pdf_uploaded_at
    ]
    _append("cases", row)
    return case_id

def get_case(case_id: str) -> dict[str, Any] | None:
    init_db()
    for r in _get_all_records("cases"):
        if str(r.get("case_id","")) == case_id:
            return r
    return None

def set_case_drive_folder(case_id: str, drive_folder_id: str) -> None:
    init_db()
    ws = _ws("cases")
    records = ws.get_all_records()
    now = _now_iso()
    headers = ws.row_values(1)

    for i, r in enumerate(records, start=2):
        if str(r.get("case_id","")) == case_id:
            r["drive_folder_id"] = drive_folder_id
            r["updated_at"] = now
            _update_row_by_headers(ws, i, headers, r)
            return

def update_case_fields(case_id: str, fields: dict) -> None:
    """
    Actualiza campos (solo columnas existentes) para un case_id en 'cases'.
    """
    init_db()
    ws = _ws("cases")
    headers = ws.row_values(1)

    if "case_id" not in headers:
        raise RuntimeError("La hoja 'cases' no tiene columna case_id en headers.")

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
# ITEMS
# -----------------------------
def list_items(case_id: Optional[str] = None) -> pd.DataFrame:
    init_db()
    df = pd.DataFrame(_get_all_records("items"))
    if df.empty:
        return df
    if case_id:
        return df[df["case_id"] == case_id]
    return df

def _vin_exists_global(vin: str) -> bool:
    v = normalize_vin(vin)
    for r in _get_all_records("items"):
        if str(r.get("item_type","")) == "vehicle" and normalize_vin(str(r.get("unique_key",""))) == v:
            return True
    return False

def add_vehicle_item(
    case_id: str,
    vin: str,
    brand: str = "",
    model: str = "",
    year: str = "",
    description: str = "",
    quantity: int = 1,
    weight: str = "",
    value: str = "",
    source: str = "manual",
) -> str:
    init_db()
    v = normalize_vin(vin)
    if not is_valid_vin(v):
        raise ValueError("VIN inválido. Debe tener 17 caracteres y no incluir I/O/Q.")
    if _vin_exists_global(v):
        raise ValueError("Este VIN ya existe en el sistema (no se puede duplicar).")

    items_ws = _ws("items")
    existing_item_ids = [r.get("item_id","") for r in items_ws.get_all_records()]
    item_id = next_item_id(existing_item_ids)
    now = _now_iso()

    row = [
        item_id, case_id, "vehicle", v, brand, model, year,
        description, int(quantity or 1), weight, value, source, now
    ]
    _append("items", row)
    return item_id

def add_article_item(
    case_id: str,
    description: str,
    brand: str = "",
    model: str = "",
    quantity: int = 1,
    weight: str = "",
    value: str = "",
    source: str = "manual",
) -> str:
    init_db()
    items_ws = _ws("items")
    records = items_ws.get_all_records()
    existing_item_ids = [r.get("item_id","") for r in records]
    existing_keys = [r.get("unique_key","") for r in records if str(r.get("case_id","")) == case_id]

    item_id = next_item_id(existing_item_ids)
    seq = next_article_seq(existing_keys, case_id=case_id)
    now = _now_iso()

    row = [
        item_id, case_id, "article", seq, brand, model, "",
        description, int(quantity or 1), weight, value, source, now
    ]
    _append("items", row)
    return item_id

# -----------------------------
# DOCUMENTS
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
    item_id: str = "",
) -> str:
    init_db()
    docs_ws = _ws("documents")
    existing_doc_ids = [r.get("doc_id","") for r in docs_ws.get_all_records()]
    doc_id = next_doc_id(existing_doc_ids)
    now = _now_iso()
    row = [doc_id, case_id, item_id, doc_type, drive_file_id, file_name, now]
    _append("documents", row)
    return doc_id
