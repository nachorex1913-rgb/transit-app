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
    list_documents,
)
from transit_core.drive_bridge import create_case_folder_via_script
from transit_core.validators import normalize_vin, is_valid_vin
from transit_core.vin_decode import decode_vin
from transit_core.ids import next_article_seq

st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")

OFFICE_EDIT_CODE = "778899"


# ---------------------------
# Helpers
# ---------------------------
def _now_year() -> int:
    return datetime.now().year


def _client_map():
    cdf = list_clients().fillna("")
    mp = {}
    if not cdf.empty and "client_id" in cdf.columns:
        for _, r in cdf.iterrows():
            mp[str(r.get("client_id",""))] = str(r.get("name","")).strip()
    return mp


def _safe_str(x) -> str:
    return "" if x is None else str(x)


def _auto_article_desc(
    art_type: str,
    ref: str,
    brand: str,
    model: str,
    weight: str,
    condition: str,
    quantity: int,
    value: str,
) -> str:
    parts = []
    if art_type.strip():
        parts.append(art_type.strip())
    if ref.strip():
        parts.append(f"Ref: {ref.strip()}")
    if brand.strip():
        parts.append(f"Marca: {brand.strip()}")
    if model.strip():
        parts.append(f"Modelo: {model.strip()}")
    if weight.strip():
        parts.append(f"Peso: {weight.strip()}")
    if condition.strip():
        parts.append(f"Estado: {condition.strip()}")
    if quantity:
        parts.append(f"Cantidad: {int(quantity)}")
    if value.strip():
        parts.append(f"Valor: {value.strip()}")
    return " | ".join(parts).strip()


def _parse_article_dictation(text: str) -> dict:
    """
    Soporta continuo, sin ":":
    tipo lavadora ref 440827 marca Sienna modelo Sleep4415 peso 95 lb estado usado cantidad 1 valor 120 parte_vehiculo no
    """
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
        "value": "",
    }
    if not t:
        return data

    aliases = {
        "tipo": "type",
        "ref": "ref", "referencia": "ref", "serie": "ref", "serial": "ref",
        "marca": "brand", "brand": "brand",
        "modelo": "model", "model": "model",
        "peso": "weight", "weight": "weight", "kilo": "weight", "kilos": "weight", "kg": "weight", "kilogramos": "weight", "lb": "weight", "libras": "weight",
        "estado": "condition", "condicion": "condition", "condition": "condition",
        "cantidad": "quantity", "qty": "quantity", "quantity": "quantity",
        "valor": "value", "value": "value",
        "parte_vehiculo": "is_vehicle_part", "partevehiculo": "is_vehicle_part", "parte": "is_vehicle_part",
    }

    tokens = re.split(r"\s+", t)
    i = 0
    cur = None
    buff = []

    def flush():
        nonlocal cur, buff
        if not cur:
            buff = []
            return
        val = " ".join(buff).strip()
        if cur == "type":
            data["type"] = val
        elif cur == "ref":
            data["ref"] = val
        elif cur == "brand":
            data["brand"] = val
        elif cur == "model":
            data["model"] = val
        elif cur == "weight":
            data["weight"] = val
        elif cur == "condition":
            data["condition"] = val
        elif cur == "quantity":
            try:
                data["quantity"] = int(re.findall(r"\d+", val)[0])
            except Exception:
                data["quantity"] = 1
        elif cur == "value":
            data["value"] = val
        elif cur == "is_vehicle_part":
            data["is_vehicle_part"] = val.lower() in ("si", "sí", "yes", "true", "1")
        buff = []

    while i < len(tokens):
        tok = tokens[i].strip().lower()
        tok_clean = re.sub(r"[^\wáéíóúüñ_]+", "", tok)
        if tok_clean in aliases:
            flush()
            cur = aliases[tok_clean]
            buff = []
        else:
            buff.append(tokens[i])
        i += 1
    flush()

    return data


