# transit_core/drive_bridge.py
from __future__ import annotations

from typing import Dict, Any
import requests
import streamlit as st


def _require_secrets() -> Dict[str, str]:
    drive = st.secrets.get("drive", {})
    script_url = drive.get("script_url", "").strip()
    token = drive.get("token", "").strip()
    root_folder_id = drive.get("root_folder_id", "").strip()

    missing = []
    if not script_url:
        missing.append("drive.script_url")
    if not token:
        missing.append("drive.token")
    if not root_folder_id:
        missing.append("drive.root_folder_id")

    if missing:
        raise RuntimeError(
            "Faltan secrets para Drive Bridge: "
            + ", ".join(missing)
            + ". Revisa tu .streamlit/secrets.toml"
        )

    return {"script_url": script_url, "token": token, "root_folder_id": root_folder_id}


def create_case_folder_via_script(root_folder_id: str, case_id: str, folder_name: str) -> Dict[str, Any]:
    s = _require_secrets()
    url = s["script_url"]

    payload = {
        "token": s["token"],
        "action": "create_case_folder",
        "root_folder_id": root_folder_id,
        "case_id": case_id,
        "folder_name": folder_name,
    }

    r = requests.post(url, json=payload, timeout=30)
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
    subfolder: str = "",
) -> Dict[str, Any]:
    s = _require_secrets()
    url = s["script_url"]

    files = {
        "file": (file_name, file_bytes, mime_type or "application/octet-stream")
    }
    data = {
        "token": s["token"],
        "action": "upload_file",
        "case_folder_id": case_folder_id,
        "subfolder": subfolder or "",
        "file_name": file_name,
        "mime_type": mime_type or "application/octet-stream",
    }

    r = requests.post(url, data=data, files=files, timeout=60)
    r.raise_for_status()
    out = r.json() if r.content else {}
    if not out.get("ok"):
        raise RuntimeError(f"Apps Script error subiendo archivo: {out}")
    return out
