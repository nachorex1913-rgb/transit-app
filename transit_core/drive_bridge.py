# transit_core/drive_bridge.py
import base64
import requests
import streamlit as st


def _get_script_config() -> tuple[str, str]:
    url = st.secrets["apps_script"]["upload_url"]
    token = st.secrets["apps_script"]["token"]

    if not url or not token:
        raise RuntimeError("Faltan secrets: apps_script.upload_url o apps_script.token")

    return url, token


def _post_to_script(payload: dict, timeout: int = 90) -> dict:
    url, token = _get_script_config()
    payload = {**payload, "token": token}

    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    if not isinstance(data, dict):
        raise RuntimeError("Apps Script no devolvió un JSON válido.")

    if not data.get("ok"):
        raise RuntimeError(data.get("error", "Apps Script call failed"))

    return data


def upload_to_drive_via_script(folder_id: str, file_name: str, mime_type: str, file_bytes: bytes) -> str:
    """
    Sube un archivo a una carpeta Drive (folder_id) usando Apps Script Web App.
    Retorna: Drive file_id
    """
    if not folder_id:
        raise ValueError("folder_id es requerido")
    if not file_name:
        raise ValueError("file_name es requerido")
    if file_bytes is None:
        raise ValueError("file_bytes es requerido")

    payload = {
        "action": "upload",
        "folder_id": folder_id,
        "file_name": file_name,
        "mime_type": mime_type or "application/octet-stream",
        "file_b64": base64.b64encode(file_bytes).decode("utf-8"),
    }

    data = _post_to_script(payload, timeout=90)
    file_id = data.get("file_id")
    if not file_id:
        raise RuntimeError("Apps Script upload no devolvió file_id")

    return file_id


def create_case_folder_via_script(root_folder_id: str, case_id: str) -> dict:
    """
    Crea la carpeta del trámite y subcarpetas estándar dentro de root_folder_id.
    Retorna:
      {
        "folder_id": "...",
        "folder_url": "...",
        "subfolders": { "01_Docs_Cliente": "...", ... }
      }
    """
    if not root_folder_id:
        raise ValueError("root_folder_id es requerido")
    if not case_id:
        raise ValueError("case_id es requerido")

    payload = {
        "action": "create_case_folder",
        "root_folder_id": root_folder_id,
        "case_id": case_id,
    }

    data = _post_to_script(payload, timeout=90)

    folder_id = data.get("folder_id")
    if not folder_id:
        raise RuntimeError("Apps Script create_case_folder no devolvió folder_id")

    return {
        "folder_id": folder_id,
        "folder_url": data.get("folder_url"),
        "subfolders": data.get("subfolders", {}),
    }
