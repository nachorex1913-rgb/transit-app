import re
import streamlit as st
from datetime import datetime

from transit_core.gsheets_db import (
    list_clients,
    list_cases,
    get_case,
    create_case,
    list_items,
    add_vehicle_item,
    add_article_item,
)
from transit_core.drive_bridge import create_case_folder_via_script
from transit_core.ids import next_case_id
from transit_core.validators import normalize_vin, is_valid_vin
from transit_core.vin_ocr import extract_vin_from_image
from transit_core.vin_decode import decode_vin

st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")


def _parse_article_dictation(text: str) -> dict:
    t = (text or "").strip()
    data = {
        "ref": "",
        "brand": "",
        "model": "",
        "weight": "",
        "condition": "",
        "quantity": 1,
        "is_vehicle_part": False,
        "parent_vin": "",
        "description": "",
        "value": "",
    }
    if not t:
        return data

    parts = [p.strip() for p in re.split(r"\||\n|;", t) if p.strip()]
    if len(parts) == 1 and ":" not in parts[0]:
        data["description"] = parts[0]
        return data

    for p in parts:
        if ":" not in p:
            continue
        k, v = p.split(":", 1)
        k = k.strip().lower()
        v = v.strip()

        if k in ("ref", "referencia", "serie", "serial"):
            data["ref"] = v
        elif k in ("marca", "brand"):
            data["brand"] = v
        elif k in ("modelo", "model"):
            data["model"] = v
        elif k in ("peso", "weight"):
            data["weight"] = v
        elif k in ("estado", "condition"):
            data["condition"] = v
        elif k in ("cantidad", "qty", "quantity"):
            try:
                data["quantity"] = int(re.findall(r"\d+", v)[0])
            except Exception:
                data["quantity"] = 1
        elif k in ("parte_vehiculo", "parte del vehiculo", "es_parte", "vehicle_part"):
            data["is_vehicle_part"] = v.strip().lower() in ("si", "sí", "yes", "true", "1")
        elif k in ("vin", "parent_vin", "vin_padre"):
            data["parent_vin"] = normalize_vin(v)
        elif k in ("descripcion", "description"):
            data["description"] = v
        elif k in ("valor", "value"):
            data["value"] = v

    if not data["description"]:
        desc = " | ".join([x for x in [data["ref"], data["brand"], data["model"], data["condition"]] if x])
        data["description"] = desc.strip()

    return data


# ---------------------------
# Crear trámite
# ---------------------------
st.subheader("Crear trámite")

clients_df = list_clients().fillna("")
if clients_df.empty:
    st.warning("No hay clientes. Crea uno primero.")
    st.stop()

clients_df["label"] = clients_df["client_id"].astype(str) + " — " + clients_df["name"].astype(str)

c1, c2, c3 = st.columns([2, 2, 3])

with c1:
    selected_label = st.selectbox("Cliente", clients_df["label"].tolist(), key="create_case_client")
    row = clients_df.loc[clients_df["label"] == selected_label].iloc[0]
    client_id = str(row["client_id"])
    client_name = str(row["name"]).strip()

with c2:
    origin = st.text_input("Origen", value="USA", key="create_case_origin")
    destination = st.text_input("Destino", value="Guatemala", key="create_case_dest")

with c3:
    notes = st.text_input("Notas (opcional)", value="", key="create_case_notes")

if st.button("Crear trámite", type="primary", key="create_case_btn"):
    try:
        cases_df = list_cases().fillna("")
        existing_ids = cases_df["case_id"].tolist() if "case_id" in cases_df.columns else []
        year = datetime.now().year
        case_id_new = next_case_id(existing_ids, year=year)

        root_folder_id = st.secrets["drive"]["root_folder_id"]
        folder_name = f"{case_id_new} - {client_name}".strip()

        res = create_case_folder_via_script(
            root_folder_id=root_folder_id,
            case_id=case_id_new,
            folder_name=folder_name,
        )
        drive_folder_id = res["folder_id"]

        created_case_id = create_case(
            client_id=client_id,
            origin=origin.strip() or "USA",
            destination=destination.strip(),
            notes=notes.strip(),
            drive_folder_id=drive_folder_id,
        )

        st.success(f"Trámite creado: {created_case_id}")
        st.info(f"Carpeta: {folder_name}")
        st.rerun()

    except Exception as e:
        st.error(f"Error creando trámite: {type(e).__name__}: {e}")

