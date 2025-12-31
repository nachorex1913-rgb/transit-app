import streamlit as st
from transit_core.auth import drive_oauth_ready_ui

st.title("OAuth Callback")
st.info("Procesando autorización de Google Drive...")

ok = drive_oauth_ready_ui()

if ok:
    st.success("✅ Drive conectado. Ya puedes subir documentos.")
    st.page_link("pages/03_Documentos.py", label="Ir a Documentos")
