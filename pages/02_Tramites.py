import streamlit as st
from datetime import datetime

from transit_core.gsheets_db import list_clients, list_cases, create_case
from transit_core.drive_bridge import create_case_folder_via_script
from transit_core.ids import next_case_id

st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")


def _now_iso_utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


st.subheader("Crear trámite")

clients_df = list_clients().fillna("")
if clients_df.empty:
    st.warning("No hay clientes. Crea uno primero.")
    st.stop()

c1, c2, c3 = st.columns([2, 2, 3])

with c1:
    clients_df["label"] = clients_df["client_id"].astype(str) + " — " + clients_df["name"].astype(str)
    selected_label = st.selectbox("Cliente", clients_df["label"].tolist())
    client_id = clients_df.loc[clients_df["label"] == selected_label, "client_id"].iloc[0]

with c2:
    origin = st.text_input("Origen", value="USA")
    destination = st.text_input("Destino", value="Guatemala")

with c3:
    notes = st.text_input("Notas (opcional)", value="")

create_btn = st.button("Crear trámite", type="primary")

if create_btn:
    try:
        # 1) Generar case_id sin escribir row aún
        cases_df = list_cases().fillna("")
        existing_ids = cases_df["case_id"].tolist() if "case_id" in cases_df.columns else []
        year = datetime.now().year
        case_id = next_case_id(existing_ids, year=year)

        # 2) Crear carpeta en Drive por Apps Script
        root_folder_id = st.secrets["drive"]["root_folder_id"]
        res = create_case_folder_via_script(root_folder_id=root_folder_id, case_id=case_id)
        drive_folder_id = res["folder_id"]

        # 3) Crear case en Sheets ya con drive_folder_id
        created_case_id = create_case(
            client_id=str(client_id),
            origin=origin.strip() or "USA",
            destination=destination.strip(),
            notes=notes.strip(),
            drive_folder_id=drive_folder_id,
        )

        st.success(f"Trámite creado: {created_case_id}")
        st.info(f"Carpeta Drive: {drive_folder_id}")
        st.rerun()

    except Exception as e:
        st.error(f"Error creando trámite: {type(e).__name__}: {e}")

st.divider()

st.subheader("Listado de trámites")
cases_df = list_cases().fillna("")
if cases_df.empty:
    st.info("No hay trámites aún.")
else:
    st.dataframe(cases_df.sort_values(by="case_id", ascending=False), use_container_width=True)