st.divider()

# ---------------------------
# Seleccionar trámite
# ---------------------------
st.subheader("Gestionar ítems del trámite")

cases_df = list_cases().fillna("")
if cases_df.empty:
    st.info("No hay trámites aún.")
    st.stop()

selected_case_id = st.selectbox("Selecciona un trámite", cases_df["case_id"].tolist(), key="case_select")
case = get_case(str(selected_case_id))
if not case:
    st.error("No se pudo cargar el trámite.")
    st.stop()

case_id = str(case.get("case_id") or "")
items_df = list_items(case_id=case_id)
items_df = items_df.fillna("") if items_df is not None else items_df

st.write(f"**Trámite:** {case.get('case_id','')}")
st.write(f"**Cliente ID:** {case.get('client_id','')}")
st.write(f"**Drive folder:** {case.get('drive_folder_id','')}")

st.divider()
st.subheader("Items registrados")
if items_df is None or items_df.empty:
    st.info("Aún no hay vehículos ni artículos en este trámite.")
else:
    st.dataframe(items_df, use_container_width=True)

# ---------------------------
# VEHÍCULO por foto VIN
# ---------------------------
st.divider()
st.subheader("Agregar vehículo (VIN por foto)")

vin_image = st.file_uploader(
    "Sube foto del VIN (desde cámara)",
    type=["jpg", "jpeg", "png"],
    key=f"vin_uploader_{case_id}",
)

extract_btn = st.button("Extraer VIN de la foto", key=f"extract_vin_btn_{case_id}")

vin_res_key = f"vin_res_{case_id}"
vin_decoded_key = f"vin_decoded_{case_id}"
vin_last_key = f"vin_last_{case_id}"

veh_brand_key = f"veh_brand_{case_id}"
veh_model_key = f"veh_model_{case_id}"
veh_year_key = f"veh_year_{case_id}"

if vin_res_key not in st.session_state:
    st.session_state[vin_res_key] = {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "error": ""}

if vin_decoded_key not in st.session_state:
    st.session_state[vin_decoded_key] = {}

if vin_last_key not in st.session_state:
    st.session_state[vin_last_key] = ""

if extract_btn:
    if vin_image is None:
        st.warning("Sube una imagen primero.")
    else:
        res = extract_vin_from_image(vin_image.getvalue())
        st.session_state[vin_res_key] = res
        if res.get("error"):
            st.error(res["error"])

res = st.session_state.get(vin_res_key, {}) or {}
cands = res.get("candidates", []) or []
conf = float(res.get("confidence", 0.0) or 0.0)

with st.expander("🧪 Debug OCR"):
    st.write("confidence:", conf)
    st.write("candidates:", cands)
    st.text(res.get("raw_text", "") or "")

if cands:
    vin_detected = st.selectbox(
        "VIN detectados (elige el correcto)",
        cands,
        key=f"vin_candidates_{case_id}",
    )
else:
    vin_detected = res.get("vin", "") or ""

vin_input = st.text_input(
    "VIN detectado (puedes corregirlo)",
    value=vin_detected,
    key=f"vin_input_{case_id}",
)

vin_input_norm = normalize_vin(vin_input)

if vin_input_norm and vin_input_norm != st.session_state.get(vin_last_key, ""):
    st.session_state[vin_last_key] = vin_input_norm
    st.session_state[vin_decoded_key] = {}

decode_btn = st.button(
    "Decodificar VIN",
    key=f"decode_btn_{case_id}",
    disabled=(not vin_input_norm or len(vin_input_norm) != 17),
)

decoded = st.session_state.get(vin_decoded_key, {}) or {}

if decode_btn:
    out = decode_vin(vin_input_norm) or {}

    if out.get("error"):
        st.warning(out["error"])
        st.session_state[vin_decoded_key] = {}
        decoded = {}
    else:
        st.session_state[vin_decoded_key] = out
        decoded = out

        st.session_state[veh_brand_key] = str(decoded.get("brand", "") or "")
        st.session_state[veh_model_key] = str(decoded.get("model", "") or "")
        st.session_state[veh_year_key] = str(decoded.get("year", "") or "")

        if (st.session_state[veh_brand_key].strip()
            or st.session_state[veh_model_key].strip()
            or st.session_state[veh_year_key].strip()):
            st.success("VIN decodificado correctamente.")
        else:
            st.warning("Se consultó el decoder pero no devolvió datos útiles. Ingresa manual.")

