# transit_core/drive_bridge.py
from __future__ import annotations

from typing import Dict, Any
import base64
import requests
import streamlit as st


def _require_secrets() -> Dict[str, str]:
    """
    Acepta dos formatos de secrets:

    Formato NUEVO:
    [drive]
    root_folder_id = "..."

    [apps_script]
    upload_url = "https://script.google.com/macros/s/.../exec"
    token = "..."

    Formato VIEJO (legacy):
    [drive]
    root_folder_id = "..."
    script_url = "https://script.google.com/macros/s/.../exec"
    token = "..."
    """
    drive = st.secrets.get("drive", {})
    apps = st.secrets.get("apps_script", {})  # puede no existir

    root_folder_id = (drive.get("root_folder_id") or "").strip()

    # Preferimos el nuevo, si existe; si no, caemos al viejo.
    upload_url = (apps.get("upload_url") or "").strip() or (drive.get("script_url") or "").strip()
    token = (apps.get("token") or "").strip() or (drive.get("token") or "").strip()

    missing = []
    if not root_folder_id:
        missing.append("drive.root_folder_id")
    if not upload_url:
        missing.append("apps_script.upload_url (o drive.script_url)")
    if not token:
        missing.append("apps_script.token (o drive.token)")

    if missing:
        raise RuntimeError(
            "Faltan secrets para Drive Bridge: "
            + ", ".join(missing)
            + ". Revisa tu .streamlit/secrets.toml / Secrets en Streamlit Cloud."
        )

    return {"root_folder_id": root_folder_id, "upload_url": upload_url, "token": token}


def create_case_folder_via_script(case_id: str, folder_name: str) -> Dict[str, Any]:
    s = _require_secrets()
    payload = {
        "token": s["token"],
        "action": "create_case_folder",
        "root_folder_id": s["root_folder_id"],
        "case_id": case_id,
        "folder_name": folder_name,
    }
    r = requests.post(s["upload_url"], json=payload, timeout=30)
    r.raise_for_status()
    out = r.json() if r.content else {}
    if not out.get("ok"):
        raise RuntimeError(f"Apps Script error creando carpeta: {out}")
    return out


def upload_file_to_case_folder_via_script(
    case_folder_id: str,
    file_bytes: bytes,
    file_name: str,
    mime_type: str,
) -> Dict[str, Any]:
    s = _require_secrets()
    file_b64 = base64.b64encode(file_bytes).decode("utf-8")

    payload = {
        "token": s["token"],
        "action": "upload",
        "folder_id": case_folder_id,
        "file_name": file_name,
        "mime_type": mime_type or "application/octet-stream",
        "file_b64": file_b64,
    }

    r = requests.post(s["upload_url"], json=payload, timeout=90)
    r.raise_for_status()
    out = r.json() if r.content else {}
    if not out.get("ok"):
        raise RuntimeError(f"Apps Script error subiendo archivo: {out}")
    return out
