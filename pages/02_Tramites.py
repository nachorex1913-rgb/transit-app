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


# -----------------------------
# Helpers
# -----------------------------
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


# -----------------------------
# Tabs
# -----------------------------
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
    st.info(f"📌 Nombre del trámite (obligatorio): **{client_name}**")

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

    if not drive_folder_id:
        if st.button("📁 Crear carpeta en Drive ahora", key=f"mk_drive_{case_id}", disabled=is_locked):
            try:
                root_folder_id = st.secrets["drive"]["root_folder_id"]
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

    status_options = ["BORRADOR", "PENDIENTE", "ENVIADO"]
    idx = status_options.index(case_status) if case_status in status_options else 0
    new_status = st.selectbox("Estatus", status_options, index=idx, key=f"status_sel_{case_id}", disabled=is_locked)
    if st.button("Actualizar estatus", key=f"status_update_{case_id}", disabled=is_locked):
        try:
            update_case_fields(case_id, {"status": new_status})
            st.success("✅ Estatus actualizado.")
            st.rerun()
        except Exception as e:
            st.error(f"Error actualizando estatus: {type(e).__name__}: {e}")

    st.subheader("Items registrados")
    if items_df is None or items_df.empty:
        st.info("Aún no hay vehículos ni artículos en este trámite.")
    else:
        st.dataframe(items_df, use_container_width=True)

    # ======================================================
    # VEHÍCULO — SOLO TEXTO (dictado o pegar)
    # ======================================================
    st.divider()
    st.subheader("Agregar vehículo (VIN por dictado o copiar/pegar)")

    vin_method = st.radio(
        "Método VIN",
        ["⌨️ Copiar/Pegar", "🎙 Dictado (texto)"],
        horizontal=True,
        key=f"vin_method_{case_id}",
        disabled=is_locked,
    )

    if vin_method == "⌨️ Copiar/Pegar":
        vin_text = st.text_input("Pega el VIN aquí", value="", key=f"vin_paste_{case_id}", disabled=is_locked)
    else:
        vin_text = st.text_area("Dicta (puede venir texto con VIN dentro)", height=70, key=f"vin_dict_{case_id}", disabled=is_locked)

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

    # keys para fijar valores (SOLUCIÓN del bug)
    veh_brand_key = f"veh_brand_{case_id}"
    veh_model_key = f"veh_model_{case_id}"
    veh_year_key = f"veh_year_{case_id}"
    veh_weight_key = f"veh_weight_{case_id}"

    st.session_state.setdefault(veh_brand_key, "")
    st.session_state.setdefault(veh_model_key, "")
    st.session_state.setdefault(veh_year_key, "")
    st.session_state.setdefault(veh_weight_key, "")

    # cuando cambia VIN => limpiar decoded + campos
    vin_last_key = f"vin_last_{case_id}"
    st.session_state.setdefault(vin_last_key, "")
    if vin_norm and vin_norm != st.session_state[vin_last_key]:
        st.session_state[vin_last_key] = vin_norm
        st.session_state[veh_brand_key] = ""
        st.session_state[veh_model_key] = ""
        st.session_state[veh_year_key] = ""
        # NO tocamos peso manual
        st.session_state[f"vin_decoded_{case_id}"] = {}

    with st.expander("🧪 Debug VIN (punto exacto de lectura)"):
        st.write("Texto recibido:", vin_text)
        st.write("Candidatos:", cands)
        st.write("vin_norm:", vin_norm)
        st.write("len:", len(vin_norm))
        st.write("is_valid_vin:", valid_chars)

    if vin_norm and valid_len and not valid_chars:
        st.warning("VIN inválido (contiene I/O/Q o caracteres no permitidos).")

    dec_key = f"vin_decoded_{case_id}"
    st.session_state.setdefault(dec_key, {})

    consult_disabled = is_locked or (not vin_norm) or (not valid_len) or (not valid_chars)

    if st.button("Consultar información del vehículo", key=f"vin_decode_btn_{case_id}", disabled=consult_disabled):
        out = decode_vin(vin_norm) or {}
        st.session_state[dec_key] = out

        if out.get("error"):
            st.error(out.get("error"))
        else:
            # ✅ AQUÍ está el fix: escribir en session_state para que los inputs se llenen
            st.session_state[veh_brand_key] = str(out.get("brand", "") or "")
            st.session_state[veh_model_key] = str(out.get("model", "") or "")
            st.session_state[veh_year_key] = str(out.get("year", "") or "")

            # Si NHTSA trae peso, lo ponemos (si el usuario no escribió nada aún)
            cw = str(out.get("curb_weight", "") or "").strip()
            if cw and not str(st.session_state.get(veh_weight_key, "")).strip():
                st.session_state[veh_weight_key] = cw

            # Mensaje correcto según data
            has_any = (
                st.session_state[veh_brand_key].strip()
                or st.session_state[veh_model_key].strip()
                or st.session_state[veh_year_key].strip()
            )
            if has_any:
                st.success("✅ Info consultada. Revisa antes de guardar.")
            else:
                st.warning("VIN válido, pero NHTSA no devolvió Make/Model/Year. Revisa el Debug del decoder.")

    decoded = st.session_state.get(dec_key, {}) or {}
    if decoded:
        with st.expander("🧪 Debug decoder (respuesta completa)"):
            st.json(decoded)

    st.subheader("Datos del vehículo")
    st.text_input("Marca", key=veh_brand_key, disabled=is_locked)
    st.text_input("Modelo", key=veh_model_key, disabled=is_locked)
    st.text_input("Año", key=veh_year_key, disabled=is_locked)
    st.text_input("Peso (opcional)", key=veh_weight_key, disabled=is_locked)

    evidence = st.file_uploader(
        "📎 Evidencia VIN (opcional: foto/pdf, NO OCR)",
        type=["jpg", "jpeg", "png", "pdf"],
        key=f"vin_evidence_{case_id}",
        disabled=is_locked,
    )

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

            item_id = add_vehicle_item(
                case_id=case_id,
                vin=vin_norm,
                brand=st.session_state[veh_brand_key],
                model=st.session_state[veh_model_key],
                year=st.session_state[veh_year_key],
                description="",
                quantity=1,
                weight=st.session_state[veh_weight_key],
                value="0",
                source="vin_text",
            )

            if evidence is not None:
                if not drive_folder_id:
                    raise RuntimeError("Este trámite aún no tiene carpeta en Drive. Crea la carpeta primero.")

                up = upload_file_to_case_folder_via_script(
                    case_folder_id=drive_folder_id,
                    file_bytes=evidence.getvalue(),
                    file_name=evidence.name,
                    mime_type=evidence.type or "application/octet-stream",
                    subfolder="VIN_EVIDENCIA",
                )
                drive_file_id = up.get("file_id", "")
                if not drive_file_id:
                    raise RuntimeError(f"No se recibió file_id del script: {up}")

                add_document(
                    case_id=case_id,
                    item_id=item_id,
                    doc_type="vin_evidence",
                    drive_file_id=drive_file_id,
                    file_name=evidence.name,
                )

            st.success("✅ Vehículo guardado correctamente.")
            st.rerun()

        except Exception as e:
            st.error(f"Error guardando vehículo: {type(e).__name__}: {e}")

    # ======================================================
    # DOCUMENTOS — desde Trámites
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

        attach_options = [("Trámite (general / cliente)", "")]
        if items_df is not None and not items_df.empty:
            for _, r in items_df.iterrows():
                item_id = str(r.get("item_id", "")).strip()
                itype = str(r.get("item_type", "")).strip()
                ukey = str(r.get("unique_key", "")).strip()
                attach_options.append((f"{itype.upper()} — {ukey} — ({item_id})", item_id))

        attach_label = st.selectbox("¿A qué pertenece?", [x[0] for x in attach_options], key=f"doc_attach_sel_{case_id}", disabled=is_locked)
        attach_item_id = dict(attach_options).get(attach_label, "")

        doc_type = st.selectbox(
            "Tipo de documento",
            ["passport", "driver_license", "title", "invoice", "bill_of_sale", "other"],
            key=f"doc_type_{case_id}",
            disabled=is_locked,
        )

        upload = st.file_uploader(
            "Subir documento",
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
