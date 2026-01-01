import re
import streamlit as st
from datetime import datetime

from transit_core.gsheets_db import (
    list_clients,
    list_cases,
    get_case,
    create_case,
    update_case_fields,
    list_items,
    add_vehicle_item,
    add_article_item,
)
from transit_core.drive_bridge import create_case_folder_via_script
from transit_core.ids import next_case_id
from transit_core.validators import normalize_vin, is_valid_vin

# ✅ IMPORTS (ajusta si tus módulos tienen otros nombres)
# Si todavía no tienes estas funciones con este nombre, deja el fallback de abajo.
try:
    from transit_core.vin_ocr import extract_vin_from_image
except Exception:
    extract_vin_from_image = None

try:
    from transit_core.vin_decode import decode_vin
except Exception:
    decode_vin = None


st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")


def _now_iso_utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _parse_article_dictation(text: str) -> dict:
    """
    Dictado esperado (flexible):
      ref: 8891-AX | marca: Milwaukee | modelo: M18 | peso: 3.5 lb | estado: usado | cantidad: 2 | parte_vehiculo: si | vin: 1HG...
    """
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

    # Normaliza separadores
    parts = [p.strip() for p in re.split(r"\||\n|;", t) if p.strip()]

    # Si el usuario solo dicta una descripción libre:
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

    # Si no dictó description, construye una con ref/marca/modelo/estado
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

c1, c2, c3 = st.columns([2, 2, 3])

with c1:
    clients_df["label"] = clients_df["client_id"].astype(str) + " — " + clients_df["name"].astype(str)
    selected_label = st.selectbox("Cliente", clients_df["label"].tolist())
    row = clients_df.loc[clients_df["label"] == selected_label].iloc[0]
    client_id = str(row["client_id"])
    client_name = str(row["name"]).strip()

with c2:
    origin = st.text_input("Origen", value="USA")
    destination = st.text_input("Destino", value="Guatemala")

with c3:
    notes = st.text_input("Notas (opcional)", value="")

create_btn = st.button("Crear trámite", type="primary")

