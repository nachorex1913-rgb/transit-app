# pages/04_PDF.py
import streamlit as st
from datetime import datetime

from transit_core.gsheets_db import (
    list_cases,
    get_case,
    get_client,
    list_items,
    list_documents,
    add_document,
    update_case_fields,
)

from transit_core.pdf_generator import generate_case_pdf
from transit_core.drive_bridge import upload_to_drive_via_script


st.title("PDF Final")

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def pdf_filename(case_id: str) -> str:
    return f"{case_id}.pdf"

cases = list_cases()
if cases is None or cases.empty:
    st.warning("No hay trámites aún.")
    st.stop()

case_id = st.selectbox("Selecciona un trámite", cases["case_id"].tolist())
case = get_case(case_id)
if not case:
    st.stop()

client = get_client(case.get("client_id", ""))
if not client:
    st.error("No se encontró el cliente asociado a este trámite.")
    st.stop()

folder_id = case.get("drive_folder_id", "")

st.divider()
c1, c2, c3 = st.columns(3)
c1.metric("Trámite", case.get("case_id", ""))
c2.metric("Fecha", case.get("case_date", ""))
c3.metric("Estatus", case.get("status", "Borrador"))
st.caption(f"Origen: {case.get('origin','')}  |  Destino: {case.get('destination','')}")

if folder_id:
    st.caption("Drive folder_id:")
    st.code(folder_id)
else:
    st.warning("Este trámite no tiene carpeta en Drive (drive_folder_id). Ve a Trámites y créala primero.")

items_df = list_items(case_id)
docs_df = list_documents(case_id)

with st.expander("Ver ítems / documentos", expanded=False):
    st.subheader("Ítems")
    st.dataframe(items_df, use_container_width=True)
    st.subheader("Documentos")
    st.dataframe(docs_df, use_container_width=True)

st.divider()
st.subheader("Generación del PDF")

if "pdf_cache" not in st.session_state:
    st.session_state["pdf_cache"] = {}

b1, b2, b3 = st.columns([1,1,2])

with b1:
    if st.button("Generar PDF", use_container_width=True):
        try:
            with st.spinner("Generando PDF..."):
                pdf_bytes = generate_case_pdf(case, client, items_df, docs_df)
                if not isinstance(pdf_bytes, (bytes, bytearray)) or len(pdf_bytes) < 200:
                    raise RuntimeError("El generador no devolvió bytes válidos.")
                st.session_state["pdf_cache"][case_id] = bytes(pdf_bytes)
            st.success("✅ PDF generado.")
        except Exception as e:
            st.error(f"No se pudo generar PDF: {type(e).__name__}: {e}")

pdf_bytes = st.session_state["pdf_cache"].get(case_id)

with b2:
    if pdf_bytes:
        st.download_button(
            "Descargar PDF",
            data=pdf_bytes,
            file_name=pdf_filename(case_id),
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        st.button("Descargar PDF", disabled=True, use_container_width=True)

with b3:
    st.caption("Si cambias ítems o datos del cliente, vuelve a generar el PDF.")

st.divider()
st.subheader("Subir PDF Final a Drive")

if not folder_id:
    st.info("Primero asigna/crea la carpeta del trámite en Drive.")
elif not pdf_bytes:
    st.info("Primero genera el PDF.")
else:
    if st.button("Subir PDF a Drive", use_container_width=True):
        try:
            with st.spinner("Subiendo PDF a Drive..."):
                filename = pdf_filename(case_id)

                drive_id = upload_to_drive_via_script(
                    folder_id=folder_id,
                    file_name=filename,
                    mime_type="application/pdf",
                    file_bytes=pdf_bytes,
                )

                # registra como doc
                add_document(
                    case_id=case_id,
                    drive_file_id=drive_id,
                    file_name=filename,
                    doc_type="pdf_final",
                    item_id="",
                )

                # guarda referencia en cases
                update_case_fields(case_id, {
                    "final_pdf_drive_id": drive_id,
                    "final_pdf_uploaded_at": now_str(),
                    "updated_at": now_str(),
                })

            st.success(f"✅ PDF subido y registrado. drive_file_id: {drive_id}")

        except Exception as e:
            st.error(f"No se pudo subir el PDF: {type(e).__name__}: {e}")
