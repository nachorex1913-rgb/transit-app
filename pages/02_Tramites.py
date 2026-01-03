# app/pages/02_Tramites.py
from __future__ import annotations

import re
import streamlit as st
from datetime import datetime

from transit_core.gsheets_db import (
    list_clients,
    list_cases,
    create_case,
    update_case_fields,
    list_items,
    add_vehicle_item,
    add_article_item,
    list_documents,
    add_document,
)
from transit_core.drive_bridge import (
    create_case_folder_via_script,
    upload_file_to_case_folder_via_script,
)
from transit_core.ids import next_case_id
from transit_core.validators import normalize_vin, is_valid_vin
from transit_core.vin_decode import decode_vin
from transit_core.case_pdf import build_case_summary_pdf


st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")

OFFICE_EDIT_CODE = "778899"
DOC_TYPES = ["ID_CLIENTE", "TITULO_VEHICULO", "FACTURA_VEHICULO", "FACTURA_ARTICULO", "OTRO", "RESUMEN_TRAMITE"]


# ----------------------------
# Helpers
# ----------------------------
def _safe(s: str) -> str:
    return (s or "").strip()

def _norm_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", _safe(s))

def _sanitize_filename(name: str) -> str:
    name = _norm_spaces(name)
    name = re.sub(r"[^\w\-\.\s]+", "", name, flags=re.UNICODE)
    name = name.replace(" ", "_")
    return name[:120] if len(name) > 120 else name

def _parse_article_dictation(text: str) -> dict:
    """
    Dictado continuo recomendado:
    tipo lavadora ref 440827 marca Sienna modelo Sleep4415 peso 95 lb estado usado cantidad 1 valor 120 parte_vehiculo no
    """
    t = _norm_spaces(text)
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
        "tipo": "type", "type": "type",
        "ref": "ref", "referencia": "ref", "serie": "ref", "serial": "ref",
        "marca": "brand", "brand": "brand",
        "modelo": "model", "model": "model",
        "peso": "weight", "weight": "weight",
        "estado": "condition", "condition": "condition",
        "cantidad": "quantity", "qty": "quantity", "quantity": "quantity",
        "parte_vehiculo": "is_vehicle_part", "partevehiculo": "is_vehicle_part",
        "parte": "is_vehicle_part", "vehicle_part": "is_vehicle_part",
        "vin": "parent_vin", "vin_padre": "parent_vin", "parent_vin": "parent_vin",
        "valor": "value", "value": "value",
    }

    if has_colon:
        for p in parts:
            if ":" not in p:
                continue
            k, v = p.split(":", 1)
            k = _safe(k).lower()
            v = _safe(v)
            k = re.sub(r"[^\wáéíóúüñ_]+", "", k)
            key = aliases.get(k)
            if not key:
                continue
            if key == "quantity":
                try:
                    data["quantity"] = int(re.findall(r"\d+", v)[0])
                except Exception:
                    data["quantity"] = 1
            elif key == "is_vehicle_part":
                data["is_vehicle_part"] = v.lower() in ("si", "sí", "yes", "true", "1")
            elif key == "parent_vin":
                data["parent_vin"] = normalize_vin(v)
            else:
                data[key] = v
        return data

    tokens = t.split(" ")
    i = 0
    current_key = None
    buff = []

    def flush():
        nonlocal current_key, buff
        if not current_key:
            buff = []
            return
        val = _norm_spaces(" ".join(buff))
        if current_key == "quantity":
            try:
                data["quantity"] = int(re.findall(r"\d+", val)[0])
            except Exception:
                data["quantity"] = 1
        elif current_key == "is_vehicle_part":
            data["is_vehicle_part"] = val.lower() in ("si", "sí", "yes", "true", "1")
        elif current_key == "parent_vin":
            data["parent_vin"] = normalize_vin(val)
        else:
            data[current_key] = val
        buff = []

    while i < len(tokens):
        tok = tokens[i].lower().strip()
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


