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


# -----------------------------
# SCHEMA DEFINITIONS
# -----------------------------
SHEETS = {
    "clients": [
        "client_id","name","address","id_type","id_number","phone",
        "email","country_destination","created_at","updated_at"
    ],
    "cases": [
        "case_id","client_id",
        "case_name",            # >>> NEW: nombre visible (nombre del cliente)
        "case_date",
        "status",
        "origin","destination","notes",
        "drive_folder_id",
        "created_at","updated_at",
        "final_pdf_drive_id","final_pdf_uploaded_at"
    ],
    "items": [
        "item_id","case_id","item_type","unique_key",
        "brand","model","year",
        "description","quantity","weight","value",
        "parent_vin",           # >>> NEW: relación opcional con vehículo
        "source","created_at"
    ],
    "documents": [
        "doc_id","case_id","item_id","doc_type",
        "drive_file_id","file_name","uploaded_at"
    ],
    "audit_log": ["log_id","timestamp","user","action","entity","entity_id","details"],
    "oauth_tokens": ["key","value"],
}

DEFAULT_STATUS = "BORRADOR"  # >>> NEW: normalizado


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# -----------------------------
# GOOGLE CLIENTS
# -----------------------------
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
    raise RuntimeError(f"Google Sheets APIError persistente: {last_err}") from last_err


# -----------------------------
# INIT / HELPERS
# -----------------------------
def init_db(force: bool = False) -> None:
    if not force and st.session_state.get("_transit_db_inited", False):
        return

    ss = _ss()
    wmap = {ws.title: ws for ws in ss.worksheets()}

    for tab, headers in SHEETS.items():
        if tab not in wmap:
            ws = ss.add_worksheet(title=tab, rows=2000, cols=max(20, len(headers)+2))
            ws.append_row(headers)
        else:
            ws = wmap[tab]
            first_row = ws.get("1:1")[0]
            missing = [h for h in headers if h not in first_row]
            if missing:
                ws.update("1:1", [first_row + missing])

    st.session_state["_transit_db_inited"] = True


def _ws(tab: str) -> gspread.Worksheet:
    init_db()
    return _ss().worksheet(tab)


def _get_all_records(tab: str) -> list[dict[str, Any]]:
    return _ws(tab).get_all_records()


def _append(tab: str, row: list[Any]) -> None:
    _ws(tab).append_row(row, value_input_option="USER_ENTERED")


# -----------------------------
# CLIENTS
# -----------------------------
def list_clients() -> pd.DataFrame:
    init_db()
    return pd.DataFrame(_get_all_records("clients"))


def get_client(client_id: str) -> dict[str, Any] | None:
    for r in _get_all_records("clients"):
        if r.get("client_id") == client_id:
            return r
    return None


# -----------------------------
# CASES
# -----------------------------
def list_cases() -> pd.DataFrame:
    init_db()
    return pd.DataFrame(_get_all_records("cases"))


def create_case(
    client_id: str,
    case_name: str,           # >>> NEW (obligatorio)
    origin: str = "USA",
    destination: str = "",
    notes: str = "",
    case_date: Optional[str] = None,
    status: str = DEFAULT_STATUS,
    drive_folder_id: str = "",
) -> str:
    init_db()
    records = _get_all_records("cases")
    existing_ids = [r.get("case_id","") for r in records]
    year = datetime.now().year
    case_id = next_case_id(existing_ids, year=year)

    now = _now_iso()
    cdate = case_date or datetime.now().date().isoformat()

    row = [
        case_id,
        client_id,
        case_name,
        cdate,
        status,
        origin,
        destination,
        notes,
        drive_folder_id,
        now,
        now,
        "",
        "",
    ]
    _append("cases", row)
    return case_id


def get_case(case_id: str) -> dict[str, Any] | None:
    for r in _get_all_records("cases"):
        if r.get("case_id") == case_id:
            return r
    return None


def update_case_fields(case_id: str, fields: dict) -> None:
    ws = _ws("cases")
    headers = ws.get("1:1")[0]
    col_case = headers.index("case_id") + 1
    cell = ws.find(case_id, in_column=col_case)
    if not cell:
        raise ValueError("case_id no encontrado")

    for k, v in fields.items():
        if k in headers:
            col = headers.index(k) + 1
            ws.update_cell(cell.row, col, v)

    ws.update_cell(cell.row, headers.index("updated_at")+1, _now_iso())


# -----------------------------
# ITEMS
# -----------------------------
def list_items(case_id: Optional[str] = None) -> pd.DataFrame:
    df = pd.DataFrame(_get_all_records("items"))
    if case_id:
        return df[df["case_id"] == case_id]
    return df


def _vin_exists_global(vin: str) -> bool:
    v = normalize_vin(vin)
    for r in _get_all_records("items"):
        if r.get("item_type") == "vehicle" and normalize_vin(r.get("unique_key","")) == v:
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
    v = normalize_vin(vin)
    if not is_valid_vin(v):
        raise ValueError("VIN inválido.")
    if _vin_exists_global(v):
        raise ValueError("Este VIN ya existe en el sistema.")

    records = _get_all_records("items")
    existing_ids = [r.get("item_id","") for r in records]
    item_id = next_item_id(existing_ids)
    now = _now_iso()

    row = [
        item_id, case_id, "vehicle", v,
        brand, model, year,
        description, quantity, weight, value,
        "",
        source, now
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
    parent_vin: str = "",     # >>> NEW
    source: str = "manual",
) -> str:
    records = _get_all_records("items")

    def n(x): return (str(x or "").strip().lower())

    for r in records:
        if (
            r.get("case_id") == case_id
            and r.get("item_type") == "article"
            and n(r.get("description")) == n(description)
            and n(r.get("brand")) == n(brand)
            and n(r.get("model")) == n(model)
            and n(r.get("weight")) == n(weight)
            and n(r.get("value")) == n(value)
            and n(r.get("parent_vin")) == n(parent_vin)
        ):
            raise ValueError("Este artículo ya existe en el trámite.")

    existing_ids = [r.get("item_id","") for r in records]
    existing_keys = [r.get("unique_key","") for r in records if r.get("case_id")==case_id]
    item_id = next_item_id(existing_ids)
    seq = next_article_seq(existing_keys, case_id=case_id)
    now = _now_iso()

    row = [
        item_id, case_id, "article", seq,
        brand, model, "",
        description, quantity, weight, value,
        parent_vin,
        source, now
    ]
    _append("items", row)
    return item_id


# -----------------------------
# DOCUMENTS
# -----------------------------
def list_documents(case_id: str) -> pd.DataFrame:
    df = pd.DataFrame(_get_all_records("documents"))
    return df[df["case_id"] == case_id]


def add_document(
    case_id: str,
    drive_file_id: str,
    file_name: str,
    doc_type: str,
    item_id: str = "",
) -> str:
    records = _get_all_records("documents")
    existing_ids = [r.get("doc_id","") for r in records]
    doc_id = next_doc_id(existing_ids)
    now = _now_iso()
    row = [doc_id, case_id, item_id, doc_type, drive_file_id, file_name, now]
    _append("documents", row)
    return doc_id
