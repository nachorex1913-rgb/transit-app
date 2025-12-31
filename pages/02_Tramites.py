# pages/02_Tramites.py
import streamlit as st
from datetime import datetime

from transit_core.gsheets_db import (
    list_clients,
    list_cases,
    create_case,
    update_case_fields,
)
from transit_core.drive_bridge import create_case_folder_via_script


st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")


def _now_iso_utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# -------- Data --------
clients_df = list_clients()
cases_df = list_cases()

if clients_df is None or clients_df.empty:
    st.warning("No hay clientes todavía. Crea un cliente primero en la página Clientes.")
    st.stop()

# -------- Crear trámite --------
st.subheader("Crear trámite")

c1, c2, c3 = st.columns([2, 2, 3])

with c1:
    # En tu DB la columna es "name"
    clients_df = clients_df.fillna("")
    clients_df["label"] = clients_df["client_id"].astype(str) + " — " + clients_df["name"].astype(str)

    selected_label = st.selectbox("Cliente", clients_df["label"].tolist())
    selected_client_id = clients_df.loc[clients_df["label"] == selected_label, "client_id"].iloc[0]

with c2:
    origin = st.text_input("Origen", value="USA")
    destination = st.text_input("Destino", value="")

with c3:
    notes = st.text_input("Notas (opcional)", value="")

create_btn = st.button("Crear trámite", type="primary")

if create_btn:
    try:
        # 1) Crear case en Sheets
        case_id = create_case(
            client_id=str(selected_client_id),
            origin=origin.strip() or "USA",
            destination=destination.strip(),
            notes=notes.strip(),
        )

        # 2) Crear carpeta Drive por Apps Script
        root_folder_id = st.secrets["drive"]["root_folder_id"]
        res = create_case_folder_via_script(root_folder_id=root_folder_id, case_id=case_id)
        drive_folder_id = res["folder_id"]

        # 3) Guardar drive_folder_id en cases
        update_case_fields(case_id, {
            "drive_folder_id": drive_folder_id,
            "updated_at": _now_iso_utc(),
        })

        st.success(f"Trámite creado: {case_id}")
        st.info(f"Carpeta Drive: {drive_folder_id}")
        st.rerun()

    except Exception as e:
        st.error(f"Error creando trámite: {type(e).__name__}: {e}")

st.divider()

# -------- Listado --------
st.subheader("Listado de trámites")

cases_df = list_cases()
if cases_df is None or cases_df.empty:
    st.info("No hay trámites aún.")
    st.stop()

# Mapeo client_id -> name (para mostrar bonito)
clients_map = clients_df.set_index("client_id")["name"].to_dict()

cases_df = cases_df.fillna("")
if "client_id" in cases_df.columns:
    cases_df["client_name"] = cases_df["client_id"].map(clients_map).fillna("")

# Columnas a mostrar (solo las que existan)
cols = [c for c in [
    "case_id", "client_id", "client_name",
    "case_date", "status", "origin", "destination",
    "drive_folder_id",
    "final_pdf_drive_id", "final_pdf_uploaded_at",
    "created_at", "updated_at"
] if c in cases_df.columns]

st.dataframe(
    cases_df[cols].sort_values(by="case_id", ascending=False),
    use_container_width=True
)

st.caption("Si drive_folder_id sale vacío, revisa: secrets.drive.root_folder_id y el deploy del Apps Script Web App.")
