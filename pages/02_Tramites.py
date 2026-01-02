import re
import hashlib
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
from transit_core.validators import normalize_vin, is_valid_vin
from transit_core.vin_ocr import extract_vin_from_image
from transit_core.vin_decode import decode_vin

st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")

# ======================================================
# Helpers (dictado + dedupe)
# ======================================================
def _norm_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _make_article_fingerprint(
    case_id: str,
    brand: str,
    model: str,
    weight: str,
    condition: str,
    quantity: int,
    parent_vin: str,
    description: str,
    value: str,
) -> str:
    """
    Fingerprint solo para evitar doble click / mismo envío inmediato en UI.
    El bloqueo fuerte contra duplicados se hace en DB (gsheets_db.add_article_item).
    """
    payload = "|".join(
        [
            _norm_text(case_id),
            _norm_text(brand).lower(),
            _norm_text(model).lower(),
            _norm_text(weight).lower(),
            _norm_text(condition).lower(),
            str(int(quantity or 1)),
            _norm_text(parent_vin).upper(),
            _norm_text(description).lower(),
            _norm_text(value).lower(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_article_dictation(text: str) -> dict:
    """
    Soporta:
    - Formato con ":"  -> marca: X | modelo: Y
    - Formato sin ":"  -> marca X modelo Y peso 95 lb cantidad 2 ...
    Separadores: |  ;  saltos de línea
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

    parts = [p.strip() for p in re.split(r"\||\n|;", t) if p.strip()]
    has_colon = any(":" in p for p in parts)

    # -----------------
    # Parseo sin ":" (clave valor)
    # -----------------
    if not has_colon:
        aliases = {
            "ref": "ref", "referencia": "ref", "serie": "ref", "serial": "ref",
            "marca": "brand", "brand": "brand",
            "modelo": "model", "model": "model",
            "peso": "weight", "weight": "weight",
            "estado": "condition", "condition": "condition",
            "cantidad": "quantity", "qty": "quantity", "quantity": "quantity",
            "parte_vehiculo": "is_vehicle_part", "partevehiculo": "is_vehicle_part",
            "parte": "is_vehicle_part", "vehicle_part": "is_vehicle_part",
            "vin": "parent_vin", "vin_padre": "parent_vin", "parent_vin": "parent_vin",
            "descripcion": "description", "description": "description",
            "valor": "value", "value": "value",
        }

        tokens = re.split(r"\s+", t.strip())
        i = 0
        current_key = None
        buff = []

        def flush():
            nonlocal current_key, buff
            if not current_key:
                buff = []
                return
            val = " ".join(buff).strip()
            key = current_key

            if key == "ref":
                data["ref"] = val
            elif key == "brand":
                data["brand"] = val
            elif key == "model":
                data["model"] = val
            elif key == "weight":
                data["weight"] = val
            elif key == "condition":
                data["condition"] = val
            elif key == "quantity":
                try:
                    data["quantity"] = int(re.findall(r"\d+", val)[0])
                except Exception:
                    data["quantity"] = 1
            elif key == "is_vehicle_part":
                data["is_vehicle_part"] = val.lower() in ("si", "sí", "yes", "true", "1")
            elif key == "parent_vin":
                data["parent_vin"] = normalize_vin(val)
            elif key == "description":
                data["description"] = val
            elif key == "value":
                data["value"] = val

            buff = []

        while i < len(tokens):
            tok = tokens[i].strip().lower()
            tok_clean = re.sub(r"[^\wáéíóúüñ_]+", "", tok)
            if tok_clean in aliases:
                flush()
                current_key = aliases[tok_clean]
                buff = []
            else:
                buff.append(tokens[i])
            i += 1
        flush()

        return data

    # -----------------
    # Parseo clásico con ":" por partes
    # -----------------
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

    return data


# ======================================================
# Tabs principales
# ======================================================
tab_create, tab_manage, tab_list = st.tabs(
    ["➕ Crear trámite", "🛠 Gestionar / Modificar", "📋 Listado + Estatus"]
)

# ======================================================
# TAB 1 — CREAR
# ======================================================
with tab_create:
    st.subheader("Crear trámite")

    clients_df = list_clients().fillna("")
    if clients_df.empty:
        st.warning("No hay clientes. Crea uno primero.")
        st.stop()

    clients_df["label"] = clients_df["client_id"].astype(str) + " — " + clients_df["name"].astype(str)

    c1, c2, c3 = st.columns([3, 2, 2])
    with c1:
        selected_label = st.selectbox("Cliente", clients_df["label"].tolist(), key="create_case_client")
        row = clients_df.loc[clients_df["label"] == selected_label].iloc[0]
        client_id = str(row["client_id"])
        client_name = str(row["name"]).strip()

    with c2:
        origin = st.text_input("Origen", value="USA", key="create_case_origin")

    with c3:
        destination = st.text_input("Destino", value="Guatemala", key="create_case_dest")

    notes = st.text_input("Notas (opcional)", value="", key="create_case_notes")

    st.info(f"📌 Nombre visible del trámite (obligatorio): **{client_name}**")

    if st.button("Crear trámite", type="primary", key="create_case_btn"):
        try:
            # 1) Crear caso en Sheets (genera case_id)
            created_case_id = create_case(
                client_id=client_id,
                case_name=client_name,
                origin=origin.strip() or "USA",
                destination=destination.strip(),
                notes=notes.strip(),
                drive_folder_id="",  # se setea después
            )

            # 2) Crear carpeta en Drive (por Apps Script) y actualizar case con drive_folder_id
            root_folder_id = st.secrets["drive"]["root_folder_id"]
            folder_name = f"{created_case_id} - {client_name}".strip()

            res = create_case_folder_via_script(
                root_folder_id=root_folder_id,
                case_id=created_case_id,
                folder_name=folder_name,
            )
            drive_folder_id = res.get("folder_id", "")

            if drive_folder_id:
                update_case_fields(created_case_id, {"drive_folder_id": drive_folder_id})

            st.success(f"✅ Trámite creado: {created_case_id}")
            st.info(f"📁 Carpeta: {folder_name}")
            st.rerun()

        except Exception as e:
            st.error(f"Error creando trámite: {type(e).__name__}: {e}")


# ======================================================
# TAB 2 — GESTIONAR / MODIFICAR
# ======================================================
with tab_manage:
    st.subheader("Gestionar / Modificar trámite")

    cases_df = list_cases().fillna("")
    if cases_df.empty:
        st.info("No hay trámites aún.")
        st.stop()

    # Etiqueta: case_id — case_name — status
    if "case_name" not in cases_df.columns:
        cases_df["case_name"] = ""
    if "status" not in cases_df.columns:
        cases_df["status"] = "BORRADOR"

    cases_df["label"] = (
        cases_df["case_id"].astype(str)
        + " — "
        + cases_df["case_name"].astype(str)
        + " — ["
        + cases_df["status"].astype(str)
        + "]"
    )

    selected_label = st.selectbox("Selecciona un trámite", cases_df["label"].tolist(), key="case_select")
    selected_case_id = selected_label.split(" — ")[0].strip()

    case = get_case(str(selected_case_id))
    if not case:
        st.error("No se pudo cargar el trámite.")
        st.stop()

    case_id = str(case.get("case_id") or "")
    case_name = str(case.get("case_name") or "")
    case_status = str(case.get("status") or "BORRADOR").upper().strip()

    items_df = list_items(case_id=case_id)
    items_df = items_df.fillna("") if items_df is not None else items_df

    # Header info + status control
    top1, top2, top3, top4 = st.columns([3, 2, 2, 3])
    with top1:
        st.write(f"**Trámite:** {case_id}")
        st.write(f"**Nombre (cliente):** {case_name}")
    with top2:
        st.write(f"**Cliente ID:** {case.get('client_id','')}")
    with top3:
        st.write(f"**Drive folder:** {case.get('drive_folder_id','')}")
    with top4:
        st.write("")

    st.divider()

    # Status update
    status_options = ["BORRADOR", "PENDIENTE", "ENVIADO"]
    try:
        status_index = status_options.index(case_status)
    except Exception:
        status_index = 0

    s1, s2 = st.columns([2, 4])
    with s1:
        new_status = st.selectbox("Estatus", status_options, index=status_index, key=f"status_sel_{case_id}")
    with s2:
        if st.button("Actualizar estatus", key=f"status_update_{case_id}"):
            try:
                update_case_fields(case_id, {"status": new_status})
                st.success("✅ Estatus actualizado.")
                st.rerun()
            except Exception as e:
                st.error(f"Error actualizando estatus: {type(e).__name__}: {e}")

    is_locked = (new_status == "ENVIADO") or (case_status == "ENVIADO")
    if is_locked:
        st.warning("🔒 Este trámite está en **ENVIADO**. Edición bloqueada (solo lectura).")

    st.subheader("Items registrados")
    if items_df is None or items_df.empty:
        st.info("Aún no hay vehículos ni artículos en este trámite.")
    else:
        st.dataframe(items_df, use_container_width=True)

    # ======================================================
    # VEHÍCULO por foto VIN (mismo flujo, solo bloquea si ENVIADO)
    # ======================================================
    st.divider()
    st.subheader("Agregar vehículo (VIN por foto)")

    vin_image = st.file_uploader(
        "Sube foto del VIN (desde cámara)",
        type=["jpg", "jpeg", "png"],
        key=f"vin_uploader_{case_id}",
        disabled=is_locked,
    )

    extract_btn = st.button("Extraer VIN de la foto", key=f"extract_vin_btn_{case_id}", disabled=is_locked)

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

    if extract_btn and not is_locked:
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
            disabled=is_locked,
        )
    else:
        vin_detected = res.get("vin", "") or ""

    vin_input = st.text_input(
        "VIN detectado (puedes corregirlo)",
        value=vin_detected,
        key=f"vin_input_{case_id}",
        disabled=is_locked,
    )

    vin_input_norm = normalize_vin(vin_input)

    if vin_input_norm and vin_input_norm != st.session_state.get(vin_last_key, ""):
        st.session_state[vin_last_key] = vin_input_norm
        st.session_state[vin_decoded_key] = {}

    decode_btn = st.button(
        "Decodificar VIN",
        key=f"decode_btn_{case_id}",
        disabled=is_locked or (not vin_input_norm or len(vin_input_norm) != 17),
    )

    decoded = st.session_state.get(vin_decoded_key, {}) or {}

    if decode_btn and not is_locked:
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

            if (
                st.session_state[veh_brand_key].strip()
                or st.session_state[veh_model_key].strip()
                or st.session_state[veh_year_key].strip()
            ):
                st.success("VIN decodificado correctamente.")
            else:
                st.warning("Se consultó el decoder pero no devolvió datos útiles. Ingresa manual.")

    st.write(f"**Confianza OCR:** {conf:.2f}")
    if vin_input_norm and len(vin_input_norm) == 17 and not is_valid_vin(vin_input_norm):
        st.warning("VIN inválido (contiene I/O/Q o caracteres no permitidos). Verifica antes de guardar.")

    st.session_state.setdefault(veh_brand_key, "")
    st.session_state.setdefault(veh_model_key, "")
    st.session_state.setdefault(veh_year_key, "")

    veh_c1, veh_c2, veh_c3 = st.columns(3)
    with veh_c1:
        brand = st.text_input("Marca", key=veh_brand_key, disabled=is_locked)
    with veh_c2:
        model = st.text_input("Modelo", key=veh_model_key, disabled=is_locked)
    with veh_c3:
        year = st.text_input("Año", key=veh_year_key, disabled=is_locked)

    veh_c4, veh_c5, veh_c6 = st.columns(3)
    with veh_c4:
        quantity = st.number_input("Cantidad", min_value=1, value=1, step=1, key=f"veh_qty_{case_id}", disabled=is_locked)
    with veh_c5:
        weight = st.text_input("Peso (lb/kg)", value="", key=f"veh_weight_{case_id}", disabled=is_locked)
    with veh_c6:
        value = st.text_input("Valor (USD)", value="", key=f"veh_value_{case_id}", disabled=is_locked)

    description = st.text_area("Descripción (opcional)", value="", height=80, key=f"veh_desc_{case_id}", disabled=is_locked)

    confirm_vehicle = st.checkbox(
        "✅ Confirmo que el VIN y la información son correctos antes de guardar.",
        value=False,
        key=f"veh_confirm_{case_id}",
        disabled=is_locked,
    )

    if st.button(
        "Guardar vehículo",
        type="primary",
        disabled=is_locked or (not confirm_vehicle),
        key=f"save_vehicle_{case_id}",
    ):
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

            st.success("✅ Vehículo guardado.")
            st.session_state[vin_decoded_key] = {}
            st.session_state[vin_res_key] = {"vin": "", "confidence": 0.0, "raw_text": "", "candidates": [], "error": ""}
            st.rerun()

        except Exception as e:
            st.error(f"Error guardando vehículo: {type(e).__name__}: {e}")

    # ======================================================
    # ARTÍCULO por dictado/manual (descripción MANUAL + parent_vin separado)
    # ======================================================
    st.divider()
    st.subheader("Agregar artículo (dictado / manual)")

    # Mensaje persistente de guardado
    last_msg_key = f"art_last_save_msg_{case_id}"
    if st.session_state.get(last_msg_key):
        st.success(st.session_state[last_msg_key])

    st.caption(
        "Formato sugerido (con ':'): ref: 440827 | marca: Sienna | modelo: Sleep4415 | peso: 95 lb | estado: usado | cantidad: 1 | parte_vehiculo: no | valor: 120"
    )
    st.caption(
        "Formato continuo (sin ':'): ref 440827 marca Sienna modelo Sleep4415 peso 95 lb estado usado cantidad 1 parte_vehiculo no valor 120"
    )
    st.caption("📌 La **descripción** del artículo es **manual** (la escribes tú). No se arma automáticamente.")

    # Keys por trámite
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
    art_last_fpr_key = f"art_last_fingerprint_{case_id}"

    st.session_state.setdefault(art_ref_key, "")
    st.session_state.setdefault(art_brand_key, "")
    st.session_state.setdefault(art_model_key, "")
    st.session_state.setdefault(art_weight_key, "")
    st.session_state.setdefault(art_value_key, "")
    st.session_state.setdefault(art_desc_key, "")
    st.session_state.setdefault(art_qty_key, 1)
    st.session_state.setdefault(art_is_part_key, False)
    st.session_state.setdefault(art_parent_vin_txt_key, "")
    st.session_state.setdefault(art_last_fpr_key, "")

    dictation = st.text_area("Dictado (o escribe manual)", height=90, key=f"art_dict_{case_id}", disabled=is_locked)
    parsed = _parse_article_dictation(dictation)

    apply_dict_btn = st.button("Aplicar dictado a campos", key=f"apply_dict_{case_id}", disabled=is_locked)

    if apply_dict_btn and not is_locked:
        st.session_state[art_ref_key] = parsed.get("ref", "") or ""
        st.session_state[art_brand_key] = parsed.get("brand", "") or ""
        st.session_state[art_model_key] = parsed.get("model", "") or ""
        st.session_state[art_weight_key] = parsed.get("weight", "") or ""
        st.session_state[art_value_key] = parsed.get("value", "") or ""
        # OJO: descripción manual. Si dictan "descripcion:" la ponemos, si no, no inventamos.
        st.session_state[art_desc_key] = (parsed.get("description", "") or st.session_state.get(art_desc_key, "")).strip()

        try:
            st.session_state[art_qty_key] = int(parsed.get("quantity", 1) or 1)
        except Exception:
            st.session_state[art_qty_key] = 1

        st.session_state[art_is_part_key] = bool(parsed.get("is_vehicle_part", False))

        pv = normalize_vin(parsed.get("parent_vin", "") or "")
        if pv:
            st.session_state[art_parent_vin_txt_key] = pv

        st.success("✅ Dictado aplicado a los campos.")

    with st.expander("🧪 Debug dictado parseado"):
        st.json(parsed)

    art_c1, art_c2, art_c3 = st.columns(3)
    with art_c1:
        art_ref = st.text_input("Serie/Referencia (opcional)", key=art_ref_key, disabled=is_locked)
    with art_c2:
        art_brand = st.text_input("Marca", key=art_brand_key, disabled=is_locked)
    with art_c3:
        art_model = st.text_input("Modelo", key=art_model_key, disabled=is_locked)

    art_c4, art_c5, art_c6 = st.columns(3)
    with art_c4:
        art_weight = st.text_input("Peso (lb/kg)", key=art_weight_key, disabled=is_locked)
    with art_c5:
        art_condition = st.selectbox("Estado", options=["", "nuevo", "usado"], key=f"art_cond_{case_id}", disabled=is_locked)
    with art_c6:
        art_qty = st.number_input(
            "Cantidad",
            min_value=1,
            value=int(st.session_state[art_qty_key]),
            step=1,
            key=art_qty_key,
            disabled=is_locked,
        )

    is_part = st.checkbox("¿Es parte del vehículo?", key=art_is_part_key, disabled=is_locked)

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
                key=art_parent_vin_sel_key,
                disabled=is_locked,
            )
        else:
            parent_vin = st.text_input(
                "VIN del vehículo (no hay vehículos registrados aún)",
                key=art_parent_vin_txt_key,
                disabled=is_locked,
            )
    else:
        parent_vin = ""

    art_value = st.text_input("Valor (USD) (opcional)", key=art_value_key, disabled=is_locked)

    # Descripción manual (requerida en práctica)
    art_description = st.text_area(
        "Descripción (MANUAL) — Ej: Lavadora ref XXXX, marca YYY, modelo ZZZ, peso ..., estado ...",
        height=90,
        key=art_desc_key,
        disabled=is_locked,
    )

    # Validación suave: si ref no está en descripción, avisar (pero no auto-modificar)
    if art_ref and art_ref.strip() and art_description and art_ref.strip() not in art_description:
        st.info("ℹ️ Nota: La referencia no aparece en la descripción. Si la necesitas en el PDF, inclúyela manualmente.")

    confirm_article = st.checkbox(
        "✅ Confirmo que la información del artículo es correcta antes de guardar.",
        value=False,
        key=f"art_confirm_{case_id}",
        disabled=is_locked,
    )

    if st.button(
        "Guardar artículo",
        type="primary",
        disabled=is_locked or (not confirm_article),
        key=f"save_article_{case_id}",
    ):
        try:
            desc = (art_description or "").strip()
            if not desc:
                raise ValueError("La descripción es obligatoria (manual).")

            pv_norm = normalize_vin(parent_vin) if is_part else ""
            if pv_norm and (len(pv_norm) != 17 or not is_valid_vin(pv_norm)):
                raise ValueError("El VIN seleccionado para 'parte de vehículo' es inválido.")

            # Fingerprint UI anti doble-click inmediato
            fpr = _make_article_fingerprint(
                case_id=case_id,
                brand=art_brand,
                model=art_model,
                weight=art_weight,
                condition=art_condition,
                quantity=int(art_qty),
                parent_vin=pv_norm,
                description=desc,
                value=art_value,
            )

            if st.session_state.get(art_last_fpr_key, "") == fpr:
                st.warning("Este artículo ya se guardó (misma captura). No se guardó de nuevo.")
                st.stop()

            # Guardar (DB aplica anti-duplicados real)
            add_article_item(
                case_id=case_id,
                description=desc,
                brand=art_brand,
                model=art_model,
                quantity=int(art_qty),
                weight=art_weight,
                value=art_value,
                parent_vin=pv_norm,  # ✅ columna real en items
                source="voice" if dictation.strip() else "manual",
            )

            st.session_state[art_last_fpr_key] = fpr
            st.session_state[last_msg_key] = "✅ Artículo guardado correctamente."
            st.success(st.session_state[last_msg_key])
            st.toast("Artículo agregado al trámite", icon="✅")
            st.rerun()

        except Exception as e:
            st.error(f"Error guardando artículo: {type(e).__name__}: {e}")


# ======================================================
# TAB 3 — LISTADO + ESTATUS
# ======================================================
with tab_list:
    st.subheader("Listado de trámites y estatus")

    df = list_cases().fillna("")
    if df.empty:
        st.info("No hay trámites registrados.")
    else:
        # Asegurar columnas
        if "case_name" not in df.columns:
            df["case_name"] = ""
        if "status" not in df.columns:
            df["status"] = "BORRADOR"

        # Filtro por estatus
        f1, f2 = st.columns([2, 6])
        with f1:
            status_filter = st.selectbox("Filtrar estatus", ["TODOS", "BORRADOR", "PENDIENTE", "ENVIADO"], index=0)

        view = df.copy()
        view["status"] = view["status"].astype(str).str.upper().str.strip()

        if status_filter != "TODOS":
            view = view[view["status"] == status_filter]

        cols = []
        for c in ["case_id", "case_name", "status", "origin", "destination", "created_at", "updated_at", "drive_folder_id"]:
            if c in view.columns:
                cols.append(c)

        st.dataframe(view[cols], use_container_width=True)
