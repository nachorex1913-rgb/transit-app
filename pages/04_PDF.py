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
from transit_core.drive_bridge import upload_to_drive_via_script

# Importa tu generador real:
from transit_core.pdf_generator import generate_case_pdf


st.set_page_config(page_title="PDF Final", layout="wide")
st.title("PDF Final")


def _now_iso_utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# 1) Cargar casos (una vez)
cases_df = list_cases()
if cases_df is None or cases_df.empty:
    st.info("No hay trámites aún.")
    st.stop()

cases_df = cases_df.fillna("")
cases_df["label"] = cases_df["case_id"].astype(str) + " — " + cases_df.get("status", "")

selected = st.selectbox("Selecciona un trámite", cases_df["case_id"].tolist())
case_id = str(selected)

case = get_case(case_id)
if not case:
    st.error("No se pudo cargar el trámite.")
    st.stop()

client = get_client(case.get("client_id", ""))
items_df = list_items(case_id=case_id)
docs_df = list_documents(case_id=case_id)

st.divider()
st.write(f"**Trámite:** {case_id}")
st.write(f"**Cliente:** {client.get('name','') if client else ''}")
st.write(f"**Drive folder:** {case.get('drive_folder_id','')}")

# 2) Generar PDF
st.subheader("Generar PDF")

gen_col1, gen_col2 = st.columns([1, 2])

with gen_col1:
    gen_btn = st.button("Generar PDF", type="primary")

with gen_col2:
    st.caption("Genera el PDF final desde los datos del trámite. Luego puedes descargarlo o subirlo a Drive.")

if "pdf_bytes" not in st.session_state:
    st.session_state["pdf_bytes"] = None

if gen_btn:
    try:
        pdf_bytes = generate_case_pdf(case, client, items_df, docs_df)
        if not isinstance(pdf_bytes, (bytes, bytearray)) or len(pdf_bytes) == 0:
            raise RuntimeError("generate_case_pdf no devolvió bytes válidos.")
        st.session_state["pdf_bytes"] = bytes(pdf_bytes)
        st.success("PDF generado.")
    except Exception as e:
        st.error(f"Error generando PDF: {type(e).__name__}: {e}")

pdf_bytes = st.session_state.get("pdf_bytes")
if pdf_bytes:
    file_name = f"{case_id}_PDF_Final.pdf"

    st.download_button(
        label="Descargar PDF",
        data=pdf_bytes,
        file_name=file_name,
        mime="application/pdf",
        type="secondary",
    )

    st.divider()
    st.subheader("Subir PDF a Drive")

    if not case.get("drive_folder_id"):
        st.warning("Este trámite no tiene drive_folder_id. Crea o re-crea la carpeta del trámite.")
    else:
        up_btn = st.button("Subir PDF final a Drive", type="primary")

        if up_btn:
            try:
                drive_folder_id = case["drive_folder_id"]
                drive_file_id = upload_to_drive_via_script(
                    folder_id=drive_folder_id,
                    file_name=file_name,
                    mime_type="application/pdf",
                    file_bytes=pdf_bytes,
                )

                # Registrar en documents
                add_document(
                    case_id=case_id,
                    drive_file_id=drive_file_id,
                    file_name=file_name,
                    doc_type="pdf_final",
                    item_id="",
                )

                # Guardar en cases
                update_case_fields(case_id, {
                    "final_pdf_drive_id": drive_file_id,
                    "final_pdf_uploaded_at": _now_iso_utc(),
                    "updated_at": _now_iso_utc(),
                })

                st.success(f"PDF subido a Drive: {drive_file_id}")
                st.rerun()

            except Exception as e:
                st.error(f"Error subiendo PDF: {type(e).__name__}: {e}")
else:
    st.info("Genera el PDF para habilitar descarga y subida a Drive.")