def _build_article_description(
    item_type: str,
    ref: str,
    brand: str,
    model: str,
    weight: str,
    condition: str,
    quantity: int,
    value: str,
    is_part: bool,
    parent_vin: str,
) -> str:
    chunks = []
    if item_type:
        chunks.append(f"Tipo: {item_type}")
    if ref:
        chunks.append(f"Ref: {ref}")
    if brand:
        chunks.append(f"Marca: {brand}")
    if model:
        chunks.append(f"Modelo: {model}")
    if weight:
        chunks.append(f"Peso: {weight}")
    if condition:
        chunks.append(f"Estado: {condition}")
    if quantity:
        chunks.append(f"Cantidad: {int(quantity)}")
    if value:
        chunks.append(f"Valor: {value}")
    if is_part:
        pv = normalize_vin(parent_vin)
        if pv and len(pv) == 17 and is_valid_vin(pv):
            chunks.append(f"Parte de vehículo: {pv}")
        else:
            chunks.append("Parte de vehículo: Sí")
    else:
        chunks.append("Parte de vehículo: No")
    return " | ".join(chunks).strip()


def _case_label(case_row: dict, clients_df) -> str:
    cid = str(case_row.get("case_id", ""))
    client_id = str(case_row.get("client_id", ""))
    status = str(case_row.get("status", ""))
    client_name = ""
    if clients_df is not None and not clients_df.empty and "client_id" in clients_df.columns:
        m = clients_df[clients_df["client_id"].astype(str) == client_id]
        if not m.empty:
            client_name = str(m.iloc[0].get("name", "")).strip()
    return f"{cid} — {client_name} ({status})".strip()


# ----------------------------
# Tabs
# ----------------------------
tab_create, tab_manage, tab_list = st.tabs(["➕ Crear trámite", "🧾 Gestionar trámite", "📋 Listado & estatus"])


# =========================================================
# TAB 1: Crear trámite
# =========================================================
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
            year = datetime.now().year
            case_id_new = next_case_id(existing_ids, year=year)

            folder_name = f"{case_id_new} - {client_name}".strip()
            res = create_case_folder_via_script(case_id=case_id_new, folder_name=folder_name)
            drive_folder_id = res["folder_id"]

            created_case_id = create_case(
                client_id=client_id,
                origin=_safe(origin) or "USA",
                destination=_safe(destination),
                notes=_safe(notes),
                drive_folder_id=drive_folder_id,
            )

            st.success(f"✅ Trámite creado: {created_case_id}")
            st.info(f"📁 Carpeta Drive: {folder_name}")
            st.rerun()

        except Exception as e:
            st.error(f"Error creando trámite: {type(e).__name__}: {e}")


# =========================================================
# TAB 3: Listado & estatus
# =========================================================
with tab_list:
    st.subheader("Listado de trámites y estatus")

    clients_df = list_clients().fillna("")
    cases_df = list_cases().fillna("")
    if cases_df.empty:
        st.info("No hay trámites.")
    else:
        df = cases_df.copy()
        if not clients_df.empty and "client_id" in df.columns and "client_id" in clients_df.columns:
            m = clients_df[["client_id", "name"]].copy()
            m.columns = ["client_id", "client_name"]
            df = df.merge(m, on="client_id", how="left")

        show_cols = [c for c in ["case_id","client_name","status","origin","destination","drive_folder_id","created_at","updated_at"] if c in df.columns]
        st.dataframe(df[show_cols], use_container_width=True)


