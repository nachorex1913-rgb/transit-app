import streamlit as st
from transit_core.gsheets_db import upsert_client, search_clients

st.title("Clientes")

with st.form("new_client"):
    st.subheader("Alta / Actualización")
    name = st.text_input("Nombre completo*", "")
    address = st.text_area("Dirección", "")
    id_type = st.text_input("Tipo de identificación", "ID")
    id_number = st.text_input("Número de identificación", "")
    phone = st.text_input("Teléfono", "")
    email = st.text_input("Email", "")
    country_destination = st.text_input("País destino", "")
    submitted = st.form_submit_button("Guardar cliente")

    if submitted:
        if not name.strip():
            st.error("Nombre es obligatorio.")
        else:
            cid = upsert_client(name=name, address=address, id_type=id_type, id_number=id_number,
                               phone=phone, email=email, country_destination=country_destination)
            st.success(f"Cliente guardado: {cid}")

st.divider()
st.subheader("Buscar")
q = st.text_input("Buscar por nombre, teléfono, ID, etc.", "")
df = search_clients(q)
st.dataframe(df, use_container_width=True)

