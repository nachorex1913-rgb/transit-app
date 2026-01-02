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
    list_documents,
    add_vehicle_item,
    add_article_item,
    add_document,
)
from transit_core.drive_bridge import (
    create_case_folder_via_script,
    upload_file_to_case_folder_via_script,
)
from transit_core.validators import normalize_vin, is_valid_vin
from transit_core.vin_decode import decode_vin

st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")


def _extract_vin_candidates(text: str) -> list[str]:
    if not text:
        return []
    up = re.sub(r"[^A-Z0-9]", "", text.upper())
    cands = re.findall(r"[A-HJ-NPR-Z0-9]{17}", up)
    return list(dict.fromkeys(cands))


def _parse_article_dictation(text: str) -> dict:
    t = (text or "").strip()
    data = {
        "type": "",
        "ref": "",
        "brand": "",
        "model": "",
        "weight": "",
        "condition": "",
        "quantity": 1,
        "is_vehicle_part": False,
        "parent_vin": "",
        "value": "",
    }
    if not t:
        return data

    parts = [p.strip() for p in re.split(r"\||\n|;", t) if p.strip()]
    has_colon = any(":" in p for p in parts)

    aliases = {
        "tipo": "type", "articulo": "type", "artículo": "type", "item": "type", "producto": "type",
        "ref": "ref", "referencia": "ref", "serie": "ref", "serial": "ref",
        "marca": "brand", "brand": "brand",
        "modelo": "model", "model": "model",
        "peso": "weight", "weight": "weight",
        "estado": "condition", "condition": "condition",
        "cantidad": "quantity", "qty": "quantity", "quantity": "quantity",
        "parte_vehiculo": "is_vehicle_part", "partevehiculo": "is_vehicle_part", "vehicle_part": "is_vehicle_part",
        "vin": "parent_vin", "vin_padre": "parent_vin", "parent_vin": "parent_vin",
        "valor": "value", "value": "value",
    }

    def _set(key: str, val: str):
        val = (val or "").strip()
        if key == "quantity":
            try:
                data["quantity"] = int(re.findall(r"\d+", val)[0])
            except Exception:
                data["quantity"] = 1
        elif key == "is_vehicle_part":
            vv = val.lower()
            if vv in ("si", "sí", "yes", "true", "1"):
                data["is_vehicle_part"] = True
            elif vv in ("no", "false", "0"):
                data["is_vehicle_part"] = False
        elif key == "parent_vin":
            data["parent_vin"] = normalize_vin(val)
        else:
            data[key] = val

    if has_colon:
        for p in parts:
            if ":" not in p:
                continue
            k, v = p.split(":", 1)
            k = k.strip().lower()
            v = v.strip()
            key = aliases.get(k)
            if key:
                _set(key, v)
        return data

    tokens = re.split(r"\s+", t.strip())
    i = 0
    current_key = None
    buff = []

    def flush():
        nonlocal current_key, buff
        if not current_key:
            buff = []
            return
        _set(current_key, " ".join(buff).strip())
        buff = []

    while i < len(tokens):
        tok = re.sub(r"[^\wáéíóúüñ_]+", "", tokens[i].lower())
        if tok in aliases:
            flush()
            current_key = aliases[tok]
            buff = []
        else:
            buff.append(tokens[i])
        i += 1
    flush()

    return data


def _build_article_description(art_type, ref, brand, model, weight, condition, quantity, value) -> str:
    parts = []
    if art_type:
        parts.append(str(art_type).strip())
    if ref:
        parts.append(f"Ref {str(ref).strip()}")
    if brand:
        parts.append(f"Marca {str(brand).strip()}")
    if model:
        parts.append(f"Modelo {str(model).strip()}")
    if weight:
        parts.append(f"Peso {str(weight).strip()}")
    if condition:
        parts.append(f"Estado {str(condition).strip()}")
    parts.append(f"Cantidad {int(quantity or 1)}")
    if value:
        parts.append(f"Valor {str(value).strip()}")
    return " | ".join(parts).strip()


