import streamlit as st
from transit_core.gsheets_db import (
    list_clients, create_case, list_cases, get_case, list_items,
    add_vehicle_item, add_article_item, set_case_drive_folder
)
from transit_core.gdrive_storage import create_case_folder

st.title("Trámites")

clients = list_clients()
if clients.empty:
    st.warning("Primero crea un cliente.")
    st.stop()

st.subheader("Crear trámite")
client_label = clients.apply(lambda r: f"{r['client_id']} - {r['name']}", axis=1).tolist()
client_pick = st.selectbox("Cliente", client_label)
client_id = client_pick.split(" - ")[0].strip()

origin = st.text_input("Origen", "USA")
destination = st.text_input("Destino", "")
notes = st.text_area("Notas", "")

if st.button("Crear trámite"):
    case_id = create_case(client_id=client_id, origin=origin, destination=destination, notes=notes)
    st.success(f"Trámite creado: {case_id}")

st.divider()
st.subheader("Seleccionar trámite")
cases = list_cases()
case_pick = st.selectbox("Trámite", cases["case_id"].tolist() if not cases.empty else [])
if not case_pick:
    st.stop()

case = get_case(case_pick)
st.write(case)

# Crear carpeta Drive si no existe
if case and not case.get("drive_folder_id"):
    if st.button("Crear carpeta Drive para este trámite"):
        client_name = clients[clients["client_id"] == case["client_id"]].iloc[0]["name"]
        folder_id = create_case_folder(case_id=case["case_id"], client_name=client_name, case_date=case["case_date"])
        set_case_drive_folder(case["case_id"], folder_id)
        st.success("Carpeta creada y ligada al trámite.")

st.divider()
st.subheader("Agregar vehículo (manual)")
vin = st.text_input("VIN (17 caracteres)", "").strip().upper()
brand = st.text_input("Marca", "")
model = st.text_input("Modelo", "")
year = st.text_input("Año", "")
weight = st.text_input("Peso (opcional)", "")
if st.button("Guardar vehículo"):
    try:
        add_vehicle_item(case_id=case_pick, vin=vin, brand=brand, model=model, year=year, weight=weight)
        st.success("Vehículo guardado.")
    except Exception as e:
        st.error(str(e))

st.divider()
st.subheader("Agregar artículo (manual rápido)")
desc = st.text_input("Descripción", "")
a_brand = st.text_input("Marca (artículo)", "")
a_model = st.text_input("Modelo (artículo)", "")
a_weight = st.text_input("Peso (artículo)", "")
qty = st.number_input("Cantidad (siempre recomendado 1 por línea)", min_value=1, value=1, step=1)
if st.button("Guardar artículo"):
    if not desc.strip():
        st.error("Descripción requerida.")
    else:
        st.write({
  "case_id": case_pick,
  "description": desc,
  "brand": a_brand,
  "model": a_model,
  "weight": a_weight,
  "qty": qty
})
        add_article_item(case_id=case_pick, description=desc, brand=a_brand, model=a_model, weight=a_weight, quantity=int(qty))
        st.success("Artículo guardado.")

st.divider()
st.subheader("Items del trámite")
items = list_items(case_pick)
st.dataframe(items, use_container_width=True)