if create_btn:
    try:
        cases_df = list_cases().fillna("")
        existing_ids = cases_df["case_id"].tolist() if "case_id" in cases_df.columns else []
        year = datetime.now().year
        case_id = next_case_id(existing_ids, year=year)

        root_folder_id = st.secrets["drive"]["root_folder_id"]
        folder_name = f"{case_id} - {client_name}".strip()

        res = create_case_folder_via_script(
            root_folder_id=root_folder_id,
            case_id=case_id,
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
        st.info(f"Carpeta Drive: {folder_name}")
        st.rerun()

    except Exception as e:
        st.error(f"Error creando trámite: {type(e).__name__}: {e}")

st.divider()

# ---------------------------
# Seleccionar trámite existente
# ---------------------------
st.subheader("Gestionar ítems del trámite")

cases_df = list_cases().fillna("")
if cases_df.empty:
    st.info("No hay trámites aún.")
    st.stop()

cases_df["label"] = cases_df["case_id"].astype(str) + " — " + cases_df.get("destination", "").astype(str) + " — " + cases_df.get("status", "").astype(str)
selected_case_id = st.selectbox("Selecciona un trámite", cases_df["case_id"].tolist())

case = get_case(str(selected_case_id))
if not case:
    st.error("No se pudo cargar el trámite.")
    st.stop()

st.write(f"**Trámite:** {case.get('case_id','')}")
st.write(f"**Cliente ID:** {case.get('client_id','')}")
st.write(f"**Drive folder:** {case.get('drive_folder_id','')}")

items_df = list_items(case_id=case.get("case_id"))
items_df = items_df.fillna("") if items_df is not None else items_df

st.divider()
st.subheader("Items registrados")
if items_df is None or items_df.empty:
    st.info("Aún no hay vehículos ni artículos en este trámite.")
else:
    st.dataframe(items_df, use_container_width=True)

# ---------------------------
# Agregar VEHÍCULO (foto VIN → validar → confirmar → guardar)
# ---------------------------
st.divider()
st.subheader("Agregar vehículo (VIN por foto)")

left, right = st.columns([1, 1])

with left:
    vin_image = st.file_uploader("Sube foto del VIN (desde cámara)", type=["jpg", "jpeg", "png"])

    ocr_btn = st.button("Extraer VIN de la foto", type="secondary", disabled=(vin_image is None))

with right:
    st.caption("Flujo: foto → OCR → confirmar VIN → (opcional) decode → confirmar datos → guardar")
    st.caption("Regla: el VIN NO se puede repetir globalmente.")

if "vin_ocr_result" not in st.session_state:
    st.session_state["vin_ocr_result"] = {"vin": "", "confidence": 0.0, "raw_text": ""}

if ocr_btn and vin_image is not None:
    if extract_vin_from_image is None:
        st.error("No se pudo importar extract_vin_from_image desde transit_core.vin_ocr. Pega tu vin_ocr.py para conectarlo.")
    else:
        try:
            res = extract_vin_from_image(vin_image.getvalue())
            vin = normalize_vin(res.get("vin", ""))
            st.session_state["vin_ocr_result"] = {
                "vin": vin,
                "confidence": float(res.get("confidence", 0.0) or 0.0),
                "raw_text": str(res.get("raw_text", "") or ""),
            }
        except Exception as e:
            st.error(f"OCR error: {type(e).__name__}: {e}")

res = extract_vin_from_image(vin_image.getvalue())

with st.expander("🧪 Debug OCR (texto leído por Tesseract)"):
    st.text(res.get("raw_text", ""))
    st.write("Candidatos VIN:", res.get("candidates", []))


vin_guess = st.session_state["vin_ocr_result"]["vin"]
conf = st.session_state["vin_ocr_result"]["confidence"]

vin_input = st.text_input("VIN detectado (puedes corregirlo)", value=vin_guess)
vin_input_norm = normalize_vin(vin_input)

decode_btn = st.button("Decodificar VIN", type="secondary", disabled=(not vin_input_norm or not is_valid_vin(vin_input_norm)))

decoded = {}
if decode_btn:
    if decode_vin is None:
        st.error("No se pudo importar decode_vin desde transit_core.vin_decode. Pega tu vin_decode.py para conectarlo.")
    else:
        try:
            decoded = decode_vin(vin_input_norm) or {}
            st.session_state["vin_decoded"] = decoded
        except Exception as e:
            st.error(f"Decode error: {type(e).__name__}: {e}")

decoded = st.session_state.get("vin_decoded", {}) or {}

st.write(f"**Confianza OCR:** {conf:.2f}")

# Form editable
veh_c1, veh_c2, veh_c3 = st.columns(3)
with veh_c1:
    brand = st.text_input("Marca", value=str(decoded.get("brand", "")))
with veh_c2:
    model = st.text_input("Modelo", value=str(decoded.get("model", "")))
with veh_c3:
    year = st.text_input("Año", value=str(decoded.get("year", "")))

veh_c4, veh_c5, veh_c6 = st.columns(3)
with veh_c4:
    quantity = st.number_input("Cantidad", min_value=1, value=1, step=1)
with veh_c5:
    weight = st.text_input("Peso (lb/kg)", value="")
with veh_c6:
    value = st.text_input("Valor (USD)", value="")

description = st.text_area("Descripción (opcional)", value="", height=80)

confirm_vehicle = st.checkbox("✅ Confirmo que el VIN y la información son correctos antes de guardar.", value=False)

save_vehicle_btn = st.button("Guardar vehículo", type="primary", disabled=not confirm_vehicle)

if save_vehicle_btn:
    try:
        if not is_valid_vin(vin_input_norm):
            raise ValueError("VIN inválido. Debe tener 17 caracteres y no incluir I/O/Q.")
        # add_vehicle_item ya valida duplicado global con _vin_exists_global
        add_vehicle_item(
            case_id=case["case_id"],
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
        st.session_state["vin_decoded"] = {}
        st.session_state["vin_ocr_result"] = {"vin": "", "confidence": 0.0, "raw_text": ""}
        st.rerun()
    except Exception as e:
        st.error(f"Error guardando vehículo: {type(e).__name__}: {e}")

# ---------------------------
# Agregar ARTÍCULO (dictado / manual) + validación + “parte del vehículo”
# ---------------------------
st.divider()
st.subheader("Agregar artículo (dictado)")

st.caption(
    "Usa el micrófono del teclado del celular para dictar aquí. "
    "Formato sugerido: ref: XXX | marca: YYY | modelo: ZZZ | peso: 3.5 lb | estado: usado | cantidad: 2 | parte_vehiculo: si | vin: 1HG..."
)

dictation = st.text_area("Dictado (o escribe manual)", height=90)
parsed = _parse_article_dictation(dictation)

art_c1, art_c2, art_c3 = st.columns(3)
with art_c1:
    art_ref = st.text_input("Serie/Referencia", value=parsed.get("ref", ""))
with art_c2:
    art_brand = st.text_input("Marca", value=parsed.get("brand", ""))
with art_c3:
    art_model = st.text_input("Modelo", value=parsed.get("model", ""))

art_c4, art_c5, art_c6 = st.columns(3)
with art_c4:
    art_weight = st.text_input("Peso (lb/kg)", value=parsed.get("weight", ""))
with art_c5:
    art_condition = st.selectbox("Estado", options=["", "nuevo", "usado"], index=0 if not parsed.get("condition") else (1 if "nue" in parsed.get("condition","").lower() else 2))
with art_c6:
    art_qty = st.number_input("Cantidad", min_value=1, value=int(parsed.get("quantity", 1) or 1), step=1)

is_part = st.checkbox("¿Es parte del vehículo?", value=bool(parsed.get("is_vehicle_part", False)))

parent_vin = ""
if is_part:
    # Sugerimos elegir uno de los VIN ya registrados en este trámite
    if items_df is not None and not items_df.empty and "item_type" in items_df.columns:
        vins = items_df[items_df["item_type"] == "vehicle"]["unique_key"].tolist() if "unique_key" in items_df.columns else []
        vins = [v for v in vins if v]
    else:
        vins = []

    if vins:
        parent_vin = st.selectbox("Selecciona el VIN del vehículo al que pertenece", vins)
    else:
        parent_vin = st.text_input("VIN del vehículo (no hay vehículos registrados aún)", value=parsed.get("parent_vin",""))

art_value = st.text_input("Valor (USD)", value=parsed.get("value",""))
art_desc_default = parsed.get("description","").strip()
art_description = st.text_area("Descripción", value=art_desc_default, height=80)

confirm_article = st.checkbox("✅ Confirmo que la información del artículo es correcta antes de guardar.", value=False)
save_article_btn = st.button("Guardar artículo", type="primary", disabled=not confirm_article)

if save_article_btn:
    try:
        # Si es parte del vehículo y nos dieron VIN, lo añadimos en la descripción por ahora (sin cambiar DB)
        # Luego, si quieres, agregamos columnas parent_vin / is_vehicle_part en items.
        desc = art_description.strip()
        if is_part:
            pv = normalize_vin(parent_vin)
            if pv and is_valid_vin(pv):
                desc = f"[PARTE_DE_VEHICULO:{pv}] {desc}".strip()
            else:
                # Permitimos guardar pero advertimos
                desc = f"[PARTE_DE_VEHICULO] {desc}".strip()

        # Incluimos referencia en la descripción si no está
        if art_ref and art_ref not in desc:
            desc = f"{art_ref} | {desc}".strip(" |")

        add_article_item(
            case_id=case["case_id"],
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