def _validate_ready_for_pending(case: dict, items_df) -> tuple[bool, list[str]]:
    errors = []
    if not case.get("case_id"):
        errors.append("No hay case_id.")
    if not case.get("client_id"):
        errors.append("No hay client_id.")
    if not (case.get("case_name") or "").strip():
        errors.append("Falta nombre del trámite (case_name).")

    if items_df is None or items_df.empty:
        errors.append("El trámite no tiene items (vehículos/artículos).")
        return (False, errors)

    # Reglas mínimas operativas (sin inventar)
    veh = items_df[items_df.get("item_type", "") == "vehicle"] if "item_type" in items_df.columns else None
    if veh is None or veh.empty:
        errors.append("Debe existir al menos 1 vehículo.")

    # Vehículos: VIN + marca/modelo/año
    if veh is not None and not veh.empty:
        for _, r in veh.iterrows():
            vin = str(r.get("unique_key", "")).strip()
            if len(normalize_vin(vin)) != 17:
                errors.append(f"Vehículo con VIN inválido: {vin}")
            if not str(r.get("brand", "")).strip():
                errors.append(f"Vehículo {vin}: falta Marca.")
            if not str(r.get("model", "")).strip():
                errors.append(f"Vehículo {vin}: falta Modelo.")
            if not str(r.get("year", "")).strip():
                errors.append(f"Vehículo {vin}: falta Año.")

    return (len(errors) == 0, errors)


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
    st.info(f"Nombre del trámite (obligatorio): **{client_name}**")

    if st.button("Crear trámite", type="primary", key="create_case_btn"):
        try:
            created_case_id = create_case(
                client_id=client_id,
                case_name=client_name,
                origin=origin.strip() or "USA",
                destination=destination.strip(),
                notes=notes.strip(),
                drive_folder_id="",
            )

            st.success(f"✅ Trámite creado: {created_case_id}")
            st.info("Ahora entra a 'Gestionar / Modificar' para crear carpeta Drive, agregar vehículo, artículos y documentos.")
            st.rerun()

        except Exception as e:
            st.error(f"Error creando trámite: {type(e).__name__}: {e}")


