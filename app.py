# app.py
import streamlit as st
from transit_core.gsheets_db import init_db

st.set_page_config(page_title="Transit", layout="wide")
init_db()

st.title("Transit")
st.caption("Gestión de importación / exportación de vehículos y artículos (Sheets + Drive + PDF)")
st.info("Usa el menú lateral para navegar: Clientes → Trámites → Documentos → PDF")

