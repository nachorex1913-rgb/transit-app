# transit_core/drive_bridge.py
from __future__ import annotations

from typing import Dict, Any
import base64
import requests
import streamlit as st

DRIVE_BRIDGE_VERSION = "DRIVE_BRIDGE_APPS_SCRIPT_v3_2026-01-02"


def _script_url() -> str:
    url = st.secrets.get("drive", {}).get("script_url", "")
    if not url:
        raise RuntimeError("Falta secrets: drive.script_url")
    return url


def _script_token() -> str:
    tok = st.secrets.get("drive", {}).get("script_token", "")
    if not tok:
        raise RuntimeError("Falta secrets: drive.script_token")
    return tok


def create_case_folder_via_script(root_folder_id: str, case_id: str, folder_name: str) -> Dict[str, Any]:
    payload = {
        "token": _script_token(),
        "action": "create_case_folder",
        "root_folder_id": root_folder_id,
        "case_id": case_id,
        "folder_name": folder_name,
        "version": DRIVE_BRIDGE_VERSION,
    }
    r = requests.post(_script_url(), json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Drive Script error: {data}")
    return data


def upload_file_to_case_folder_via_script(
    case_folder_id: str,
    file_bytes: bytes,
    file_name: str,
    mime_type: str = "application/octet-stream",
    subfolder: str = "",
) -> Dict[str, Any]:
    if not case_folder_id:
        raise ValueError("case_folder_id vacío. No se puede subir archivo.")

    b64 = base64.b64encode(file_bytes).decode("utf-8")

    payload = {
        "token": _script_token(),
        "action": "upload_file",
        "case_folder_id": case_folder_id,
        "file_name": file_name,
        "mime_type": mime_type,
        "subfolder": subfolder or "",
        "file_b64": b64,
        "version": DRIVE_BRIDGE_VERSION,
    }
    r = requests.post(_script_url(), json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Drive Script upload error: {data}")
    return data


# -----------------------------
# Aliases (compatibilidad)
# -----------------------------
def create_case_folder(*args, **kwargs):
    return create_case_folder_via_script(*args, **kwargs)


def upload_file_via_script(*args, **kwargs):
    # alias genérico
    return upload_file_to_case_folder_via_script(*args, **kwargs)


def upload_file_to_drive_via_script(*args, **kwargs):
    # alias por si en PDF o Documentos importaban esto
    return upload_file_to_case_folder_via_script(*args, **kwargs)
