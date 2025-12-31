# transit_core/auth.py
from __future__ import annotations

import json
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials as SACredentials
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

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
    # busca fila existente
    for i, r in enumerate(rows, start=2):
        if str(r.get("key", "")).strip() == key:
            ws.update(f"B{i}", [[token_str]])
            return
    # si no existe, crea fila
    ws.append_row([key, token_str])

def drive_oauth_ready_ui() -> bool:
    """
    UI para autorizar Drive por OAuth (una sola vez).
    Devuelve True si ya hay token guardado y listo.
    """
    token = _get_token_json("drive_token")
    if token:
        return True

    st.warning("Drive OAuth no está conectado. Conecta tu Google Drive para poder subir documentos.")

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

    # si ya viene ?code=... en la URL, canjea por token y guarda
    qp = st.query_params
    if "code" in qp:
        code = qp["code"]
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
            st.success("✅ Drive conectado. Ya puedes subir documentos.")
            # limpia el code de la URL
            st.query_params.clear()
            return True
        except Exception as e:
            st.error(f"No pude completar OAuth: {type(e).__name__}: {e}")
            return False

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # asegura refresh_token
    )

    st.markdown("### 1) Haz clic para conectar Drive")
    st.link_button("Conectar Google Drive", auth_url)
    st.caption("Después de autorizar, Google te regresará a esta app y se guardará el token.")
    return False

def get_drive_user_credentials() -> UserCredentials:
    token = _get_token_json("drive_token")
    if not token:
        raise RuntimeError("Drive no está conectado por OAuth todavía.")

    creds = UserCredentials(
        token=token.get("token"),
        refresh_token=token.get("refresh_token"),
        token_uri=token.get("token_uri"),
        client_id=token.get("client_id"),
        client_secret=token.get("client_secret"),
        scopes=token.get("scopes"),
    )

    # refresca si es necesario y persiste
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token["token"] = creds.token
            _set_token_json("drive_token", token)

    return creds

