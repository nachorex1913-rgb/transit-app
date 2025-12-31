# transit_core/auth.py
from __future__ import annotations

import json
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials as SACredentials
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

# Scope mínimo recomendado (evita verificación pesada)
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def _gc_sa() -> gspread.Client:
    sa = st.secrets["gcp_service_account"]
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = SACredentials.from_service_account_info(sa, scopes=scopes)
    return gspread.authorize(creds)

def _tokens_ws():
    ss = _gc_sa().open_by_key(st.secrets["SPREADSHEET_ID"])
    return ss.worksheet("oauth_tokens")

def _get_token_json(key: str) -> dict | None:
    ws = _tokens_ws()
    rows = ws.get_all_records()
    for r in rows:
        if str(r.get("key", "")).strip() == key:
            val = str(r.get("value", "")).strip()
            if val:
                return json.loads(val)
    return None

def _set_token_json(key: str, token: dict) -> None:
    ws = _tokens_ws()
    rows = ws.get_all_records()
    token_str = json.dumps(token)

    for i, r in enumerate(rows, start=2):
        if str(r.get("key", "")).strip() == key:
            ws.update(f"B{i}", [[token_str]])
            return

    ws.append_row([key, token_str])

def _get_query_params() -> dict:
    # Compatibilidad Streamlit
    try:
        return dict(st.query_params)
    except Exception:
        return st.experimental_get_query_params()

def _clear_query_params():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

def drive_oauth_ready_ui() -> bool:
    # 1) Si ya hay token guardado → listo
    token = _get_token_json("drive_token")
    if token:
        return True

    st.warning("Drive no está conectado. Conéctalo para subir documentos.")

    client_id = st.secrets["google_oauth"]["client_id"]
    client_secret = st.secrets["google_oauth"]["client_secret"]
    redirect_uri = st.secrets["google_oauth"]["redirect_uri"]

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=DRIVE_SCOPES,
        redirect_uri=redirect_uri,
    )

    # 2) Si venimos de Google con ?code=... → canjear y guardar
    qp = _get_query_params()
    code = qp.get("code")

    # A veces viene como lista
    if isinstance(code, list):
        code = code[0] if code else None

    if code:
        try:
            flow.fetch_token(code=code)
            creds = flow.credentials
            token_payload = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
            }
            _set_token_json("drive_token", token_payload)
            _clear_query_params()
            st.success("✅ Drive conectado.")
            st.rerun()
        except Exception as e:
            st.error(f"No pude completar OAuth: {type(e).__name__}: {e}")
            return False

    # 3) Si no hay code → mostrar link de autorización
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    st.link_button("Conectar Google Driv_