st.write(f"**Confianza OCR:** {conf:.2f}")
if vin_input_norm and len(vin_input_norm) == 17 and not is_valid_vin(vin_input_norm):
    st.warning("VIN inválido (contiene I/O/Q o caracteres no permitidos). Verifica antes de guardar.")

with st.expander("🧪 Debug Decode"):
    st.json(decoded)

if veh_brand_key not in st.session_state:
    st.session_state[veh_brand_key] = ""
if veh_model_key not in st.session_state:
    st.session_state[veh_model_key] = ""
if veh_year_key not in st.session_state:
    st.session_state[veh_year_key] = ""

veh_c1, veh_c2, veh_c3 = st.columns(3)
with veh_c1:
    brand = st.text_input("Marca", key=veh_brand_key)
with veh_c2:
    model = st.text_input("Modelo", key=veh_model_key)
with veh_c3:
    year = st.text_input("Año", key=veh_year_key)

veh_c4, veh_c5, veh_c6 = st.columns(3)
with veh_c4:
    quantity = st.number_input("Cantidad", min_value=1, value=1, step=1, key=f"veh_qty_{case_id}")
with veh_c5:
    weight = st.text_input("Peso (lb/kg)", value="", key=f"veh_weight_{case_id}")
with veh_c6:
    value = st.text_input("Valor (USD)", value="", key=f"veh_value_{case_id}")

description = st.text_area("Descripción (opcional)", value="", height=80, key=f"veh_desc_{case_id}")

confirm_vehicle = st.checkbox(
    "✅ Confirmo que el VIN y la información son correctos antes de guardar.",
    value=False,
    key=f"veh_confirm_{case_id}",
)

if st.button("Guardar vehículo", type="primary", disabled=not confirm_vehicle, key=f"save_vehicle_{case_id}"):
    try:
        if len(vin_input_norm) != 17:
            raise ValueError("VIN debe tener 17 caracteres.")
        if not is_valid_vin(vin_input_norm):
            raise ValueError("VIN inválido. Debe tener 17 caracteres y NO incluir I/O/Q.")

        add_vehicle_item(
            case_id=case_id,
            vin=vin_input_norm,
            brand=brand,
            model=model,
            year=year,
            description=description,
            quantity=int(quantity),
            weight=weight,
            value=value,
            source="vin_photo",
        )

        st.success("Vehículo guardado.")
        st.session_state[vin_decoded_key] = {}
        st.session_state[vin_res_key] = {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "error": ""}
        st.rerun()

    except Exception as e:
        st.error(f"Error guardando vehículo: {type(e).__name__}: {e}")


# ---------------------------
# ARTÍCULO por dictado/manual
# ---------------------------
st.divider()
st.subheader("Agregar artículo (dictado)")

st.caption(
    "Formato sugerido: ref: XXX | marca: YYY | modelo: ZZZ | peso: 3.5 lb | estado: usado | cantidad: 2 | parte_vehiculo: si | vin: 1HG..."
)

# Keys consistentes (para poder escribir en session_state y que sí llene)
art_ref_key = f"art_ref_{case_id}"
art_brand_key = f"art_brand_{case_id}"
art_model_key = f"art_model_{case_id}"
art_weight_key = f"art_weight_{case_id}"
art_value_key = f"art_value_{case_id}"
art_desc_key = f"art_desc_{case_id}"
art_qty_key = f"art_qty_{case_id}"
art_is_part_key = f"art_is_part_{case_id}"
art_parent_vin_txt_key = f"art_parent_vin_txt_{case_id}"
art_parent_vin_sel_key = f"art_parent_vin_sel_{case_id}"

# Inicializar defaults SOLO si no existen
if art_ref_key not in st.session_state:
    st.session_state[art_ref_key] = ""
if art_brand_key not in st.session_state:
    st.session_state[art_brand_key] = ""
if art_model_key not in st.session_state:
    st.session_state[art_model_key] = ""
if art_weight_key not in st.session_state:
    st.session_state[art_weight_key] = ""
if art_value_key not in st.session_state:
    st.session_state[art_value_key] = ""
if art_desc_key not in st.session_state:
    st.session_state[art_desc_key] = ""
if art_qty_key not in st.session_state:
    st.session_state[art_qty_key] = 1