# ---------------------------
# Tabs
# ---------------------------
tab_create, tab_manage, tab_list = st.tabs(["➕ Crear trámite", "🧰 Gestionar trámite", "📋 Listado & estatus"])

client_name_by_id = _client_map()


# ============================================================
# TAB 1: Crear trámite
# ============================================================
with tab_create:
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
            year = _now_year()
            # case_id lo genera create_case internamente, pero aquí lo necesitamos para carpeta (nombre)
            # entonces generamos el siguiente con ids actuales:
            from transit_core.ids import next_case_id
            case_id_new = next_case_id(existing_ids, year=year)

            root_folder_id = st.secrets["drive"]["root_folder_id"]
            folder_name = f"{case_id_new} - {client_name}".strip()

            res = create_case_folder_via_script(
                root_folder_id=root_folder_id,
                case_id=case_id_new,
                folder_name=folder_name,
            )
            drive_folder_id = res.get("folder_id","")

            created_case_id = create_case(
                client_id=client_id,
                origin=origin.strip() or "USA",
                destination=destination.strip(),
                notes=notes.strip(),
                drive_folder_id=drive_folder_id,
            )

            st.success(f"✅ Trámite creado: {created_case_id}")
            st.info(f"📁 Carpeta: {folder_name}")
            st.rerun()

        except Exception as e:
            st.error(f"Error creando trámite: {type(e).__name__}: {e}")


