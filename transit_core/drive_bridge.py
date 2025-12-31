# transit_core/drive_bridge.py
import base64
import requests
import streamlit as st


def _cfg() -> tuple[str, str]:
    url = st.secrets["apps_script"]["upload_url"]
    token = st.secrets["apps_script"]["token"]
    if not url or not token:
        raise RuntimeError("Faltan secrets: apps_script.upload_url o apps_script.token")
    return url, token


def _post(payload: dict, timeout: int = 90) -> dict:
    url, token = _cfg()
    payload = {**payload, "token": token}

    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    if not isinstance(data, dict):
        raise RuntimeError("Apps Script no devolvió JSON válido")

    if not data.get("ok"):
        raise RuntimeError(data.get("error", "Apps Script call failed"))

    return data


def upload_to_drive_via_script(folder_id: str, file_name: str, mime_type: str, file_bytes: bytes) -> str:
    data = _post({
        "action": "upload",
        "folder_id": folder_id,
        "file_name": file_name,
        "mime_type": mime_type or "application/octet-stream",
        "file_b64": base64.b64encode(file_bytes).decode("utf-8"),
    })
    return data["file_id"]


def create_case_folder_via_script(root_folder_id: str, case_id: str) -> dict:
    data = _post({
        "action": "create_case_folder",
        "root_folder_id": root_folder_id,
        "case_id": case_id,
    })
    return {
        "folder_id": data["folder_id"],
        "folder_url": data.get("folder_url"),
        "subfolders": data.get("subfolders", {}),
    }
