# transit_core/gdrive_storage.py
from __future__ import annotations
from datetime import datetime
from typing import Optional

import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from .ids import normalize_name_for_folder

@st.cache_resource
def _drive():
    sa = st.secrets["gcp_service_account"]
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(sa, scopes=scopes)
    return build("drive", "v3", credentials=creds)

SUBFOLDERS = {
    "01_Docs_Cliente": "01_Docs_Cliente",
    "02_Vehiculos": "02_Vehiculos",
    "03_Articulos": "03_Articulos",
    "04_Pedimentos": "04_Pedimentos",
    "05_PDF_Final": "05_PDF_Final",
}

def create_case_folder(case_id: str, client_name: str, case_date: str) -> str:
    root_id = st.secrets["DRIVE_ROOT_FOLDER_ID"]
    safe_name = normalize_name_for_folder(client_name)
    folder_name = f"{case_date}_{case_id}_{safe_name}"

    service = _drive()
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [root_id],
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    folder_id = folder["id"]

    # create subfolders
    for sf in SUBFOLDERS.values():
        service.files().create(
            body={
                "name": sf,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [folder_id],
            },
            fields="id"
        ).execute()

    return folder_id

def _find_child_folder(parent_id: str, name: str) -> Optional[str]:
    service = _drive()
    q = (
        f"'{parent_id}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"name='{name}' and trashed=false"
    )
    res = service.files().list(q=q, fields="files(id,name)").execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None

def upload_file(case_folder_id: str, file_bytes: bytes, filename: str, subfolder_key: str) -> str:
    service = _drive()
    sub_name = SUBFOLDERS.get(subfolder_key, subfolder_key)
    sub_id = _find_child_folder(case_folder_id, sub_name) or case_folder_id

    media = MediaInMemoryUpload(file_bytes, resumable=False)
    metadata = {"name": filename, "parents": [sub_id]}
    f = service.files().create(body=metadata, media_body=media, fields="id").execute()
    return f["id"]

