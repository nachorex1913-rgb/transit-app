import streamlit as st
from transit_core.gsheets_db import list_cases, get_case, list_items, list_documents, list_clients
from transit_core.pdf_generator import generate_case_pdf
from transit_core.gdrive_storage import upload_file

st.title("PDF Final")
cases = list_cases()
if cases.empty:
    st.warning("No hay trámites.")
    st.stop()

case_id = st.selectbox("Trámite", cases["case_id"].tolist())
case = get_case(case_id)
if not case:
    st.stop()

try:
    cases = list_cases()
except Exception as e:
    st.error(f"Sheets temporal: {e}")
    st.stop()


clients = list_clients()
client = clients[clients["client_id"] == case["client_id"]].iloc[0].to_dict()

items = list_items(case_id)
docs = list_documents(case_id)

if st.button("Generar PDF"):
    pdf_bytes = generate_case_pdf(case=case, client=client, items_df=items, documents_df=docs)

    st.download_button("Descargar PDF", data=pdf_bytes, file_name=f"{case_id}.pdf", mime="application/pdf")

    if case.get("drive_folder_id"):
        drive_id = upload_file(case["drive_folder_id"], pdf_bytes, f"{case_id}.pdf", "05_PDF_Final")
        st.success("PDF guardado en Drive.")
    else:
        st.warning("No se guardó en Drive porque el trámite no tiene carpeta ligada.")