# =========================================================
# TAB 2: Gestionar trámite
# =========================================================
with tab_manage:
    st.subheader("Gestionar trámite")

    clients_df = list_clients().fillna("")
    cases_df = list_cases().fillna("")
    if cases_df.empty:
        st.info("No hay trámites aún.")
        st.stop()

    borradores = cases_df[cases_df["status"].astype(str).str.lower() == "borrador"] if "status" in cases_df.columns else cases_df

    allow_edit_locked = st.checkbox("Editar trámites Pendiente/Enviado (requiere código)", value=False, key="allow_edit_locked")
    office_code_ok = False
    if allow_edit_locked:
        code = st.text_input("Código oficina", type="password", key="office_code")
        office_code_ok = (code == OFFICE_EDIT_CODE)

    if allow_edit_locked and office_code_ok:
        cases_for_manage = cases_df
        st.info("Modo edición habilitado para trámites Pendiente/Enviado.")
    else:
        cases_for_manage = borradores
        st.caption("Solo se muestran trámites en **Borrador**.")

    if cases_for_manage.empty:
        st.warning("No hay trámites disponibles para gestionar con los filtros actuales.")
        st.stop()

    options = []
    rows = []
    for _, r in cases_for_manage.iterrows():
        rr = r.to_dict()
        rows.append(rr)
        options.append(_case_label(rr, clients_df))

    idx = st.selectbox("Selecciona un trámite", list(range(len(options))), format_func=lambda i: options[i], key="case_select_idx")
    case = rows[int(idx)]
    case_id = str(case.get("case_id", ""))
    case_status = str(case.get("status", ""))
    drive_folder_id = str(case.get("drive_folder_id", ""))
    client_id = str(case.get("client_id", ""))

    client_name = ""
    if not clients_df.empty:
        m = clients_df[clients_df["client_id"].astype(str) == client_id]
        if not m.empty:
            client_name = str(m.iloc[0].get("name", "")).strip()

    st.write(f"**Trámite:** {case_id}  |  **Cliente:** {client_name}  |  **Estatus:** {case_status}")
    st.write(f"**Drive folder_id:** {drive_folder_id}")

    # =========================
    # DATA LOAD
    # =========================
    items_df = list_items(case_id=case_id)
    if items_df is not None and not items_df.empty:
        items_df = items_df.fillna("")
        items_df = items_df.copy().reset_index(drop=True)
        items_df.insert(0, "No.", range(1, len(items_df) + 1))
        st.subheader("Items registrados")
        st.dataframe(items_df, use_container_width=True)
    else:
        st.info("Aún no hay vehículos ni artículos en este trámite.")

    st.divider()

    # ============================================
    # FLAGS para limpiar sin romper Streamlit
    # ============================================
    clear_vehicle_flag = f"__clear_vehicle_{case_id}"
    clear_article_flag = f"__clear_article_{case_id}"

    # VEH keys
    vin_text_key = f"vin_text_{case_id}"
    vin_norm_key = f"vin_norm_{case_id}"
    vin_decoded_key = f"vin_decoded_{case_id}"
    vin_confirm_key = f"vin_confirm_{case_id}"

    veh_brand_key = f"veh_brand_{case_id}"
    veh_model_key = f"veh_model_{case_id}"
    veh_year_key = f"veh_year_{case_id}"
    veh_trim_key = f"veh_trim_{case_id}"
    veh_engine_key = f"veh_engine_{case_id}"
    veh_vtype_key = f"veh_vtype_{case_id}"
    veh_body_key = f"veh_body_{case_id}"
    veh_plant_key = f"veh_plant_{case_id}"
    veh_gvwr_key = f"veh_gvwr_{case_id}"
    veh_curb_key = f"veh_curb_{case_id}"
    veh_weight_key = f"veh_weight_{case_id}"
    veh_desc_key = f"veh_desc_{case_id}"

    # ART keys
    art_dict_key = f"art_dict_{case_id}"
    art_type_key = f"art_type_{case_id}"
    art_ref_key = f"art_ref_{case_id}"
    art_brand_key = f"art_brand_{case_id}"
    art_model_key = f"art_model_{case_id}"
    art_weight_key = f"art_weight_{case_id}"
    art_cond_key = f"art_cond_{case_id}"
    art_qty_key = f"art_qty_{case_id}"
    art_value_key = f"art_value_{case_id}"
    art_is_part_key = f"art_is_part_{case_id}"
    art_parent_vin_key = f"art_parent_vin_{case_id}"

    # APPLY CLEAR BEFORE WIDGETS
    if st.session_state.get(clear_vehicle_flag):
        st.session_state[vin_text_key] = ""
        st.session_state[vin_decoded_key] = {}
        st.session_state[vin_confirm_key] = False
        st.session_state[veh_brand_key] = ""
        st.session_state[veh_model_key] = ""
        st.session_state[veh_year_key] = ""
        st.session_state[veh_trim_key] = ""
        st.session_state[veh_engine_key] = ""
        st.session_state[veh_vtype_key] = ""
        st.session_state[veh_body_key] = ""
        st.session_state[veh_plant_key] = ""
        st.session_state[veh_gvwr_key] = ""
        st.session_state[veh_curb_key] = ""
        st.session_state[veh_weight_key] = ""
        st.session_state[veh_desc_key] = ""
        st.session_state[clear_vehicle_flag] = False

    if st.session_state.get(clear_article_flag):
        st.session_state[art_dict_key] = ""
        st.session_state[art_type_key] = ""
        st.session_state[art_ref_key] = ""
        st.session_state[art_brand_key] = ""
        st.session_state[art_model_key] = ""
        st.session_state[art_weight_key] = ""
        st.session_state[art_cond_key] = ""
        st.session_state[art_qty_key] = 1
        st.session_state[art_value_key] = ""
        st.session_state[art_is_part_key] = False
        st.session_state[art_parent_vin_key] = ""
        st.session_state[clear_article_flag] = False

    # ---------------------------
    # VEHÍCULO
    # ---------------------------
    st.subheader("Agregar vehículo")
    st.caption("Dicta el VIN claramente, o pégalo en el campo.")

    st.session_state.setdefault(vin_text_key, "")
    st.session_state.setdefault(vin_decoded_key, {})

    st.session_state.setdefault(veh_brand_key, "")
    st.session_state.setdefault(veh_model_key, "")
    st.session_state.setdefault(veh_year_key, "")
    st.session_state.setdefault(veh_trim_key, "")
    st.session_state.setdefault(veh_engine_key, "")
    st.session_state.setdefault(veh_vtype_key, "")
    st.session_state.setdefault(veh_body_key, "")
    st.session_state.setdefault(veh_plant_key, "")
    st.session_state.setdefault(veh_gvwr_key, "")
    st.session_state.setdefault(veh_curb_key, "")
    st.session_state.setdefault(veh_weight_key, "")
    st.session_state.setdefault(veh_desc_key, "")

    vin_text = st.text_input("VIN", key=vin_text_key)
    vin_norm = normalize_vin(vin_text)
    st.session_state[vin_norm_key] = vin_norm

    with st.expander("🧪 Debug VIN (punto exacto de lectura)"):
        st.write("Texto recibido:", vin_text)
        st.write("vin_norm:", vin_norm)
        st.write("len:", len(vin_norm))
        st.write("is_valid_vin:", bool(vin_norm and len(vin_norm) == 17 and is_valid_vin(vin_norm)))

    colA, colB = st.columns([1, 2])
    with colA:
        confirm_vin = st.checkbox("✅ Confirmo que el VIN es correcto", key=vin_confirm_key)
    with colB:
        consult_btn = st.button(
            "Consultar información del vehículo",
            disabled=(not confirm_vin or not vin_norm or len(vin_norm) != 17 or not is_valid_vin(vin_norm)),
            key=f"consult_vin_{case_id}"
        )

    if consult_btn:
        out = decode_vin(vin_norm) or {}
        if out.get("error"):
            st.warning(out["error"])
            st.session_state[vin_decoded_key] = {}
        else:
            st.session_state[vin_decoded_key] = out
            st.session_state[veh_brand_key] = str(out.get("brand","") or "")
            st.session_state[veh_model_key] = str(out.get("model","") or "")
            st.session_state[veh_year_key] = str(out.get("year","") or "")
            st.session_state[veh_trim_key] = str(out.get("trim","") or "")
            st.session_state[veh_engine_key] = str(out.get("engine","") or "")
            st.session_state[veh_vtype_key] = str(out.get("vehicle_type","") or "")
            st.session_state[veh_body_key] = str(out.get("body_class","") or "")
            st.session_state[veh_plant_key] = str(out.get("plant_country","") or "")
            st.session_state[veh_gvwr_key] = str(out.get("gvwr","") or "")
            st.session_state[veh_curb_key] = str(out.get("curb_weight","") or "")
            st.success("✅ Info consultada. Revisa antes de guardar.")

    decoded = st.session_state.get(vin_decoded_key, {}) or {}
    with st.expander("🧪 Debug decoder (respuesta completa)"):
        st.json(decoded)

    st.markdown("### Datos del vehículo")
    vc1, vc2, vc3 = st.columns(3)
    with vc1:
        brand = st.text_input("Marca", key=veh_brand_key)
    with vc2:
        model = st.text_input("Modelo", key=veh_model_key)
    with vc3:
        year = st.text_input("Año", key=veh_year_key)

    vc4, vc5, vc6 = st.columns(3)
    with vc4:
        trim = st.text_input("Trim (opcional)", key=veh_trim_key)
    with vc5:
        engine = st.text_input("Engine (opcional)", key=veh_engine_key)
    with vc6:
        vehicle_type = st.text_input("Vehicle type (opcional)", key=veh_vtype_key)

    vc7, vc8, vc9 = st.columns(3)
    with vc7:
        body_class = st.text_input("Body class (opcional)", key=veh_body_key)
    with vc8:
        plant_country = st.text_input("Plant country (opcional)", key=veh_plant_key)
    with vc9:
        gvwr = st.text_input("GVWR (opcional)", key=veh_gvwr_key)

    vc10, vc11 = st.columns(2)
    with vc10:
        curb_weight = st.text_input("Curb weight (opcional)", key=veh_curb_key)
    with vc11:
        weight_opt = st.text_input("Peso (opcional)", key=veh_weight_key)

    description = st.text_area("Descripción (opcional)", height=60, key=veh_desc_key)

    save_vehicle_confirm = st.checkbox(
        "✅ Confirmo que VIN + datos del vehículo están listos para guardar",
        value=False,
        key=f"save_vehicle_confirm_{case_id}",
    )

    if st.button("Guardar vehículo", type="primary", disabled=not save_vehicle_confirm, key=f"save_vehicle_{case_id}"):
        try:
            if not vin_norm or len(vin_norm) != 17:
                raise ValueError("VIN debe tener 17 caracteres.")
            if not is_valid_vin(vin_norm):
                raise ValueError("VIN inválido. Debe tener 17 caracteres y NO incluir I/O/Q.")

            add_vehicle_item(
                case_id=case_id,
                vin=vin_norm,
                brand=brand,
                model=model,
                year=year,
                trim=trim,
                engine=engine,
                vehicle_type=vehicle_type,
                body_class=body_class,
                plant_country=plant_country,
                gvwr=gvwr,
                curb_weight=curb_weight,
                description=description,
                quantity=1,
                weight=weight_opt,
                value="0",
                source="vin_text",
            )

            st.success("✅ Vehículo guardado correctamente.")
            st.session_state[clear_vehicle_flag] = True
            st.rerun()

        except Exception as e:
            st.error(f"Error guardando vehículo: {type(e).__name__}: {e}")

    st.divider()

    # ---------------------------
    # ARTÍCULOS
    # ---------------------------
    st.subheader("Agregar artículos")

    st.caption("Dicta claramente, con voz clara y fuerte. Ejemplo:")
    st.code("tipo lavadora ref 440827 marca Sienna modelo Sleep4415 peso 95 lb estado usado cantidad 1 valor 120 parte_vehiculo no", language="text")

    st.session_state.setdefault(art_dict_key, "")
    st.session_state.setdefault(art_type_key, "")
    st.session_state.setdefault(art_ref_key, "")
    st.session_state.setdefault(art_brand_key, "")
    st.session_state.setdefault(art_model_key, "")
    st.session_state.setdefault(art_weight_key, "")
    st.session_state.setdefault(art_cond_key, "")
    st.session_state.setdefault(art_qty_key, 1)
    st.session_state.setdefault(art_value_key, "")
    st.session_state.setdefault(art_is_part_key, False)
    st.session_state.setdefault(art_parent_vin_key, "")

    dictation = st.text_area("Dictado", height=90, key=art_dict_key)
    parsed = _parse_article_dictation(dictation)

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("Aplicar dictado", key=f"apply_dict_{case_id}"):
            st.session_state[art_type_key] = parsed.get("type","") or ""
            st.session_state[art_ref_key] = parsed.get("ref","") or ""
            st.session_state[art_brand_key] = parsed.get("brand","") or ""
            st.session_state[art_model_key] = parsed.get("model","") or ""
            st.session_state[art_weight_key] = parsed.get("weight","") or ""
            st.session_state[art_cond_key] = parsed.get("condition","") or ""
            try:
                st.session_state[art_qty_key] = int(parsed.get("quantity", 1) or 1)
            except Exception:
                st.session_state[art_qty_key] = 1
            st.session_state[art_value_key] = parsed.get("value","") or ""
            st.session_state[art_is_part_key] = bool(parsed.get("is_vehicle_part", False))
            st.session_state[art_parent_vin_key] = normalize_vin(parsed.get("parent_vin","") or "")
            st.success("✅ Dictado aplicado.")
            st.rerun()
    with col2:
        with st.expander("🧪 Debug dictado parseado"):
            st.json(parsed)

    ac1, ac2, ac3 = st.columns(3)
    with ac1:
        art_type = st.text_input("Tipo (lavadora, secadora, caja, etc.)", key=art_type_key)
    with ac2:
        art_ref = st.text_input("Serie/Referencia", key=art_ref_key)
    with ac3:
        art_brand = st.text_input("Marca", key=art_brand_key)

    ac4, ac5, ac6 = st.columns(3)
    with ac4:
        art_model = st.text_input("Modelo", key=art_model_key)
    with ac5:
        art_weight = st.text_input("Peso (lb/kg)", key=art_weight_key)
    with ac6:
        art_condition = st.text_input("Estado (nuevo/usado)", key=art_cond_key)

    ac7, ac8 = st.columns(2)
    with ac7:
        art_qty = st.number_input("Cantidad", min_value=1, step=1, value=int(st.session_state[art_qty_key]), key=art_qty_key)
    with ac8:
        art_value = st.text_input("Valor (opcional)", key=art_value_key)

    is_part = st.checkbox("¿Es parte del vehículo?", key=art_is_part_key)

    parent_vin = ""
    if is_part:
        vins = []
        if items_df is not None and not items_df.empty and "item_type" in items_df.columns and "unique_key" in items_df.columns:
            vins = items_df[items_df["item_type"] == "vehicle"]["unique_key"].tolist()
            vins = [v for v in vins if v]
        if vins:
            parent_vin = st.selectbox("VIN del vehículo al que pertenece", vins, key=f"art_parent_sel_{case_id}")
        else:
            parent_vin = st.text_input("VIN del vehículo (no hay vehículos registrados aún)", key=art_parent_vin_key)
    else:
        parent_vin = ""

    desc_preview = _build_article_description(
        item_type=art_type,
        ref=art_ref,
        brand=art_brand,
        model=art_model,
        weight=art_weight,
        condition=art_condition,
        quantity=int(art_qty),
        value=art_value,
        is_part=bool(is_part),
        parent_vin=parent_vin,
    )

    # ✅ SIN KEY para que se actualice visualmente siempre
    st.text_area("Descripción (automática)", value=desc_preview, height=70, disabled=True)

    confirm_article = st.checkbox(
        "✅ Confirmo que la información del artículo es correcta antes de guardar.",
        value=False,
        key=f"art_confirm_{case_id}",
    )

    if st.button("Guardar artículo", type="primary", disabled=not confirm_article, key=f"save_article_{case_id}"):
        try:
            add_article_item(
                case_id=case_id,
                description=desc_preview,
                brand=art_brand,
                model=art_model,
                quantity=int(art_qty),
                weight=art_weight,
                value=art_value,
                source="voice" if _safe(dictation) else "manual",
            )

            st.success("✅ Artículo guardado correctamente. Puedes agregar otro.")
            st.session_state[clear_article_flag] = True
            st.rerun()

        except Exception as e:
            st.error(f"Error guardando artículo: {type(e).__name__}: {e}")

    st.divider()

    # ---------------------------
    # DOCUMENTOS (ÚNICO LUGAR)
    # ---------------------------
    st.subheader("📎 Documentos del trámite")

    if not drive_folder_id:
        st.warning("Este trámite todavía no tiene carpeta en Drive. (drive_folder_id vacío)")
    else:
        docs_df = list_documents(case_id)
        if docs_df is not None and not docs_df.empty:
            docs_df = docs_df.fillna("")
            st.dataframe(docs_df, use_container_width=True)
        else:
            st.info("Aún no hay documentos registrados para este trámite.")

        d1, d2 = st.columns([1, 3])
        with d1:
            doc_type = st.selectbox("Tipo de documento", DOC_TYPES, key=f"doc_type_{case_id}")
        with d2:
            st.caption("Sube aquí TODO lo del trámite (cliente/vehículo/artículos). Solo se etiqueta el tipo.")

        files = st.file_uploader(
            "Subir documentos (puedes seleccionar varios)",
            type=["pdf", "jpg", "jpeg", "png"],
            accept_multiple_files=True,
            key=f"docs_upload_{case_id}",
        )

        if st.button("Subir documentos al trámite", type="primary", key=f"upload_docs_btn_{case_id}"):
            try:
                if not files:
                    st.warning("Selecciona uno o más archivos primero.")
                    st.stop()

                ok_count = 0
                for f in files:
                    b = f.getvalue()
                    mime = f.type or "application/octet-stream"
                    name = f.name

                    up = upload_file_to_case_folder_via_script(
                        case_folder_id=drive_folder_id,
                        file_bytes=b,
                        file_name=name,
                        mime_type=mime,
                    )
                    drive_file_id = up.get("file_id","")
                    add_document(
                        case_id=case_id,
                        drive_file_id=drive_file_id,
                        file_name=name,
                        doc_type=doc_type,
                        item_id="",
                    )
                    ok_count += 1

                st.success(f"✅ {ok_count} archivo(s) subido(s) y registrado(s).")
                st.rerun()

            except Exception as e:
                st.error(f"Error subiendo documentos: {type(e).__name__}: {e}")

    st.divider()

    # ---------------------------
    # RESUMEN (Acordeón)
    # ---------------------------
    st.subheader("📌 Resumen del trámite")

    docs_df2 = list_documents(case_id)
    has_docs = docs_df2 is not None and not docs_df2.empty
    has_items = items_df is not None and not items_df.empty

    vehicles = []
    articles = []
    if items_df is not None and not items_df.empty:
        df_raw = items_df.copy().fillna("")
        # intentamos separar por item_type si existe
        if "item_type" in df_raw.columns:
            vdf = df_raw[df_raw["item_type"].astype(str) == "vehicle"]
            adf = df_raw[df_raw["item_type"].astype(str) == "article"]
            vehicles = vdf.to_dict(orient="records")
            articles = adf.to_dict(orient="records")
        else:
            # fallback: todo a "articles"
            articles = df_raw.to_dict(orient="records")

    documents = []
    if docs_df2 is not None and not docs_df2.empty:
        documents = docs_df2.fillna("").to_dict(orient="records")

    with st.expander("Ver resumen completo (vehículos + artículos + documentos)", expanded=True):
        st.write(f"**Trámite:** {case_id}")
        st.write(f"**Cliente:** {client_name}")
        st.write(f"**Origen/Destino:** {_safe(case.get('origin'))} → {_safe(case.get('destination'))}")
        st.write(f"**Estatus:** {case_status}")
        st.write(f"**Folder (Drive):** {drive_folder_id}")

        st.markdown("### Vehículos")
        if vehicles:
            st.dataframe(vehicles, use_container_width=True)
        else:
            st.info("No hay vehículos registrados.")

        st.markdown("### Artículos")
        if articles:
            st.dataframe(articles, use_container_width=True)
        else:
            st.info("No hay artículos registrados.")

        st.markdown("### Documentos")
        if documents:
            st.dataframe(documents, use_container_width=True)
        else:
            st.info("No hay documentos subidos.")

    st.divider()

    # ---------------------------
    # VALIDACIÓN + PDF + PASAR A PENDIENTE
    # ---------------------------
    st.subheader("✅ Validación y envío (Pendiente + PDF)")

    st.write(f"- Items registrados: {'✅' if has_items else '❌'}")
    st.write(f"- Documentos subidos: {'✅' if has_docs else '❌'}")

    ready = st.checkbox("Confirmo que el trámite está completo y listo para enviar", value=False, key=f"case_ready_{case_id}")

    can_finalize = ready and has_items and has_docs and bool(drive_folder_id)

    if st.button("Marcar como PENDIENTE + Generar PDF", disabled=not can_finalize, type="primary", key=f"finalize_{case_id}"):
        try:
            # 1) generar PDF bytes
            pdf_bytes = build_case_summary_pdf(
                case=case,
                client_name=client_name,
                vehicles=vehicles,
                articles=articles,
                documents=documents,
            )

            # 2) nombre requerido
            # Formato: RESUMEN_TRAMITE_###_name_PDF(...) -> lo dejo limpio:
            pdf_name = _sanitize_filename(f"RESUMEN_TRAMITE_{case_id}_{client_name}.pdf")

            # 3) subir a Drive
            up = upload_file_to_case_folder_via_script(
                case_folder_id=drive_folder_id,
                file_bytes=pdf_bytes,
                file_name=pdf_name,
                mime_type="application/pdf",
            )
            pdf_drive_id = up.get("file_id","")

            # 4) registrar documento en Sheets
            add_document(
                case_id=case_id,
                drive_file_id=pdf_drive_id,
                file_name=pdf_name,
                doc_type="RESUMEN_TRAMITE",
                item_id="",
            )

            # 5) cambiar estatus
            update_case_fields(case_id, {"status": "Pendiente", "updated_at": datetime.now().isoformat(timespec="seconds")})

            st.success("✅ Listo: Trámite marcado como Pendiente y PDF generado/subido a Drive.")
            st.rerun()

        except Exception as e:
            st.error(f"Error finalizando trámite: {type(e).__name__}: {e}")
