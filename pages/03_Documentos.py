import streamlit as st
from googleapiclient.errors import HttpError

from transit_core.auth import drive_oauth_ready_ui
from transit_core.gsheets_db import list_cases, get_case, add_document, list_documents
from transit_core.gdrive_storage import upload_file

st.title("Documentos")

if not drive_oauth_ready_ui():
    st.stop()

cases = list_cases()
if cases.empty:
    st.warning("No hay trámites aún.")
    st.stop()

case_id = st.selectbox("Trámite", cases["case_id"].tolist())
case = get_case(case_id)
if not case:
    st.stop()

if not case.get("drive_folder_id"):
    st.error("Este trámite aún no tiene carpeta en Drive. Ve a Trámites y créala.")
    st.stop()

doc_type = st.selectbox("Tipo de documento", ["title", "invoice", "pedimento", "photo", "other"])
subfolder = {
    "title": "02_Vehiculos",
    "invoice": "01_Docs_Cliente",
    "pedimento": "04_Pedimentos",
    "photo": "02_Vehiculos",
    "other": "01_Docs_Cliente",
}[doc_type]

file = st.file_uploader("Subir archivo", type=None)

if file and st.button("Subir a Drive"):
    file_bytes = file.read()
    try:
        drive_id = upload_file(case["drive_folder_id"], file_bytes, file.name, subfolder)
        add_document(case_id=case_id, drive_file_id=drive_id, file_name=file.name, doc_type=doc_type)
        st.success("Documento subido y registrado.")
    except HttpError as e:
        st.error(f"Drive HttpError (status {e.resp.status}).")
        st.text(e.content.decode("utf-8", errors="ignore"))

st.divider()
st.subheader("Documentos registrados")
docs = list_documents(case_id)
st.dataframe(docs, use_container_width=True)