# ======================================================
# TAB 2 — GESTIONAR
# ======================================================
with tab_manage:
    st.subheader("Gestionar / Modificar trámite")

    cases_df = list_cases().fillna("")
    if cases_df.empty:
        st.info("No hay trámites aún.")
        st.stop()

    for col in ["case_name", "status"]:
        if col not in cases_df.columns:
            cases_df[col] = ""

    cases_df["status"] = cases_df["status"].astype(str).str.upper().str.strip()
    cases_df.loc[cases_df["status"] == "", "status"] = "BORRADOR"

    edit_locked_cases = st.toggle("Editar trámites PENDIENTE/ENVIADO (requiere código)", value=False, key="toggle_edit_locked")
    authorized = False
    if edit_locked_cases:
        code = st.text_input("Código de autorización", type="password", key="auth_code")
        authorized = (code.strip() == "778899")
        if code and not authorized:
            st.error("Código incorrecto.")
        if authorized:
            st.success("Autorización válida. Edición habilitada.")

    selectable_df = cases_df.copy() if authorized else cases_df[cases_df["status"] == "BORRADOR"].copy()
    if selectable_df.empty:
        st.warning("No hay trámites en BORRADOR para gestionar (o no tienes autorización).")
        st.stop()

    selectable_df["label"] = (
        selectable_df["case_id"].astype(str)
        + " — "
        + selectable_df["case_name"].astype(str)
        + " — ["
        + selectable_df["status"].astype(str)
        + "]"
    )

    selected_label = st.selectbox("Selecciona un trámite", selectable_df["label"].tolist(), key="case_select")
    selected_case_id = selected_label.split(" — ")[0].strip()

    case = get_case(str(selected_case_id))
    if not case:
        st.error("No se pudo cargar el trámite.")
        st.stop()

    case_id = str(case.get("case_id") or "")
    case_name = str(case.get("case_name") or "").strip()
    case_status = str(case.get("status") or "BORRADOR").upper().strip()
    drive_folder_id = str(case.get("drive_folder_id") or "")
    client_id = str(case.get("client_id") or "")

    items_df = list_items(case_id=case_id)
    items_df = items_df.fillna("") if items_df is not None else items_df

    is_locked = (case_status in ("PENDIENTE", "ENVIADO")) and (not authorized)
    if is_locked:
        st.warning("🔒 Trámite PENDIENTE/ENVIADO. Para modificar activa el toggle y usa el código.")

    st.write(f"**Trámite:** {case_id}")
    st.write(f"**Nombre:** {case_name}")
    st.write(f"**Estatus:** {case_status}")
    st.write(f"**Cliente ID:** {client_id}")
    st.write(f"**Drive folder:** {drive_folder_id}")

    # -------- Carpeta Drive
    if not drive_folder_id:
        if st.button("📁 Crear carpeta en Drive", key=f"mk_drive_{case_id}", disabled=is_locked):
            try:
                root_folder_id = st.secrets.get("drive", {}).get("root_folder_id", "").strip()
                if not root_folder_id:
                    raise RuntimeError("Falta secret: drive.root_folder_id")

                folder_name = f"{case_id} - {case_name}".strip()
                res = create_case_folder_via_script(
                    root_folder_id=root_folder_id,
                    case_id=case_id,
                    folder_name=folder_name,
                )
                drive_folder_id = res.get("folder_id", "")
                if not drive_folder_id:
                    raise RuntimeError(f"No se recibió folder_id: {res}")

                update_case_fields(case_id, {"drive_folder_id": drive_folder_id})
                st.success("✅ Carpeta creada y vinculada al trámite.")
                st.rerun()

            except Exception as e:
                st.error(f"Error creando carpeta: {type(e).__name__}: {e}")

    st.divider()

    # -------- Validar y pasar a PENDIENTE (status automático)
    st.subheader("✅ Validación del trámite")
    ok, errs = _validate_ready_for_pending(case, items_df)
    if ok:
        st.success("Checklist OK. Este trámite está completo para pasar a PENDIENTE.")
    else:
        st.warning("Faltan cosas para poder pasar a PENDIENTE:")
        for er in errs[:25]:
            st.write(f"- {er}")

    if st.button("Validar y pasar a PENDIENTE", type="primary", key=f"to_pending_{case_id}", disabled=is_locked or (not ok)):
        try:
            update_case_fields(case_id, {"status": "Pendiente"})
            st.success("✅ Estatus actualizado a PENDIENTE.")
            st.rerun()
        except Exception as e:
            st.error(f"Error actualizando estatus: {type(e).__name__}: {e}")

    st.divider()

    st.subheader("Items registrados")
    if items_df is None or items_df.empty:
        st.info("Aún no hay vehículos ni artículos en este trámite.")
    else:
        st.dataframe(items_df, use_container_width=True)

    # ======================================================
    # VEHÍCULO — SOLO TEXTO
    # ======================================================
    st.divider()
    st.subheader("Agregar vehículo")

    vin_method = st.radio(
        "Método VIN",
        ["Copiar/Pegar", "Dictado"],
        horizontal=True,
        key=f"vin_method_{case_id}",
        disabled=is_locked,
    )

    if vin_method == "Copiar/Pegar":
        vin_text = st.text_input("Pega el VIN aquí", value="", key=f"vin_paste_{case_id}", disabled=is_locked)
    else:
        st.caption("Dicta claramente, con voz fuerte y sin pausas extremas.")
        vin_text = st.text_area("Dictado", height=70, key=f"vin_dict_{case_id}", disabled=is_locked)

    cands = _extract_vin_candidates(vin_text)
    if cands:
        vin_selected = st.selectbox("VIN detectado por el texto", cands, key=f"vin_detected_{case_id}", disabled=is_locked)
        vin_norm = normalize_vin(vin_selected)
    else:
        vin_norm = normalize_vin(vin_text)

    if vin_norm:
        st.write(f"**VIN normalizado:** `{vin_norm}`")

    valid_len = (len(vin_norm) == 17)
    valid_chars = (is_valid_vin(vin_norm) if valid_len else False)

    # session keys
    dec_key = f"vin_decoded_{case_id}"
    st.session_state.setdefault(dec_key, {})
    vin_last_key = f"vin_last_{case_id}"
    st.session_state.setdefault(vin_last_key, "")

    veh_brand_key = f"veh_brand_{case_id}"
    veh_model_key = f"veh_model_{case_id}"
    veh_year_key = f"veh_year_{case_id}"
    veh_weight_key = f"veh_weight_{case_id}"

    # extras
    veh_trim_key = f"veh_trim_{case_id}"
    veh_engine_key = f"veh_engine_{case_id}"
    veh_vtype_key = f"veh_vehicle_type_{case_id}"
    veh_body_key = f"veh_body_class_{case_id}"
    veh_plant_key = f"veh_plant_country_{case_id}"
    veh_gvwr_key = f"veh_gvwr_{case_id}"
    veh_cw_key = f"veh_curb_weight_{case_id}"

    for k in [
        veh_brand_key, veh_model_key, veh_year_key, veh_weight_key,
        veh_trim_key, veh_engine_key, veh_vtype_key, veh_body_key, veh_plant_key, veh_gvwr_key, veh_cw_key
    ]:
        st.session_state.setdefault(k, "")

    if vin_norm and vin_norm != st.session_state[vin_last_key]:
        st.session_state[vin_last_key] = vin_norm
        st.session_state[dec_key] = {}
        for k in [
            veh_brand_key, veh_model_key, veh_year_key,
            veh_trim_key, veh_engine_key, veh_vtype_key, veh_body_key, veh_plant_key, veh_gvwr_key, veh_cw_key
        ]:
            st.session_state[k] = ""

    with st.expander("🧪 Debug VIN (punto exacto de lectura)"):
        st.write("Texto recibido:", vin_text)
        st.write("Candidatos:", cands)
        st.write("vin_norm:", vin_norm)
        st.write("len:", len(vin_norm))
        st.write("is_valid_vin:", valid_chars)

    consult_disabled = is_locked or (not vin_norm) or (not valid_len) or (not valid_chars)

    if st.button("Consultar información del vehículo", key=f"vin_decode_btn_{case_id}", disabled=consult_disabled):
        out = decode_vin(vin_norm) or {}
        st.session_state[dec_key] = out

        if out.get("error"):
            st.error(out.get("error"))
        else:
            # principales
            st.session_state[veh_brand_key] = str(out.get("brand", "") or "")
            st.session_state[veh_model_key] = str(out.get("model", "") or "")
            st.session_state[veh_year_key] = str(out.get("year", "") or "")

            # extras
            st.session_state[veh_trim_key] = str(out.get("trim", "") or "")
            st.session_state[veh_engine_key] = str(out.get("engine", "") or "")
            st.session_state[veh_vtype_key] = str(out.get("vehicle_type", "") or "")
            st.session_state[veh_body_key] = str(out.get("body_class", "") or "")
            st.session_state[veh_plant_key] = str(out.get("plant_country", "") or "")
            st.session_state[veh_gvwr_key] = str(out.get("gvwr", "") or "")
            st.session_state[veh_cw_key] = str(out.get("curb_weight", "") or "")

            st.success("✅ Info consultada. Revisa antes de guardar.")

    decoded = st.session_state.get(dec_key, {}) or {}
    if decoded:
        with st.expander("🧪 Debug decoder (respuesta completa)"):
            st.json(decoded)

    st.subheader("Datos del vehículo")
    st.text_input("Marca", key=veh_brand_key, disabled=is_locked)
    st.text_input("Modelo", key=veh_model_key, disabled=is_locked)
    st.text_input("Año", key=veh_year_key, disabled=is_locked)
    st.text_input("Peso (opcional)", key=veh_weight_key, disabled=is_locked)

    st.subheader("Información técnica (NHTSA)")
    st.text_input("Trim", key=veh_trim_key, disabled=True)
    st.text_input("Engine", key=veh_engine_key, disabled=True)
    st.text_input("Vehicle Type", key=veh_vtype_key, disabled=True)
    st.text_input("Body Class", key=veh_body_key, disabled=True)
    st.text_input("Plant Country", key=veh_plant_key, disabled=True)
    st.text_input("GVWR", key=veh_gvwr_key, disabled=True)
    st.text_input("Curb Weight", key=veh_cw_key, disabled=True)

    confirm_save_vehicle = st.checkbox(
        "✅ Confirmo que VIN + datos del vehículo están listos para guardar",
        value=False,
        key=f"veh_confirm_save_{case_id}",
        disabled=is_locked,
    )

    if st.button("Guardar vehículo", type="primary", disabled=is_locked or (not confirm_save_vehicle), key=f"veh_save_{case_id}"):
        try:
            if len(vin_norm) != 17:
                raise ValueError("VIN debe tener 17 caracteres.")
            if not is_valid_vin(vin_norm):
                raise ValueError("VIN inválido. Debe tener 17 caracteres y NO incluir I/O/Q.")

            add_vehicle_item(
                case_id=case_id,
                vin=vin_norm,
                brand=st.session_state[veh_brand_key],
                model=st.session_state[veh_model_key],
                year=st.session_state[veh_year_key],
                description="",
                weight=st.session_state[veh_weight_key],
                value="0",
                source="vin_text",
                trim=st.session_state[veh_trim_key],
                engine=st.session_state[veh_engine_key],
                vehicle_type=st.session_state[veh_vtype_key],
                body_class=st.session_state[veh_body_key],
                plant_country=st.session_state[veh_plant_key],
                gvwr=st.session_state[veh_gvwr_key],
                curb_weight=st.session_state[veh_cw_key],
            )

            st.success("✅ Vehículo guardado correctamente.")
            st.rerun()

        except Exception as e:
            st.error(f"Error guardando vehículo: {type(e).__name__}: {e}")

    # ======================================================
    # ARTÍCULOS
    # ======================================================
    st.divider()
    st.subheader("Agregar artículo (dictado)")

    st.caption("Ejemplo continuo: tipo lavadora ref 440827 marca Sienna modelo Sleep4415 peso 95 lb estado usado cantidad 1 parte_vehiculo no valor 120")
    st.caption("Ejemplo con ':' : tipo: lavadora | ref: 440827 | marca: Sienna | modelo: Sleep4415 | peso: 95 lb | estado: usado | cantidad: 1 | parte_vehiculo: no | valor: 120")

    dictation = st.text_area("Dictado", height=90, key=f"art_dict_{case_id}", disabled=is_locked)
    parsed = _parse_article_dictation(dictation)

    with st.expander("🧪 Debug dictado parseado"):
        st.json(parsed)

    art_type = st.text_input("Tipo de artículo (lavadora, secadora, caja de herramientas, etc.)", value=parsed.get("type",""), key=f"art_type_{case_id}", disabled=is_locked)
    art_ref = st.text_input("Serie/Referencia", value=parsed.get("ref",""), key=f"art_ref_{case_id}", disabled=is_locked)
    art_brand = st.text_input("Marca", value=parsed.get("brand",""), key=f"art_brand_{case_id}", disabled=is_locked)
    art_model = st.text_input("Modelo", value=parsed.get("model",""), key=f"art_model_{case_id}", disabled=is_locked)
    art_weight = st.text_input("Peso (lb/kg)", value=parsed.get("weight",""), key=f"art_weight_{case_id}", disabled=is_locked)

    # estado automático desde dictado (sin dropdown)
    art_condition = st.text_input("Estado (nuevo/usado)", value=parsed.get("condition",""), key=f"art_cond_{case_id}", disabled=is_locked)

    try:
        qty_default = int(parsed.get("quantity", 1) or 1)
    except Exception:
        qty_default = 1
    art_qty = st.number_input("Cantidad", min_value=1, value=qty_default, step=1, key=f"art_qty_{case_id}", disabled=is_locked)

    is_part = st.checkbox("¿Es parte del vehículo?", value=bool(parsed.get("is_vehicle_part", False)), key=f"art_is_part_{case_id}", disabled=is_locked)

    parent_vin = ""
    if is_part:
        vins = []
        if items_df is not None and not items_df.empty and "item_type" in items_df.columns and "unique_key" in items_df.columns:
            vins = items_df[items_df["item_type"] == "vehicle"]["unique_key"].tolist()
            vins = [v for v in vins if v]
        if vins:
            parent_vin = st.selectbox("VIN del vehículo al que pertenece", vins, key=f"art_parent_vin_sel_{case_id}", disabled=is_locked)
        else:
            parent_vin = st.text_input("VIN del vehículo", value=parsed.get("parent_vin",""), key=f"art_parent_vin_txt_{case_id}", disabled=is_locked)

    art_value = st.text_input("Valor (USD) (opcional)", value=parsed.get("value",""), key=f"art_value_{case_id}", disabled=is_locked)

    auto_desc = _build_article_description(
        art_type, art_ref, art_brand, art_model, art_weight, art_condition, art_qty, art_value
    )

    desc_final = auto_desc
    if is_part:
        pv = normalize_vin(parent_vin)
        if pv and len(pv) == 17 and is_valid_vin(pv):
            desc_final = f"[PARTE_DE_VEHICULO:{pv}] {desc_final}".strip()
        else:
            desc_final = f"[PARTE_DE_VEHICULO] {desc_final}".strip()

    st.text_area("Descripción (automática)", value=desc_final, height=80, key=f"art_desc_auto_{case_id}", disabled=True)

    confirm_article = st.checkbox(
        "✅ Confirmo que la información del artículo es correcta antes de guardar.",
        value=False,
        key=f"art_confirm_{case_id}",
        disabled=is_locked,
    )

    if st.button("Guardar artículo", type="primary", disabled=is_locked or (not confirm_article), key=f"save_article_{case_id}"):
        try:
            add_article_item(
                case_id=case_id,
                description=desc_final,
                brand=art_brand,
                model=art_model,
                quantity=int(art_qty),
                weight=art_weight,
                value=art_value,
                source="voice" if dictation.strip() else "manual",
            )
            st.success("✅ Artículo guardado correctamente.")
            st.rerun()
        except Exception as e:
            st.error(f"Error guardando artículo: {type(e).__name__}: {e}")

    # ======================================================
    # DOCUMENTOS — UN SOLO PUNTO
    # ======================================================
    st.divider()
    st.subheader("📎 Documentos del trámite (cliente / vehículos / artículos)")

    if not drive_folder_id:
        st.warning("Este trámite aún NO tiene carpeta en Drive. Créala arriba.")
    else:
        docs_df = list_documents(case_id).fillna("")
        if docs_df.empty:
            st.info("Aún no hay documentos registrados.")
        else:
            st.dataframe(docs_df, use_container_width=True)

        attach_options = [("Trámite / Cliente (general)", "")]
        if items_df is not None and not items_df.empty:
            if "item_id" in items_df.columns:
                for _, r in items_df.iterrows():
                    item_id = str(r.get("item_id", "")).strip()
                    itype = str(r.get("item_type", "")).strip()
                    ukey = str(r.get("unique_key", "")).strip()
                    attach_options.append((f"{itype.upper()} — {ukey}", item_id))

        attach_label = st.selectbox("¿A qué pertenece?", [x[0] for x in attach_options], key=f"doc_attach_sel_{case_id}", disabled=is_locked)
        attach_item_id = dict(attach_options).get(attach_label, "")

        doc_type = st.selectbox(
            "Tipo de documento",
            ["vin_evidence", "passport", "driver_license", "title", "invoice", "bill_of_sale", "other"],
            key=f"doc_type_{case_id}",
            disabled=is_locked,
        )

        upload = st.file_uploader(
            "Subir documento (pdf/jpg/png)",
            type=["pdf", "jpg", "jpeg", "png"],
            key=f"doc_uploader_{case_id}",
            disabled=is_locked,
        )

        if st.button("Subir documento a Drive", type="primary", key=f"doc_upload_btn_{case_id}", disabled=is_locked or (upload is None)):
            try:
                up = upload_file_to_case_folder_via_script(
                    case_folder_id=drive_folder_id,
                    file_bytes=upload.getvalue(),
                    file_name=upload.name,
                    mime_type=upload.type or "application/octet-stream",
                    subfolder="DOCUMENTOS",
                )
                drive_file_id = up.get("file_id", "")
                if not drive_file_id:
                    raise RuntimeError(f"No se recibió file_id del script: {up}")

                add_document(
                    case_id=case_id,
                    item_id=attach_item_id,
                    doc_type=doc_type,
                    drive_file_id=drive_file_id,
                    file_name=upload.name,
                )

                st.success("✅ Documento subido y registrado.")
                st.rerun()

            except Exception as e:
                st.error(f"Error subiendo documento: {type(e).__name__}: {e}")


# ======================================================
# TAB 3 — LISTADO
# ======================================================
with tab_list:
    st.subheader("Listado de trámites y estatus")
    df = list_cases().fillna("")
    if df.empty:
        st.info("No hay trámites registrados.")
    else:
        for col in ["case_id", "case_name", "status", "origin", "destination", "created_at", "updated_at"]:
            if col not in df.columns:
                df[col] = ""

        df["status"] = df["status"].astype(str).str.upper().str.strip()
        df.loc[df["status"] == "", "status"] = "BORRADOR"

        status_filter = st.selectbox("Filtrar estatus", ["TODOS", "BORRADOR", "PENDIENTE", "ENVIADO"], index=0)
        view = df.copy()
        if status_filter != "TODOS":
            view = view[view["status"] == status_filter]

        cols = [c for c in ["case_id","case_name","status","origin","destination","created_at","updated_at"] if c in view.columns]
        st.dataframe(view[cols], use_container_width=True)