if art_is_part_key not in st.session_state:
    st.session_state[art_is_part_key] = False
if art_parent_vin_txt_key not in st.session_state:
    st.session_state[art_parent_vin_txt_key] = ""

dictation = st.text_area("Dictado (o escribe manual)", height=90, key=f"art_dict_{case_id}")
parsed = _parse_article_dictation(dictation)

# Botón que realmente hace que el dictado “sirva”
apply_dict_btn = st.button("Aplicar dictado a campos", key=f"apply_dict_{case_id}")

if apply_dict_btn:
    st.session_state[art_ref_key] = parsed.get("ref", "") or ""
    st.session_state[art_brand_key] = parsed.get("brand", "") or ""
    st.session_state[art_model_key] = parsed.get("model", "") or ""
    st.session_state[art_weight_key] = parsed.get("weight", "") or ""
    st.session_state[art_value_key] = parsed.get("value", "") or ""
    st.session_state[art_desc_key] = (parsed.get("description", "") or "").strip()
    try:
        st.session_state[art_qty_key] = int(parsed.get("quantity", 1) or 1)
    except Exception:
        st.session_state[art_qty_key] = 1
    st.session_state[art_is_part_key] = bool(parsed.get("is_vehicle_part", False))

    pv = normalize_vin(parsed.get("parent_vin", "") or "")
    if pv:
        st.session_state[art_parent_vin_txt_key] = pv

    st.success("Dictado aplicado a los campos.")

with st.expander("🧪 Debug dictado parseado"):
    st.json(parsed)

art_c1, art_c2, art_c3 = st.columns(3)
with art_c1:
    art_ref = st.text_input("Serie/Referencia", key=art_ref_key)
with art_c2:
    art_brand = st.text_input("Marca", key=art_brand_key)
with art_c3:
    art_model = st.text_input("Modelo", key=art_model_key)

art_c4, art_c5, art_c6 = st.columns(3)
with art_c4:
    art_weight = st.text_input("Peso (lb/kg)", key=art_weight_key)
with art_c5:
    art_condition = st.selectbox("Estado", options=["", "nuevo", "usado"], key=f"art_cond_{case_id}")
with art_c6:
    art_qty = st.number_input("Cantidad", min_value=1, value=int(st.session_state[art_qty_key]), step=1, key=art_qty_key)

is_part = st.checkbox("¿Es parte del vehículo?", key=art_is_part_key)

parent_vin = ""
if is_part:
    vins = []
    if items_df is not None and not items_df.empty and "item_type" in items_df.columns and "unique_key" in items_df.columns:
        vins = items_df[items_df["item_type"] == "vehicle"]["unique_key"].tolist()
        vins = [v for v in vins if v]

    if vins:
        parent_vin = st.selectbox(
            "Selecciona el VIN del vehículo al que pertenece",
            vins,
            key=art_parent_vin_sel_key
        )
    else:
        parent_vin = st.text_input(
            "VIN del vehículo (no hay vehículos registrados aún)",
            key=art_parent_vin_txt_key
        )

art_value = st.text_input("Valor (USD)", key=art_value_key)
art_description = st.text_area("Descripción", height=80, key=art_desc_key)

confirm_article = st.checkbox(
    "✅ Confirmo que la información del artículo es correcta antes de guardar.",
    value=False,
    key=f"art_confirm_{case_id}",
)

if st.button("Guardar artículo", type="primary", disabled=not confirm_article, key=f"save_article_{case_id}"):
    try:
        desc = (art_description or "").strip()

        if is_part:
            pv = normalize_vin(parent_vin)
            if pv and len(pv) == 17 and is_valid_vin(pv):
                desc = f"[PARTE_DE_VEHICULO:{pv}] {desc}".strip()
            else:
                desc = f"[PARTE_DE_VEHICULO] {desc}".strip()

        if art_ref and art_ref not in desc:
            desc = f"{art_ref} | {desc}".strip(" |")

        add_article_item(
            case_id=case_id,
            description=desc,
            brand=art_brand,
            model=art_model,
            quantity=int(art_qty),
            weight=art_weight,
            value=art_value,
            source="voice" if dictation.strip() else "manual",
        )

        st.success("Artículo guardado.")
        st.rerun()

    except Exception as e:
        st.error(f"Error guardando artículo: {type(e).__name__}: {e}")
