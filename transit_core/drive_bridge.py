import base64
import requests
import streamlit as st

def upload_to_drive_via_script(folder_id: str, file_name: str, mime_type: str, file_bytes: bytes) -> str:
    url = st.secrets["apps_script"]["upload_url"]
    token = st.secrets["apps_script"]["token"]

    payload = {
        "token": token,
        "folder_id": folder_id,
        "file_name": file_name,
        "mime_type": mime_type or "application/octet-stream",
        "file_b64": base64.b64encode(file_bytes).decode("utf-8"),
    }

    r = requests.post(url, json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()

    if not data.get("ok"):
        raise RuntimeError(data.get("error", "Upload failed"))

    return data["file_id"]
