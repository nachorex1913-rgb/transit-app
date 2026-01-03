# transit_core/drive_bridge.py
from __future__ import annotations

from typing import Dict, Any
import base64
import requests
import streamlit as st


def _require_secrets() -> Dict[str, str]:
    """
    Requiere:
    [drive]
    root_folder_id = "..."

    [apps_script]
    upload_url = "https://script.google.com/macros/s/.../exec"
    token = "transit_2025_super_secret_123"
    """
    drive = st.secrets.get("drive", {})
    apps = st.secrets.get("apps_script", {})

    root_folder_id = (drive.get("root_folder_id") or "").strip()
    upload_url = (apps.get("upload_url") or "").strip()
    token = (apps.get("token") or "").strip()

    missing = []
    if not root_folder_id:
        missing.append("drive.root_folder_id")
    if not upload_url:
        missing.append("apps_script.upload_url")
    if not token:
        missing.append("apps_script.token")

    if missing:
        raise RuntimeError(
            "Faltan secrets para Drive Bridge: "
            + ", ".join(missing)
            + ". Revisa tu .streamlit/secrets.toml / Secrets en Streamlit Cloud."
        )

    return {"root_folder_id": root_folder_id, "upload_url": upload_url, "token": token}


def create_case_folder_via_script(case_id: str, folder_name: str) -> Dict[str, Any]:
    """
    Crea UNA carpeta por trámite (sin subcarpetas).
    Tu Apps Script ya crea subcarpetas por defecto en el snippet anterior,
    pero tú dijiste que NO quieres subcarpetas.
    => OJO: si tu Apps Script actual todavía crea subcarpetas, ahí mismo debes quitarlas.
    (La app aquí solo consume folder_id).
    """
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
    """
    Sube archivo al folder del trámite.
    Apps Script espera:
    action=upload, folder_id, file_name, mime_type, file_b64
    """
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
