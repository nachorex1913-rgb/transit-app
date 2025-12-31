import base64
import requests
import streamlit as st


def _post_to_script(payload: dict, timeout: int = 90) -> dict:
    url = st.secrets["apps_script"]["upload_url"]
    token = st.secrets["apps_script"]["token"]

    payload = {**payload, "token": token}

    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    if not isinstance(data, dict):
        raise RuntimeError("Apps Script response is not JSON object")

    if not data.get("ok"):
        raise RuntimeError(data.get("error", "Apps Script call failed"))

    return data


def upload_to_drive_via_script(folder_id: str, file_name: str, mime_type: str, file_bytes: bytes) -> str:
    data = _post_to_script(
        {
            "action": "upload",
            "folder_id": folder_id,
            "file_name": file_name,
            "mime_type": mime_type or "application/octet-stream",
            "file_b64": base64.b64encode(file_bytes).decode("utf-8"),
        }
    )
    return data["file_id"]


def create_case_folder_via_script(root_folder_id: str, case_id: str) -> dict:
    """
    Creates the case folder + subfolders under root_folder_id.
    Returns:
      {
        "folder_id": "...",
        "folder_url": "...",         (optional)
        "subfolders": { "01_Docs_Cliente": "...", ... } (optional)
      }
    """
    data = _post_to_script(
        {
            "action": "create_case_folder",
            "root_folder_id": root_folder_id,
            "case_id": case_id,
        }
    )

    # minimum expected
    if "folder_id" not in data:
        raise RuntimeError("Apps Script did not return folder_id")

    return {
        "folder_id": data["folder_id"],
        "folder_url": data.get("folder_url"),
        "subfolders": data.get("subfolders"),
    }
