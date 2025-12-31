import streamlit as st
import pandas as pd
from datetime import datetime

from transit_core.gsheets_db import (
    get_cases_df,
    get_clients_df,
    create_case,
    update_case_fields,
)

from transit_core.drive_bridge import create_case_folder_via_script


st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")


# --------- Helpers ----------
def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# --------- Load data ----------
clients_df = get_clients_df()
cases_df = get_cases_df()

if clients_df is None or clients_df.empty:
    st.warning("No hay clientes todavía. Crea un cliente primero en la página Clientes.")
    st.stop()

# --------- UI: Create case ----------
st.subheader("Crear trámite")

col1, col2, col3 = st.columns([2, 2, 2])

with col1:
    client_options = (
        clients_df[["client_id", "full_name"]]
        .fillna("")
        .assign(label=lambda d: d["client_id"] + " — " + d["full_name"])
    )
    selected_label = st.selectbox("Cliente", client_options["label"].tolist())
    selected_client_id = client_options.loc[client_options["label"] == selected_label, "client_id"].iloc[0]

with col2:
    case_type = st.selectbox("Tipo de trámite", ["Exportación", "Importación", "Otro"])

with col3:
    notes = st.text_input("Notas (opcional)", "")

create_btn = st.button("Crear trámite", type="primary")

if create_btn:
    try:
        # 1) Create case in Sheets (returns case dict or at least case_id)
        new_case = create_case(
            client_id=selected_client_id,
            case_type=case_type,
            notes=notes,
        )

        # Asegúrate que tu create_case devuelve case_id
        case_id = new_case["case_id"] if isinstance(new_case, dict) else new_case
        if not case_id:
            raise RuntimeError("create_case() no devolvió case_id")

        # 2) Create Drive folder via Apps Script
        root_folder_id = st.secrets["drive"]["root_folder_id"]
        drive_res = create_case_folder_via_script(root_folder_id=root_folder_id, case_id=case_id)
        drive_folder_id = drive_res["folder_id"]

        # 3) Save drive_folder_id in cases
        update_case_fields(case_id, {"drive_folder_id": drive_folder_id, "updated_at": _now_iso()})

        st.success(f"Trámite creado: {case_id}")
        st.info(f"Carpeta Drive creada: {drive_folder_id}")

        # refresh
        st.rerun()

    except Exception as e:
        st.error(f"Error creando trámite: {e}")


st.divider()

# --------- UI: List cases ----------
st.subheader("Listado de trámites")

cases_df = get_cases_df()
if cases_df is None or cases_df.empty:
    st.info("No hay trámites aún.")
    st.stop()

# Join with client names (optional)
clients_map = clients_df.set_index("client_id")["full_name"].to_dict()
cases_df = cases_df.copy()
if "client_id" in cases_df.columns:
    cases_df["client_name"] = cases_df["client_id"].map(clients_map)

show_cols = [c for c in ["case_id", "client_id", "client_name", "case_type", "status", "drive_folder_id", "created_at"] if c in cases_df.columns]
st.dataframe(cases_df[show_cols].sort_values(by="case_id", ascending=False), use_container_width=True)

st.caption("Tip: si no ves drive_folder_id, revisa que update_case_fields esté funcionando y que cases tenga esa columna.")
