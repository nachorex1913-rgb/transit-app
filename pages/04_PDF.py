# pages/04_PDF.py
import streamlit as st
from datetime import datetime

from transit_core.gsheets_db import (
    list_cases,
    get_case,
    list_documents,
    add_document,
)

# Si tu gsheets_db tiene list_items, úsalo. Si se llama distinto, ajusta este import.
try:
    from transit_core.gsheets_db import list_items
except Exception:
    list_items = None

# Generador de PDF (fallback a nombres comunes)
_generate_pdf_fn = None
try:
    from transit_core.pdf_generator import generate_case_pdf as _generate_pdf_fn
except Exception:
    try:
        from transit_core.pdf_generator import build_case_pdf as _generate_pdf_fn
    except Exception:
        _generate_pdf_fn = None

from transit_core.drive_bridge import upload_to_drive_via_script


st.title("PDF Final")

# -----------------------------
# Helpers
# -----------------------------
def _fmt(v):
    return "" if v is None else str(v)

def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _case_summary(case: dict):
    cols = st.columns(3)
    cols[0].metric("Trámite", _fmt(case.get("case_id")))
    cols[1].metric("Fecha", _fmt(case.get("case_date")))
    cols[2].metric("Estatus", _fmt(case.get("status", "Borrador")))

    st.caption(f"Origen: {_fmt(case.get('origin', ''))}  |  Destino: {_fmt(case.get('destination', ''))}")

def _ensure_pdf_generator():
    if _generate_pdf_fn is None:
        st.error(
            "No encontré la función del generador de PDF. "
            "En transit_core/pdf_generator.py debe existir generate_case_pdf(case_id) o build_case_pdf(case_id)."
        )
        st.stop()

def _pdf_filename(case_id: str) -> str:
    return f"{case_id}.pdf"

def _register_pdf_in_sheets(case_id: str, drive_id: str, filename: str):
    """
    Registra el PDF como documento del caso.
    Si ya existe un pdf_final para ese caso, igual puedes permitir múltiples versiones.
    """
    add_document(
        case_id=case_id,
        drive_file_id=drive_id,
        file_name=filename,
        doc_type="pdf_final",
    )

# -----------------------------
# UI principal
# -----------------------------
cases = list_cases()
if cases is None or cases.empty:
    st.warning("No hay trámites aún.")
    st.stop()

case_id = st.selectbox("Selecciona un trámite", cases["case_id"].tolist())
case = get_case(case_id)
if not case:
    st.warning("No se encontró el trámite seleccionado.")
    st.stop()

st.divider()
st.subheader("Resumen del trámite")
_case_summary(case)

# Cliente (si tu case ya trae datos de cliente, muéstralos)
with st.expander("Ver datos del cliente (si están disponibles)", expanded=False):
    st.write({
        "client_id": case.get("client_id"),
        "client_name": case.get("client_name") or case.get("name"),
        "address": case.get("address"),
        "id_number": case.get("id_number") or case.get("identification"),
        "phone": case.get("phone"),
        "email": case.get("email"),
    })

# Items (vehículos/artículos) si existe list_items
st.divider()
st.subheader("Vehículos / Artículos (Sheets)")

if list_items is None:
    st.info("No encontré list_items(case_id) en gsheets_db.py. Si la función existe con otro nombre, ajústalo en 04_PDF.py.")
else:
    items_df = list_items(case_id)
    if items_df is None or items_df.empty:
        st.warning("No hay ítems registrados para este trámite.")
    else:
        # Vista rápida
        st.dataframe(items_df, use_container_width=True)

# Docs registrados
st.divider()
st.subheader("Documentos registrados (Sheets)")
docs_df = list_documents(case_id)
if docs_df is None or docs_df.empty:
    st.caption("Aún no hay documentos registrados.")
else:
    st.dataframe(docs_df, use_container_width=True)

# Carpeta Drive
st.divider()
st.subheader("Carpeta en Drive")
folder_id = case.get("drive_folder_id")
if not folder_id:
    st.error("Este trámite NO tiene 'drive_folder_id'. Ve a Trámites y asígnale/crea la carpeta antes de subir el PDF.")
else:
    st.code(folder_id)

# -----------------------------
# Generación del PDF
# -----------------------------
st.divider()
st.subheader("Generar PDF")

_ensure_pdf_generator()

if "pdf_cache" not in st.session_state:
    st.session_state["pdf_cache"] = {}  # case_id -> bytes

colA, colB, colC = st.columns([1, 1, 2])

with colA:
    if st.button("Generar PDF", use_container_width=True):
        try:
            with st.spinner("Generando PDF..."):
                pdf_bytes = _generate_pdf_fn(case_id)  # debe retornar bytes
                if not isinstance(pdf_bytes, (bytes, bytearray)) or len(pdf_bytes) < 100:
                    raise RuntimeError("El generador no devolvió bytes válidos del PDF.")
                st.session_state["pdf_cache"][case_id] = bytes(pdf_bytes)
            st.success("✅ PDF generado.")
        except Exception as e:
            st.error(f"No se pudo generar el PDF: {type(e).__name__}: {e}")

pdf_bytes = st.session_state["pdf_cache"].get(case_id)

with colB:
    if pdf_bytes:
        st.download_button(
            "Descargar PDF",
            data=pdf_bytes,
            file_name=_pdf_filename(case_id),
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        st.button("Descargar PDF", disabled=True, use_container_width=True)

with colC:
    st.caption(
        "Tip: Genera el PDF una vez y luego podrás descargarlo o subirlo. "
        "Si cambias datos del trámite, vuelve a generar."
    )

# -----------------------------
# Subir PDF a Drive (Apps Script)
# -----------------------------
st.divider()
st.subheader("Subir PDF final a Drive")

if not folder_id:
    st.info("Primero asigna la carpeta del trámite (drive_folder_id).")
else:
    if not pdf_bytes:
        st.info("Primero genera el PDF para poder subirlo a Drive.")
    else:
        up1, up2 = st.columns([1, 2])

        with up1:
            do_upload = st.button("Subir PDF a Drive", use_container_width=True)

        with up2:
            st.caption("Se subirá directamente a la carpeta del trámite usando Apps Script (sin OAuth en Streamlit).")

        if do_upload:
            try:
                with st.spinner("Subiendo PDF a Drive..."):
                    filename = _pdf_filename(case_id)
                    drive_id = upload_to_drive_via_script(
                        folder_id=folder_id,
                        file_name=filename,
                        mime_type="application/pdf",
                        file_bytes=pdf_bytes,
                    )

                # Registrar en Sheets
                _register_pdf_in_sheets(case_id, drive_id, filename)

                st.success(f"✅ PDF subido y registrado. drive_file_id: {drive_id}")

                # Refresca tabla
                docs_df = list_documents(case_id)
                if docs_df is not None and not docs_df.empty:
                    st.dataframe(docs_df, use_container_width=True)

            except Exception as e:
                st.error(f"No se pudo subir el PDF: {type(e).__name__}: {e}")

st.caption(f"Última actualización UI: {_now_str()}")