# ============================================================
# TAB 2: Gestionar trámite
# ============================================================
with tab_manage:
    st.subheader("Gestionar trámite")

    cases_df = list_cases().fillna("")
    if cases_df.empty:
        st.info("No hay trámites aún.")
        st.stop()

    # Por defecto: solo borrador
    only_draft = st.checkbox("Mostrar solo trámites en Borrador", value=True, key="only_draft")
    if only_draft and "status" in cases_df.columns:
        view_df = cases_df[cases_df["status"].astype(str).str.lower() == "borrador"].copy()
    else:
        view_df = cases_df.copy()

    if view_df.empty:
        st.info("No hay trámites para gestionar con ese filtro.")
        st.stop()

    # label con cliente
    def _case_label(r):
        cid = str(r.get("case_id",""))
        clid = str(r.get("client_id",""))
        cname = client_name_by_id.get(clid, "")
        stt = str(r.get("status",""))
        return f"{cid} — {cname} — {stt}"

    view_df["label"] = view_df.apply(lambda r: _case_label(r), axis=1)
    selected_label = st.selectbox("Selecciona un trámite", view_df["label"].tolist(), key="case_select")

    row = view_df.loc[view_df["label"] == selected_label].iloc[0]
    selected_case_id = str(row.get("case_id",""))
    case = get_case(selected_case_id)

    if not case:
        st.error("No se pudo cargar el trámite.")
        st.stop()

    case_id = str(case.get("case_id") or "")
    case_status = str(case.get("status") or "")
    case_client_id = str(case.get("client_id") or "")
    case_client_name = client_name_by_id.get(case_client_id, "")
    drive_folder_id = str(case.get("drive_folder_id") or "")

    # lock si no borrador
    is_locked = False
    if case_status.lower() != "borrador":
        is_locked = True
        st.warning(f"Este trámite está en '{case_status}'. Para modificar necesitas autorización.")
        code = st.text_input("Código de autorización", type="password", key=f"unlock_{case_id}")
        if code == OFFICE_EDIT_CODE:
            is_locked = False
            st.success("✅ Autorización correcta. Edición habilitada.")

    # Summary
    st.divider()
    st.subheader("Resumen del trámite")
    st.write(f"**Trámite:** {case_id}")
    st.write(f"**Cliente:** {case_client_name} ({case_client_id})")
    st.write(f"**Estatus:** {case_status}")
    st.write(f"**Drive folder:** {drive_folder_id or '(sin carpeta)'}")

    items_df = list_items(case_id=case_id).fillna("")
    docs_df = list_documents(case_id=case_id).fillna("")

    cA, cB, cC = st.columns(3)
    with cA:
        st.metric("Vehículos", int((items_df["item_type"] == "vehicle").sum()) if not items_df.empty else 0)
    with cB:
        st.metric("Artículos", int((items_df["item_type"] == "article").sum()) if not items_df.empty else 0)
    with cC:
        st.metric("Documentos", int(len(docs_df)) if not docs_df.empty else 0)

    st.divider()
    st.subheader("Items registrados")
    if items_df.empty:
        st.info("Aún no hay vehículos ni artículos en este trámite.")
    else:
        show = items_df.copy()

        # ✅ Mostrar consecutivo REAL por trámite (case_seq)
        if "case_seq" in show.columns:
            show = show.sort_values(by=["case_seq"], ascending=True)

        # ✅ Quitar el índice de Streamlit (la columna de la izquierda que te muestra '8')
        show = show.reset_index(drop=True)

        st.dataframe(show, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Documentos del trámite")
    if docs_df.empty:
        st.info("Aún no hay documentos registrados.")
    else:
        st.dataframe(docs_df.reset_index(drop=True), use_container_width=True, hide_index=True)

    # ---------------------------
    # VALIDACIÓN -> cambia estatus a Pendiente
    # ---------------------------
    st.divider()
    st.subheader("Validación del trámite")

    st.caption("Marca este check cuando TODO el trámite esté completo. Al guardar, cambia a Pendiente automáticamente.")
    ready_key = f"case_ready_{case_id}"
    ready = st.checkbox("✅ Toda la información del trámite está completa y lista para envío", key=ready_key, disabled=is_locked)

    if st.button("Guardar validación", type="primary", disabled=is_locked, key=f"save_validation_{case_id}"):
        try:
            if ready:
                update_case_fields(case_id, {"status": "Pendiente", "updated_at": datetime.now().isoformat(timespec="seconds")})
                st.success("✅ Estatus actualizado a Pendiente.")
                st.rerun()
            else:
                st.info("No marcaste el check. No se cambió el estatus.")
        except Exception as e:
            st.error(f"Error actualizando estatus: {type(e).__name__}: {e}")

    # ---------------------------
    # ACCORDIONS
    # ---------------------------
    st.divider()

    # ============================================================
    # Expander: VEHÍCULO
    # ============================================================
    with st.expander("Agregar vehículo", expanded=False):
        st.caption("Dicta o pega el VIN (17 caracteres).")

        vin_text_key = f"vin_text_{case_id}"
        dec_key = f"vin_decoded_{case_id}"
        confirm_vin_key = f"vin_confirm_{case_id}"

        st.session_state.setdefault(vin_text_key, "")
        st.session_state.setdefault(dec_key, {})

        vin_text = st.text_input("VIN detectado por el texto", key=vin_text_key, disabled=is_locked)
        vin_norm = normalize_vin(vin_text)

        with st.expander("🧪 Debug VIN (punto exacto de lectura)"):
            st.write("Texto recibido:", vin_text)
            st.write("vin_norm:", vin_norm)
            st.write("len:", len(vin_norm))
            st.write("is_valid_vin:", is_valid_vin(vin_norm) if len(vin_norm) == 17 else False)

        can_decode = bool(vin_norm) and len(vin_norm) == 17 and is_valid_vin(vin_norm)

        if st.button("Consultar información del vehículo", disabled=(is_locked or not can_decode), key=f"decode_{case_id}"):
            out = decode_vin(vin_norm) or {}
            st.session_state[dec_key] = out
            if out.get("error"):
                st.warning(out["error"])
            else:
                st.success("✅ Info consultada. Revisa antes de guardar.")

        decoded = st.session_state.get(dec_key, {}) or {}

        with st.expander("🧪 Debug decoder (respuesta completa)"):
            st.json(decoded)

        st.subheader("Datos del vehículo")

        # Campos principales
        veh_brand_key = f"veh_brand_{case_id}"
        veh_model_key = f"veh_model_{case_id}"
        veh_year_key = f"veh_year_{case_id}"
        veh_weight_key = f"veh_weight_{case_id}"  # opcional

        # extras
        veh_trim_key = f"veh_trim_{case_id}"
        veh_engine_key = f"veh_engine_{case_id}"
        veh_vtype_key = f"veh_vtype_{case_id}"
        veh_body_key = f"veh_body_{case_id}"
        veh_plant_key = f"veh_plant_{case_id}"
        veh_gvwr_key = f"veh_gvwr_{case_id}"
        veh_cw_key = f"veh_cw_{case_id}"

        # defaults (solo si aún no existen)
        st.session_state.setdefault(veh_brand_key, _safe_str(decoded.get("brand","")))
        st.session_state.setdefault(veh_model_key, _safe_str(decoded.get("model","")))
        st.session_state.setdefault(veh_year_key, _safe_str(decoded.get("year","")))
        st.session_state.setdefault(veh_weight_key, "")

        st.session_state.setdefault(veh_trim_key, _safe_str(decoded.get("trim","")))
        st.session_state.setdefault(veh_engine_key, _safe_str(decoded.get("engine","")))
        st.session_state.setdefault(veh_vtype_key, _safe_str(decoded.get("vehicle_type","")))
        st.session_state.setdefault(veh_body_key, _safe_str(decoded.get("body_class","")))
        st.session_state.setdefault(veh_plant_key, _safe_str(decoded.get("plant_country","")))
        st.session_state.setdefault(veh_gvwr_key, _safe_str(decoded.get("gvwr","")))
        st.session_state.setdefault(veh_cw_key, _safe_str(decoded.get("curb_weight","")))

        c1, c2, c3 = st.columns(3)
        with c1:
            brand = st.text_input("Marca", key=veh_brand_key, disabled=is_locked)
        with c2:
            model = st.text_input("Modelo", key=veh_model_key, disabled=is_locked)
        with c3:
            year = st.text_input("Año", key=veh_year_key, disabled=is_locked)

        st.text_input("Peso (opcional)", key=veh_weight_key, disabled=is_locked)

        st.divider()
        st.caption("Campos adicionales (si vienen en el decoder, se guardan; si no, quedan vacíos).")

        x1, x2, x3 = st.columns(3)
        with x1:
            trim = st.text_input("Trim", key=veh_trim_key, disabled=is_locked)
        with x2:
            engine = st.text_input("Engine", key=veh_engine_key, disabled=is_locked)
        with x3:
            vtype = st.text_input("Vehicle Type", key=veh_vtype_key, disabled=is_locked)

        y1, y2, y3 = st.columns(3)
        with y1:
            body = st.text_input("Body Class", key=veh_body_key, disabled=is_locked)
        with y2:
            plant = st.text_input("Plant Country", key=veh_plant_key, disabled=is_locked)
        with y3:
            gvwr = st.text_input("GVWR", key=veh_gvwr_key, disabled=is_locked)

        st.text_input("Curb Weight", key=veh_cw_key, disabled=is_locked)

        st.divider()
        st.subheader("Evidencia VIN (opcional: foto/pdf, NO OCR)")
        evidence = st.file_uploader(
            "Sube evidencia del VIN",
            type=["jpg","jpeg","png","pdf"],
            key=f"vin_evidence_{case_id}",
            disabled=is_locked,
        )
        if evidence is not None:
            st.info(f"Evidencia cargada: {evidence.name} (pendiente de subir a Drive en el módulo Documentos)")

        confirm_vehicle = st.checkbox(
            "✅ Confirmo que VIN + datos del vehículo están listos para guardar",
            key=confirm_vin_key,
            disabled=is_locked,
        )

        if st.button("Guardar vehículo", type="primary", disabled=(is_locked or not confirm_vehicle), key=f"save_vehicle_{case_id}"):
            try:
                if not can_decode:
                    raise ValueError("VIN inválido o incompleto. Debe ser 17 caracteres válidos sin I/O/Q.")

                add_vehicle_item(
                    case_id=case_id,
                    vin=vin_norm,
                    brand=brand,
                    model=model,
                    year=year,
                    description="",
                    weight=st.session_state.get(veh_weight_key,""),
                    value="0",
                    source="vin_text",
                    trim=trim,
                    engine=engine,
                    vehicle_type=vtype,
                    body_class=body,
                    plant_country=plant,
                    gvwr=gvwr,
                    curb_weight=st.session_state.get(veh_cw_key,""),
                )

                st.success("✅ Vehículo guardado correctamente.")

                # limpiar formulario
                st.session_state[vin_text_key] = ""
                st.session_state[dec_key] = {}
                st.session_state[confirm_vin_key] = False
                for k in [veh_brand_key, veh_model_key, veh_year_key, veh_weight_key,
                          veh_trim_key, veh_engine_key, veh_vtype_key, veh_body_key, veh_plant_key, veh_gvwr_key, veh_cw_key]:
                    st.session_state[k] = ""
                st.rerun()

            except Exception as e:
                st.error(f"Error guardando vehículo: {type(e).__name__}: {e}")

    # ============================================================
    # Expander: ARTÍCULOS
    # ============================================================
    with st.expander("Agregar artículos", expanded=False):
        st.caption("Dicta claramente, con voz clara y fuerte.")

        dict_key = f"art_dict_{case_id}"
        st.session_state.setdefault(dict_key, "")

        dictation = st.text_area("Dictado", height=90, key=dict_key, disabled=is_locked)

        parsed = _parse_article_dictation(dictation)

        with st.expander("🧪 Debug dictado parseado"):
            st.json(parsed)

        apply_btn = st.button("Aplicar dictado a campos", key=f"apply_art_{case_id}", disabled=is_locked)

        # keys
        k_type = f"art_type_{case_id}"
        k_ref = f"art_ref_{case_id}"
        k_brand = f"art_brand_{case_id}"
        k_model = f"art_model_{case_id}"
        k_weight = f"art_weight_{case_id}"
        k_cond = f"art_cond_{case_id}"
        k_qty = f"art_qty_{case_id}"
        k_part = f"art_is_part_{case_id}"
        k_value = f"art_value_{case_id}"
        k_confirm = f"art_confirm_{case_id}"

        st.session_state.setdefault(k_type, "")
        st.session_state.setdefault(k_ref, "")
        st.session_state.setdefault(k_brand, "")
        st.session_state.setdefault(k_model, "")
        st.session_state.setdefault(k_weight, "")
        st.session_state.setdefault(k_cond, "")
        st.session_state.setdefault(k_qty, 1)
        st.session_state.setdefault(k_part, False)
        st.session_state.setdefault(k_value, "")

        if apply_btn:
            st.session_state[k_type] = parsed.get("type","") or ""
            st.session_state[k_ref] = parsed.get("ref","") or ""
            st.session_state[k_brand] = parsed.get("brand","") or ""
            st.session_state[k_model] = parsed.get("model","") or ""
            st.session_state[k_weight] = parsed.get("weight","") or ""
            st.session_state[k_cond] = parsed.get("condition","") or ""
            try:
                st.session_state[k_qty] = int(parsed.get("quantity", 1) or 1)
            except Exception:
                st.session_state[k_qty] = 1
            st.session_state[k_part] = bool(parsed.get("is_vehicle_part", False))
            st.session_state[k_value] = parsed.get("value","") or ""
            st.success("✅ Dictado aplicado a los campos.")

        c1, c2, c3 = st.columns(3)
        with c1:
            art_type = st.text_input("Tipo de artículo", key=k_type, disabled=is_locked)
        with c2:
            art_ref = st.text_input("Serie/Referencia", key=k_ref, disabled=is_locked)
        with c3:
            art_brand = st.text_input("Marca", key=k_brand, disabled=is_locked)

        d1, d2, d3 = st.columns(3)
        with d1:
            art_model = st.text_input("Modelo", key=k_model, disabled=is_locked)
        with d2:
            art_weight = st.text_input("Peso (lb/kg)", key=k_weight, disabled=is_locked)
        with d3:
            art_condition = st.text_input("Estado (nuevo/usado)", key=k_cond, disabled=is_locked)

        art_qty = st.number_input("Cantidad", min_value=1, value=int(st.session_state[k_qty]), step=1, key=k_qty, disabled=is_locked)
        is_part = st.checkbox("¿Es parte del vehículo?", key=k_part, disabled=is_locked)
        art_value = st.text_input("Valor (USD) (opcional)", key=k_value, disabled=is_locked)

        # parent vin si es parte del vehículo
        parent_vin = ""
        if is_part:
            vins = []
            if not items_df.empty and "item_type" in items_df.columns and "unique_key" in items_df.columns:
                vins = items_df[items_df["item_type"] == "vehicle"]["unique_key"].tolist()
                vins = [v for v in vins if v]
            if vins:
                parent_vin = st.selectbox("Selecciona el VIN del vehículo", vins, disabled=is_locked, key=f"art_parent_{case_id}")
            else:
                st.warning("No hay vehículos registrados en este trámite para asociar.")
                parent_vin = ""

        # descripción automática
        auto_desc = _auto_article_desc(art_type, art_ref, art_brand, art_model, art_weight, art_condition, art_qty, art_value)
        if is_part and parent_vin:
            pv = normalize_vin(parent_vin)
            if pv and len(pv) == 17 and is_valid_vin(pv):
                auto_desc = f"[PARTE_DE_VEHICULO:{pv}] {auto_desc}".strip()

        st.text_area("Descripción (auto)", value=auto_desc, height=80, disabled=True, key=f"art_desc_auto_{case_id}")

        confirm_article = st.checkbox(
            "✅ Confirmo que la información del artículo es correcta antes de guardar.",
            key=k_confirm,
            disabled=is_locked,
        )

        if st.button("Guardar artículo", type="primary", disabled=(is_locked or not confirm_article), key=f"save_article_{case_id}"):
            try:
                # generar unique_key por trámite (A-CASEID-0001)
                existing_keys = []
                if not items_df.empty and "unique_key" in items_df.columns:
                    existing_keys = items_df["unique_key"].tolist()
                unique_key = next_article_seq(existing_keys, case_id=case_id)

                add_article_item(
                    case_id=case_id,
                    unique_key=unique_key,
                    description=auto_desc,
                    brand=art_brand,
                    model=art_model,
                    quantity=int(art_qty),
                    weight=art_weight,
                    value=art_value,
                    source="voice" if dictation.strip() else "manual",
                )

                st.success(f"✅ Artículo guardado correctamente: {unique_key}")

                # limpiar para agregar más
                st.session_state[dict_key] = ""
                for k in [k_type, k_ref, k_brand, k_model, k_weight, k_cond, k_value]:
                    st.session_state[k] = ""
                st.session_state[k_qty] = 1
                st.session_state[k_part] = False
                st.session_state[k_confirm] = False
                st.rerun()

            except Exception as e:
                st.error(f"Error guardando artículo: {type(e).__name__}: {e}")


# ============================================================
# TAB 3: Listado & Estatus
# ============================================================
with tab_list:
    st.subheader("Listado de trámites y estatus")

    cases_df = list_cases().fillna("")
    if cases_df.empty:
        st.info("No hay trámites aún.")
        st.stop()

    # agregar nombre de cliente
    if "client_id" in cases_df.columns:
        cases_df["client_name"] = cases_df["client_id"].astype(str).map(lambda x: client_name_by_id.get(x, ""))
    else:
        cases_df["client_name"] = ""

    # vista ordenada
    cols = [c for c in ["case_id","client_name","status","origin","destination","case_date","drive_folder_id"] if c in cases_df.columns]
    view = cases_df[cols].copy() if cols else cases_df.copy()
    view = view.sort_values(by=["status","case_id"], ascending=[True, True]).reset_index(drop=True)

    st.dataframe(view, use_container_width=True, hide_index=True)
