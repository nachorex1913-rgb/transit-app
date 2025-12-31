import streamlit as st

from transit_core.gsheets_db import list_cases, get_case, add_document, list_documents
from transit_core.drive_bridge import upload_to_drive_via_script

st.title("Documentos")

cases = list_cases()
if cases.empty:
    st.warning("No hay trámites aún.")
    st.stop()

case_id = st.selectbox("Trámite", cases["case_id"].tolist())
case = get_case(case_id)
if not case:
    st.stop()

folder_id = case.get("drive_folder_id")
if not folder_id:
    st.error("Este trámite aún no tiene carpeta en Drive. Ve a Trámites y créala.")
    st.stop()

doc_type = st.selectbox("Tipo de documento", ["title", "invoice", "pedimento", "photo", "other"])

file = st.file_uploader("Subir archivo", type=None)

if file and st.button("Subir a Drive"):
    try:
        file_bytes = file.read()
        drive_id = upload_to_drive_via_script(
            folder_id=folder_id,
            file_name=file.name,
            mime_type=file.type or "application/octet-stream",
            file_bytes=file_bytes,
        )
        add_document(case_id=case_id, drive_file_id=drive_id, file_name=file.name, doc_type=doc_type)
        st.success("✅ Documento subido y registrado.")
    except Exception as e:
        st.error(f"No se pudo subir: {type(e).__name__}: {e}")

st.divider()
st.subheader("Documentos registrados")
docs = list_documents(case_id)
st.dataframe(docs, use_container_width=True)
