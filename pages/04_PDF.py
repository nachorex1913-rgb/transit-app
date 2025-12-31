# pages/04_PDF.py
import streamlit as st
from datetime import datetime

from transit_core.gsheets_db import (
    list_cases,
    get_case,
    list_documents,
    add_document,
    update_case_fields,
)

# Items
try:
    from transit_core.gsheets_db import list_items
except Exception:
    list_items = None

# PDF generator (ajusta si tu función se llama distinto)
try:
    from transit_core.pdf_generator import generate_case_pdf
except Exception:
    generate_case_pdf = None

from transit_core.drive_bridge import upload_to_drive_via_script


st.title("PDF Final")

def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def pdf_filename(case_id: str) -> str:
    return f"{case_id}.pdf"

def ensure_pdf_generator():
    if generate_case_pdf is None:
        st.error("No encontré generate_case_pdf en transit_core/pdf_generator.py")
        st.stop()

cases = list_cases()
if cases is None or cases.empty:
    st.warning("No hay trámites aún.")
    st.stop()

case_id = st.selectbox("Selecciona un trámite", cases["case_id"].tolist())
case = get_case(case_id)
if not case:
    st.stop()

st.divider()
st.subheader("Resumen")
c1, c2, c3 = st.columns(3)
c1.metric("Trámite", case.get("case_id", ""))
c2.metric("Fecha", case.get("case_date", ""))
c3.metric("Estatus", case.get("status", "Borrador"))

st.caption(f"Origen: {case.get('origin','')}  |  Destino: {case.get('destination','')}")

folder_id = case.get("drive_folder_id")
if folder_id:
    st.caption("Drive folder_id:")
    st.code(folder_id)
else:
    st.warning("Este trámite no tiene drive_folder_id. Ve a Trámites y asígnalo antes de subir el PDF.")

# Mostrar items y docs (opcional)
with st.expander("Ver ítems / documentos registrados", expanded=False):
    if list_items is not None:
        items_df = list_items(case_id)
        if items_df is not None and not items_df.empty:
            st.subheader("Ítems")
            st.dataframe(items_df, use_container_width=True)
        else:
            st.caption("Sin ítems.")
    else:
        st.caption("list_items no disponible en gsheets_db.")

    st.subheader("Documentos")
    docs_df = list_documents(case_id)
    if docs_df is not None and not docs_df.empty:
        st.dataframe(docs_df, use_container_width=True)
    else:
        st.caption("Sin documentos.")

st.divider()
st.subheader("Generación")

ensure_pdf_generator()

if "pdf_cache" not in st.session_state:
    st.session_state["pdf_cache"] = {}

btn1, btn2, btn3 = st.columns([1, 1, 2])

with btn1:
    if st.button("Generar PDF", use_container_width=True):
        try:
            with st.spinner("Generando PDF..."):
                pdf_bytes = generate_case_pdf(case_id)  # debe retornar bytes
                if not isinstance(pdf_bytes, (bytes, bytearray)) or len(pdf_bytes) < 200:
                    raise RuntimeError("El generador no devolvió bytes válidos.")
                st.session_state["pdf_cache"][case_id] = bytes(pdf_bytes)
            st.success("✅ PDF generado.")
        except Exception as e:
            st.error(f"No se pudo generar: {type(e).__name__}: {e}")

pdf_bytes = st.session_state["pdf_cache"].get(case_id)

with btn2:
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

with btn3:
    st.caption("Tip: si cambias ítems o datos del cliente, vuelve a generar el PDF para reflejar cambios.")

st.divider()
st.subheader("Subir PDF Final a Drive (sin OAuth)")

if not folder_id:
    st.info("Primero asigna la carpeta del trámite (drive_folder_id).")
elif not pdf_bytes:
    st.info("Primero genera el PDF para poder subirlo.")
else:
    if st.button("Subir PDF a Drive", use_container_width=True):
        try:
            with st.spinner("Subiendo a Drive..."):
                filename = pdf_filename(case_id)

                drive_id = upload_to_drive_via_script(
                    folder_id=folder_id,
                    file_name=filename,
                    mime_type="application/pdf",
                    file_bytes=pdf_bytes,
                )

                # Registrar como documento
                add_document(
                    case_id=case_id,
                    drive_file_id=drive_id,
                    file_name=filename,
                    doc_type="pdf_final",
                )

                # Guardar referencia en el case
                update_case_fields(case_id, {
                    "final_pdf_drive_id": drive_id,
                    "final_pdf_uploaded_at": now_iso(),
                    "updated_at": now_iso(),
                })

            st.success(f"✅ PDF subido y guardado en el trámite. drive_file_id: {drive_id}")

            # refresca tabla de docs
            docs_df = list_documents(case_id)
            if docs_df is not None and not docs_df.empty:
                st.dataframe(docs_df, use_container_width=True)

        except Exception as e:
            st.error(f"No se pudo subir el PDF: {type(e).__name__}: {e}")
