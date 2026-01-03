# app/pages/02_Tramites.py
from __future__ import annotations

import re
import streamlit as st
from datetime import datetime

from transit_core.gsheets_db import (
    list_clients,
    list_cases,
    get_case,
    create_case,
    update_case_fields,
    list_vehicles,
    list_articles,
    add_vehicle,
    add_article,
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

from transit_core.pdf_builder import build_case_summary_pdf_bytes  # NO tocamos PDF en esta fase


st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")

OFFICE_EDIT_CODE = "778899"
DOC_TYPES = ["ID_CLIENTE", "TITULO_VEHICULO", "FACTURA_VEHICULO", "FACTURA_ARTICULO", "OTRO"]


# ----------------------------
# Estilos (corporativo/aduana)
# ----------------------------
st.markdown(
    """
    <style>
      .card {
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 14px;
        padding: 16px 16px;
        background: rgba(255,255,255,0.03);
      }
      .card h2, .card h3, .card p { margin: 0; }
      .muted { opacity: 0.75; }
      .kpi {
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 14px;
        padding: 14px 12px;
        background: rgba(255,255,255,0.02);
      }
      .kpi .label { font-size: 0.85rem; opacity: 0.75; }
      .kpi .value { font-size: 1.8rem; font-weight: 700; line-height: 1.1; margin-top: 4px; }
      .badge {
        display: inline-block;
        padding: 6px 10px;
        border-radius: 999px;
        font-weight: 700;
        font-size: 0.85rem;
        border: 1px solid rgba(255,255,255,0.18);
        background: rgba(255,255,255,0.04);
      }
      .badge.borrador { color: #f59e0b; }
      .badge.pendiente { color: #60a5fa; }
      .badge.enviado { color: #34d399; }
      .badge.otro { color: #e5e7eb; }
      .section-title {
        font-weight: 800;
        font-size: 1.05rem;
        letter-spacing: 0.2px;
        margin: 12px 0 6px 0;
        opacity: 0.92;
      }
    </style>
    """,
    unsafe_allow_html=True
)


def _safe(s: str) -> str:
    return (s or "").strip()


def _norm_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", _safe(s))


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


def _status_badge(status: str) -> str:
    s = (status or "").strip().lower()
    if s == "borrador":
        cls = "borrador"
        text = "BORRADOR"
    elif s == "pendiente":
        cls = "pendiente"
        text = "PENDIENTE"
    elif s == "enviado":
        cls = "enviado"
        text = "ENVIADO"
    else:
        cls = "otro"
        text = (status or "N/D").upper()
    return f'<span class="badge {cls}">{text}</span>'


def _drive_folder_url(folder_id: str) -> str:
    folder_id = (folder_id or "").strip()
    if not folder_id:
        return ""
    return f"https://drive.google.com/drive/folders/{folder_id}"


def _parse_article_dictation(text: str) -> dict:
    """
    Dictado continuo:
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
            v = val.lower()
            data["is_vehicle_part"] = v in ("si", "sí", "yes", "true", "1")
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


def _build_article_description(d: dict) -> str:
    parts = []
    if d.get("type"): parts.append(f"Tipo: {d['type']}")
    if d.get("ref"): parts.append(f"Ref: {d['ref']}")
    if d.get("brand"): parts.append(f"Marca: {d['brand']}")
    if d.get("model"): parts.append(f"Modelo: {d['model']}")
    if d.get("weight"): parts.append(f"Peso: {d['weight']}")
    if d.get("condition"): parts.append(f"Estado: {d['condition']}")
    parts.append(f"Cantidad: {int(d.get('quantity') or 1)}")
    if d.get("value"): parts.append(f"Valor: {d['value']}")
    if d.get("is_vehicle_part"):
        pv = normalize_vin(d.get("parent_vin", ""))
        parts.append(f"Parte de vehículo: {pv if pv else 'SI'}")
    else:
        parts.append("Parte de vehículo: NO")
    return " | ".join(parts).strip()


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

            st.success(f"Trámite creado: {created_case_id}")
            st.info(f"Carpeta Drive: {folder_name}")
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
        if not clients_df.empty and "client_id" in df.columns:
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
        st.caption("Solo se muestran trámites en **Borrador** (a menos que actives edición oficina).")

    if cases_for_manage.empty:
        st.warning("No hay trámites disponibles para gestionar con los filtros actuales.")
        st.stop()

    options, rows = [], []
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

    # Cargar data una sola vez (evita repetición y lentitud)
    vehicles_df = list_vehicles(case_id=case_id).fillna("")
    articles_df = list_articles(case_id=case_id).fillna("")
    docs_df = list_documents(case_id).fillna("")

    # ---------------------------------------------------------
    # TARJETA EJECUTIVA (CORPORATIVO/ADUANA) + KPI
    # ---------------------------------------------------------
    with st.container():
        st.markdown('<div class="card">', unsafe_allow_html=True)
        topL, topR = st.columns([3, 2])

        with topL:
            st.markdown(f"### {client_name or '(Sin cliente)'}")
            st.markdown(f"**{case_id}**")
            origin = str(case.get("origin", "")).strip()
            destination = str(case.get("destination", "")).strip()
            created_at = str(case.get("created_at", "")).strip()
            line = " | ".join([x for x in [f"Origen: {origin}" if origin else "", f"Destino: {destination}" if destination else "", f"Creado: {created_at}" if created_at else ""] if x])
            if line:
                st.markdown(f"<p class='muted'>{line}</p>", unsafe_allow_html=True)

        with topR:
            st.markdown("**Estatus**")
            st.markdown(_status_badge(case_status), unsafe_allow_html=True)
            url = _drive_folder_url(drive_folder_id)
            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
            st.markdown("**Carpeta Drive**")
            if url:
                st.link_button("Abrir carpeta", url)
            else:
                st.caption("(sin carpeta)")

        k1, k2, k3 = st.columns(3)
        with k1:
            st.markdown(f"<div class='kpi'><div class='label'>Vehículos</div><div class='value'>{0 if vehicles_df.empty else len(vehicles_df)}</div></div>", unsafe_allow_html=True)
        with k2:
            st.markdown(f"<div class='kpi'><div class='label'>Artículos</div><div class='value'>{0 if articles_df.empty else len(articles_df)}</div></div>", unsafe_allow_html=True)
        with k3:
            st.markdown(f"<div class='kpi'><div class='label'>Documentos</div><div class='value'>{0 if docs_df.empty else len(docs_df)}</div></div>", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='section-title'>Resumen completo (registros actuales)</div>", unsafe_allow_html=True)

    # -------------------------
    # RESUMEN EN TABLAS (claro, ejecutivo)
    # -------------------------
    # Vehículos
    st.markdown("#### Vehículos")
    if vehicles_df.empty:
        st.info("Sin vehículos.")
    else:
        v = vehicles_df.copy().fillna("").reset_index(drop=True)
        v.insert(0, "#", range(1, len(v) + 1))
        cols = [c for c in ["#", "vin", "brand", "model", "year", "trim", "engine", "vehicle_type", "body_class", "plant_country", "gvwr", "weight", "description", "created_at"] if c in v.columns]
        st.dataframe(v[cols], use_container_width=True, hide_index=True)

    # Artículos
    st.markdown("#### Artículos")
    if articles_df.empty:
        st.info("Sin artículos.")
    else:
        a = articles_df.copy().fillna("").reset_index(drop=True)
        a.insert(0, "#", range(1, len(a) + 1))
        # Para aduana: no repetir todo, la descripción ya manda
        cols = [c for c in ["#", "seq", "description", "quantity", "weight", "value", "is_vehicle_part", "parent_vin", "created_at"] if c in a.columns]
        st.dataframe(a[cols], use_container_width=True, hide_index=True)

    # Documentos
    st.markdown("#### Documentos")
    if docs_df.empty:
        st.info("Sin documentos.")
    else:
        d = docs_df.copy().fillna("").reset_index(drop=True)
        d.insert(0, "#", range(1, len(d) + 1))
        cols = [c for c in ["#", "doc_type", "file_name", "uploaded_at"] if c in d.columns]
        st.dataframe(d[cols], use_container_width=True, hide_index=True)

    st.divider()

    # =========================================================
    # ACORDEONES OPERATIVOS (se mantienen)
    # =========================================================
    with st.expander("🚗 Vehículos (agregar / ver)", expanded=True):
        st.caption("Pega o dicta el VIN. Consulta y luego guarda.")

        # Nonce para reset seguro (evita StreamlitAPIException)
        vin_nonce_key = f"vin_nonce_{case_id}"
        if vin_nonce_key not in st.session_state:
            st.session_state[vin_nonce_key] = 0

        vin_text_key = f"vin_text_{case_id}_{st.session_state[vin_nonce_key]}"
        vin_decoded_key = f"vin_decoded_{case_id}"

        vin_text = st.text_input("VIN", key=vin_text_key)
        vin_norm = normalize_vin(vin_text)

        colA, colB = st.columns([1, 2])
        with colA:
            confirm_vin = st.checkbox("Confirmo que el VIN es correcto", key=f"vin_ok_{case_id}")
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

                # ✅ Autollenado REAL: escribir en session_state de los widgets
                st.session_state[f"veh_brand_{case_id}"] = str(out.get("brand", "") or "")
                st.session_state[f"veh_model_{case_id}"] = str(out.get("model", "") or "")
                st.session_state[f"veh_year_{case_id}"] = str(out.get("year", "") or "")
                st.session_state[f"veh_trim_{case_id}"] = str(out.get("trim", "") or "")
                st.session_state[f"veh_engine_{case_id}"] = str(out.get("engine", "") or "")
                st.session_state[f"veh_vtype_{case_id}"] = str(out.get("vehicle_type", "") or "")
                st.session_state[f"veh_body_{case_id}"] = str(out.get("body_class", "") or "")
                st.session_state[f"veh_plant_{case_id}"] = str(out.get("plant_country", "") or "")
                st.session_state[f"veh_gvwr_{case_id}"] = str(out.get("gvwr", "") or "")

                # Nota: curb_weight lo guardamos internamente si viene, pero NO lo mostramos
                st.session_state[f"veh_curb_hidden_{case_id}"] = str(out.get("curb_weight", "") or "")

                st.success("Info consultada. Verifica y guarda.")

        decoded = st.session_state.get(vin_decoded_key, {}) or {}
        with st.expander("🧪 Debug decoder", expanded=False):
            st.json(decoded)

        c1, c2, c3 = st.columns(3)
        with c1:
            brand = st.text_input("Marca", key=f"veh_brand_{case_id}")
        with c2:
            model = st.text_input("Modelo", key=f"veh_model_{case_id}")
        with c3:
            year = st.text_input("Año", key=f"veh_year_{case_id}")

        c4, c5, c6 = st.columns(3)
        with c4:
            trim = st.text_input("Trim (opcional)", key=f"veh_trim_{case_id}")
        with c5:
            engine = st.text_input("Engine (opcional)", key=f"veh_engine_{case_id}")
        with c6:
            vehicle_type = st.text_input("Vehicle type (opcional)", key=f"veh_vtype_{case_id}")

        c7, c8, c9 = st.columns(3)
        with c7:
            body_class = st.text_input("Body class (opcional)", key=f"veh_body_{case_id}")
        with c8:
            plant_country = st.text_input("Plant country (opcional)", key=f"veh_plant_{case_id}")
        with c9:
            gvwr = st.text_input("GVWR (opcional)", key=f"veh_gvwr_{case_id}")

        # ✅ Quitamos curb weight (no UI)
        curb_weight_hidden = str(st.session_state.get(f"veh_curb_hidden_{case_id}", "") or "")

        weight_opt = st.text_input("Peso (opcional)", value="", key=f"veh_weight_{case_id}")
        description = st.text_area("Descripción (opcional)", value="", height=60, key=f"veh_desc_{case_id}")

        save_ok = st.checkbox("Confirmo que VIN + datos están listos para guardar", key=f"veh_save_ok_{case_id}")

        if st.button("Guardar vehículo", type="primary", disabled=not save_ok, key=f"save_vehicle_{case_id}"):
            try:
                if not vin_norm or len(vin_norm) != 17 or not is_valid_vin(vin_norm):
                    raise ValueError("VIN inválido. Debe tener 17 caracteres (sin I/O/Q).")

                add_vehicle(
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
                    curb_weight=curb_weight_hidden,  # se guarda si existe, pero no se muestra
                    weight=weight_opt,
                    value="0",
                    description=description,
                    source="vin_text",
                )

                st.success("Vehículo guardado correctamente.")

                # ✅ Reset seguro: sube nonce para que el input VIN sea “nuevo”
                st.session_state[vin_nonce_key] = int(st.session_state[vin_nonce_key]) + 1
                st.session_state[vin_decoded_key] = {}
                st.rerun()
            except Exception as e:
                st.error(f"Error guardando vehículo: {type(e).__name__}: {e}")

        st.markdown("#### Vehículos registrados")
        vehicles_df2 = list_vehicles(case_id=case_id).fillna("")
        if vehicles_df2.empty:
            st.info("Aún no hay vehículos.")
        else:
            vshow = vehicles_df2.copy().reset_index(drop=True)
            vshow.insert(0, "#", range(1, len(vshow) + 1))
            show_cols = [c for c in ["#", "vin", "brand", "model", "year", "gvwr", "weight", "created_at"] if c in vshow.columns]
            st.dataframe(vshow[show_cols], use_container_width=True, hide_index=True)

    with st.expander("📦 Artículos (agregar / ver)", expanded=True):
        st.caption("Dicta en formato continuo. Ejemplo:")
        st.code("tipo lavadora ref 440827 marca Sienna modelo Sleep4415 peso 95 lb estado usado cantidad 1 valor 120 parte_vehiculo no", language="text")

        dict_key = f"art_dict_{case_id}"
        st.session_state.setdefault(dict_key, "")

        dictation = st.text_area("Dictado", height=90, key=dict_key)
        parsed = _parse_article_dictation(dictation)

        if st.button("Aplicar dictado a campos", key=f"apply_art_{case_id}"):
            st.session_state[f"at_{case_id}"] = parsed.get("type", "") or ""
            st.session_state[f"ar_{case_id}"] = parsed.get("ref", "") or ""
            st.session_state[f"ab_{case_id}"] = parsed.get("brand", "") or ""
            st.session_state[f"am_{case_id}"] = parsed.get("model", "") or ""
            st.session_state[f"aw_{case_id}"] = parsed.get("weight", "") or ""
            st.session_state[f"ac_{case_id}"] = parsed.get("condition", "") or ""
            st.session_state[f"aq_{case_id}"] = int(parsed.get("quantity", 1) or 1)
            st.session_state[f"av_{case_id}"] = parsed.get("value", "") or ""
            st.session_state[f"ap_{case_id}"] = bool(parsed.get("is_vehicle_part", False))
            st.session_state[f"pv_{case_id}"] = normalize_vin(parsed.get("parent_vin", "") or "")
            st.success("Dictado aplicado.")

        with st.expander("🧪 Debug dictado", expanded=False):
            st.json(parsed)

        c1, c2, c3 = st.columns(3)
        with c1:
            item_type = st.text_input("Tipo", key=f"at_{case_id}")
        with c2:
            ref = st.text_input("Serie/Referencia", key=f"ar_{case_id}")
        with c3:
            brand = st.text_input("Marca", key=f"ab_{case_id}")

        c4, c5, c6 = st.columns(3)
        with c4:
            model = st.text_input("Modelo", key=f"am_{case_id}")
        with c5:
            weight = st.text_input("Peso (lb/kg)", key=f"aw_{case_id}")
        with c6:
            condition = st.text_input("Estado (nuevo/usado)", key=f"ac_{case_id}")

        c7, c8 = st.columns(2)
        with c7:
            quantity = st.number_input("Cantidad", min_value=1, step=1, value=int(st.session_state.get(f"aq_{case_id}", 1)), key=f"aq_{case_id}")
        with c8:
            value = st.text_input("Valor (opcional)", key=f"av_{case_id}")

        is_part = st.checkbox("¿Es parte del vehículo?", key=f"ap_{case_id}")
        parent_vin = ""
        if is_part:
            vins = []
            vdf = list_vehicles(case_id=case_id).fillna("")
            if not vdf.empty and "vin" in vdf.columns:
                vins = [x for x in vdf["vin"].tolist() if x]
            if vins:
                parent_vin = st.selectbox("VIN del vehículo al que pertenece", vins, key=f"pv_sel_{case_id}")
            else:
                parent_vin = st.text_input("VIN (si no hay vehículos aún)", key=f"pv_{case_id}")

        d = {
            "type": item_type, "ref": ref, "brand": brand, "model": model,
            "weight": weight, "condition": condition, "quantity": int(quantity),
            "value": value, "is_vehicle_part": bool(is_part), "parent_vin": parent_vin
        }
        desc_preview = _build_article_description(d)

        # ✅ Visible siempre
        st.text_area("Descripción (automática)", value=desc_preview, height=80, disabled=True)

        ok = st.checkbox("Confirmo que el artículo está correcto antes de guardar", key=f"art_ok_{case_id}")
        if st.button("Guardar artículo", type="primary", disabled=not ok, key=f"save_art_{case_id}"):
            try:
                add_article(
                    case_id=case_id,
                    item_type=item_type,
                    ref=ref,
                    brand=brand,
                    model=model,
                    weight=weight,
                    condition=condition,
                    quantity=int(quantity),
                    value=value,
                    is_vehicle_part=bool(is_part),
                    parent_vin=parent_vin,
                    description=desc_preview,
                    source="voice" if _safe(dictation) else "manual",
                )
                st.success("Artículo guardado correctamente.")

                st.session_state[dict_key] = ""
                st.session_state[f"at_{case_id}"] = ""
                st.session_state[f"ar_{case_id}"] = ""
                st.session_state[f"ab_{case_id}"] = ""
                st.session_state[f"am_{case_id}"] = ""
                st.session_state[f"aw_{case_id}"] = ""
                st.session_state[f"ac_{case_id}"] = ""
                st.session_state[f"aq_{case_id}"] = 1
                st.session_state[f"av_{case_id}"] = ""
                st.session_state[f"ap_{case_id}"] = False
                st.session_state[f"pv_{case_id}"] = ""
                st.rerun()
            except Exception as e:
                st.error(f"Error guardando artículo: {type(e).__name__}: {e}")

        st.markdown("#### Artículos registrados")
        adf2 = list_articles(case_id=case_id).fillna("")
        if adf2.empty:
            st.info("Aún no hay artículos.")
        else:
            ashow = adf2.copy().reset_index(drop=True)
            ashow.insert(0, "#", range(1, len(ashow) + 1))
            show_cols = [c for c in ["#", "seq", "description", "quantity", "weight", "value", "created_at"] if c in ashow.columns]
            st.dataframe(ashow[show_cols], use_container_width=True, hide_index=True)

    with st.expander("📎 Documentos del trámite (subir TODO aquí)", expanded=True):
        if not drive_folder_id:
            st.warning("Este trámite todavía no tiene carpeta en Drive.")
        else:
            d1, d2 = st.columns([1, 3])
            with d1:
                doc_type = st.selectbox("Tipo de documento", DOC_TYPES, key=f"doc_type_{case_id}")
            with d2:
                st.caption("Sube aquí ID cliente, títulos/facturas de vehículos, facturas artículos, etc.")

            files = st.file_uploader(
                "Subir documentos (varios)",
                type=["pdf", "jpg", "jpeg", "png"],
                accept_multiple_files=True,
                key=f"docs_upload_{case_id}",
            )

            if st.button("Subir documentos al trámite", type="primary", key=f"upload_docs_{case_id}"):
                try:
                    if not files:
                        st.warning("Selecciona archivos primero.")
                        st.stop()

                    for f in files:
                        up = upload_file_to_case_folder_via_script(
                            case_folder_id=drive_folder_id,
                            file_bytes=f.getvalue(),
                            file_name=f.name,
                            mime_type=f.type or "application/octet-stream",
                        )
                        add_document(
                            case_id=case_id,
                            drive_file_id=up.get("file_id", ""),
                            file_name=f.name,
                            doc_type=doc_type,
                        )

                    st.success(f"{len(files)} archivo(s) subido(s) y registrado(s).")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error subiendo documentos: {type(e).__name__}: {e}")

        st.markdown("#### Documentos registrados")
        ddf = list_documents(case_id).fillna("")
        if ddf.empty:
            st.info("Aún no hay documentos.")
        else:
            dshow = ddf.copy().reset_index(drop=True)
            dshow.insert(0, "#", range(1, len(dshow) + 1))
            show_cols = [c for c in ["#", "doc_type", "file_name", "uploaded_at"] if c in dshow.columns]
            st.dataframe(dshow[show_cols], use_container_width=True, hide_index=True)

    # (Validación + PDF + status) se mantiene igual por ahora (NO tocamos PDF)
    with st.expander("✅ Validación + Generar PDF + Marcar Pendiente", expanded=True):
        st.caption("Cuando todo esté completo (vehículos + artículos + documentos), genera el PDF y marca Pendiente.")

        vdf = list_vehicles(case_id=case_id).fillna("")
        adf = list_articles(case_id=case_id).fillna("")
        ddf = list_documents(case_id).fillna("")

        st.write(f"- Vehículos: {'✅' if not vdf.empty else '❌'}")
        st.write(f"- Artículos: {'✅' if not adf.empty else '❌'}")
        st.write(f"- Documentos: {'✅' if not ddf.empty else '❌'}")

        ready = st.checkbox("Confirmo que el trámite está completo y listo para enviar", key=f"ready_{case_id}")

        can_generate = ready and (not vdf.empty) and (not adf.empty) and (not ddf.empty) and bool(drive_folder_id)

        pdf_name = f"TR_{case_id}_{client_name}_RESUMEN_TRAMITE.pdf".replace(" ", "_")

        if st.button("Generar PDF y guardar en carpeta", type="primary", disabled=not can_generate, key=f"gen_pdf_{case_id}"):
            try:
                case_row = get_case(case_id) or {}
                pdf_bytes = build_case_summary_pdf_bytes(
                    case=case_row,
                    client_name=client_name,
                    vehicles_df=vdf,
                    articles_df=adf,
                    documents_df=ddf,
                )

                up = upload_file_to_case_folder_via_script(
                    case_folder_id=drive_folder_id,
                    file_bytes=pdf_bytes,
                    file_name=pdf_name,
                    mime_type="application/pdf",
                )

                add_document(
                    case_id=case_id,
                    drive_file_id=up.get("file_id", ""),
                    file_name=pdf_name,
                    doc_type="OTRO",
                )

                update_case_fields(case_id, {"status": "Pendiente", "updated_at": datetime.now().isoformat(timespec="seconds")})

                st.success("PDF generado + guardado en Drive y trámite marcado como Pendiente.")
                st.rerun()

            except Exception as e:
                st.error(f"Error generando PDF: {type(e).__name__}: {e}")
