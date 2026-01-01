import re
from datetime import datetime
import streamlit as st
import pandas as pd

from transit_core.gsheets_db import (
    list_clients,
    list_cases,
    create_case,
    get_case,
    get_client,
    list_items,
    add_vehicle_item,
    add_article_item,
)
from transit_core.drive_bridge import create_case_folder_via_script
from transit_core.ids import next_case_id


st.set_page_config(page_title="Trámites", layout="wide")
st.title("Trámites")


# -------------------------
# Helpers
# -------------------------
def _now_iso_utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _extract_vin_candidates(text: str) -> list[str]:
    """
    Extrae candidatos VIN (17 chars) de un texto, eliminando caracteres raros.
    """
    if not text:
        return []
    t = text.upper()
    t = re.sub(r"[^A-Z0-9]", "", t)
    # VIN: 17 chars, excluye I/O/Q normalmente; dejamos regex simple y luego validará tu validator
    cands = []
    for i in range(0, max(0, len(t) - 16)):
        chunk = t[i:i+17]
        if len(chunk) == 17:
            cands.append(chunk)
    # unique preserve order
    seen = set()
    out = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:10]


def _safe_import_vin_ocr():
    try:
        import transit_core.vin_ocr as vin_ocr  # type: ignore
        return vin_ocr
    except Exception:
        return None


def _safe_import_vin_decode():
    try:
        import transit_core.vin_decode as vin_decode  # type: ignore
        return vin_decode
    except Exception:
        return None


def _ocr_vin_from_image(file_bytes: bytes) -> dict:
    """
    Intenta extraer VIN usando vin_ocr.py.
    Devuelve: { "vin": str|None, "raw": str, "method": str }
    """
    vin_ocr = _safe_import_vin_ocr()
    if not vin_ocr:
        return {"vin": None, "raw": "", "method": "vin_ocr_not_available"}

    # Intentar distintas firmas comunes
    raw_text = ""
    vin = None

    try:
        # opción 1: extract_vin(image_bytes) -> (vin, raw_text) o dict
        if hasattr(vin_ocr, "extract_vin"):
            res = vin_ocr.extract_vin(file_bytes)  # type: ignore
            if isinstance(res, dict):
                vin = res.get("vin") or res.get("VIN")
                raw_text = res.get("raw") or res.get("text") or ""
            elif isinstance(res, (list, tuple)) and len(res) >= 1:
                vin = res[0]
                raw_text = res[1] if len(res) > 1 else ""
            else:
                vin = str(res) if res else None
            return {"vin": vin, "raw": raw_text, "method": "extract_vin"}
    except Exception as e:
        return {"vin": None, "raw": f"OCR error: {type(e).__name__}: {e}", "method": "extract_vin_error"}

    try:
        # opción 2: ocr_text(image_bytes) -> texto; luego extraemos VIN
        if hasattr(vin_ocr, "ocr_text"):
            raw_text = vin_ocr.ocr_text(file_bytes)  # type: ignore
            cands = _extract_vin_candidates(raw_text)
            vin = cands[0] if cands else None
            return {"vin": vin, "raw": raw_text, "method": "ocr_text"}
    except Exception as e:
        return {"vin": None, "raw": f"OCR error: {type(e).__name__}: {e}", "method": "ocr_text_error"}

    return {"vin": None, "raw": "", "method": "no_ocr_method_found"}


def _decode_vin(vin: str) -> dict:
    """
    Intenta decodificar VIN usando vin_decode.py.
    Devuelve dict con claves típicas: brand/make, model, year, etc.
    Si no está disponible, devuelve {}.
    """
    vin_decode = _safe_import_vin_decode()
    if not vin_decode:
        return {}

    try:
        # opción 1: decode_vin(vin) -> dict
        if hasattr(vin_decode, "decode_vin"):
            res = vin_decode.decode_vin(vin)  # type: ignore
            return res if isinstance(res, dict) else {}
    except Exception:
        pass

    try:
        # opción 2: vin_decode(vin) -> dict
        if hasattr(vin_decode, "vin_decode"):
            res = vin_decode.vin_decode(vin)  # type: ignore
            return res if isinstance(res, dict) else {}
    except Exception:
        pass

    return {}


def _parse_article_dictation(text: str) -> dict:
    """
    Parseo tolerante de dictado tipo:
    ref: 8891-AX | marca: Milwaukee | modelo: M18 | peso: 3.5 lb | estado: usado | cantidad: 2 | parte_vehiculo: si | vin: XXXXX

    Devuelve dict con campos.
    """
    t = (text or "").strip()
    if not t:
        return {}

    # separa por | o salto de línea
    parts = [p.strip() for p in re.split(r"\||\n", t) if p.strip()]
    out = {}
    for p in parts:
        if ":" in p:
            k, v = p.split(":", 1)
            k = _normalize_spaces(k).lower()
            v = _normalize_spaces(v)
            out[k] = v
        else:
            # si dictan sin "key:", lo metemos como description
            out.setdefault("descripcion", "")
            out["descripcion"] = (out["descripcion"] + " " + p).strip()

    # normalizar alias
    alias = {
        "marca": "brand",
        "brand": "brand",
        "modelo": "model",
        "model": "model",
        "peso": "weight",
        "weight": "weight",
        "valor": "value",
        "value": "value",
        "cantidad": "quantity",
        "qty": "quantity",
        "quantity": "quantity",
        "estado": "condition",
        "condicion": "condition",
        "condition": "condition",
        "ref": "ref",
        "referencia": "ref",
        "serie": "ref",
        "serial": "ref",
        "descripcion": "description",
        "description": "description",
        "parte_vehiculo": "is_vehicle_part",
        "parte del vehiculo": "is_vehicle_part",
        "es parte del vehiculo": "is_vehicle_part",
        "vin": "parent_vin",
    }

    normalized = {}
    for k, v in out.items():
        kk = alias.get(k, k)
        normalized[kk] = v

    # normalize booleans
    if "is_vehicle_part" in normalized:
        vv = normalized["is_vehicle_part"].strip().lower()
        normalized["is_vehicle_part"] = "yes" if vv in ["si", "sí", "s", "yes", "y", "true", "1"] else "no"

    # normalize quantity
    if "quantity" in normalized:
        q = re.sub(r"[^0-9]", "", normalized["quantity"])
        normalized["quantity"] = q or "1"

    return normalized


def _build_article_description(base_desc: str, ref: str, condition: str, is_vehicle_part: str, parent_vin: str) -> str:
    """
    Guardamos tags dentro de description sin tocar DB todavía.
    """
    tags = []
    if ref:
        tags.append(f"REF={ref}")
    if condition:
        tags.append(f"COND={condition}")
    if is_vehicle_part:
        tags.append(f"PART_OF_VEHICLE={is_vehicle_part}")
    if parent_vin:
        tags.append(f"PARENT_VIN={parent_vin}")

    tag_str = " | ".join(tags)
    base_desc = _normalize_spaces(base_desc)

    if tag_str and base_desc:
        return f"{base_desc}  [{tag_str}]"
    if tag_str and not base_desc:
        return f"[{tag_str}]"
    return base_desc


# -------------------------
# Crear trámite + carpeta
# -------------------------
st.subheader("Crear trámite")

clients_df = list_clients().fillna("")
if clients_df.empty:
    st.warning("No hay clientes. Crea uno primero en Clientes.")
    st.stop()

c1, c2, c3 = st.columns([2, 2, 3])

with c1:
    clients_df["label"] = clients_df["client_id"].astype(str) + " — " + clients_df["name"].astype(str)
    selected_label = st.selectbox("Cliente", clients_df["label"].tolist(), key="new_case_client")
    client_row = clients_df.loc[clients_df["label"] == selected_label].iloc[0]
    client_id = str(client_row["client_id"])
    client_name = str(client_row["name"]).strip()

with c2:
    origin = st.text_input("Origen", value="USA", key="new_case_origin")
    destination = st.text_input("Destino", value="Guatemala", key="new_case_destination")

with c3:
    notes = st.text_input("Notas (opcional)", value="", key="new_case_notes")

create_btn = st.button("Crear trámite", type="primary")

if create_btn:
    try:
        # pre-generar case_id
        cases_df_tmp = list_cases().fillna("")
        existing_ids = cases_df_tmp["case_id"].tolist() if "case_id" in cases_df_tmp.columns else []
        year = datetime.now().year
        case_id = next_case_id(existing_ids, year=year)

        # crear carpeta Drive con nombre humano
        root_folder_id = st.secrets["drive"]["root_folder_id"]
        folder_name = f"{case_id} - {client_name}".strip()

        res = create_case_folder_via_script(
            root_folder_id=root_folder_id,
            case_id=case_id,
            folder_name=folder_name,
        )
        drive_folder_id = res["folder_id"]

        # crear case en Sheets con drive_folder_id
        created_case_id = create_case(
            client_id=client_id,
            origin=_normalize_spaces(origin) or "USA",
            destination=_normalize_spaces(destination),
            notes=_normalize_spaces(notes),
            drive_folder_id=drive_folder_id,
        )

        st.success(f"Trámite creado: {created_case_id}")
        st.info(f"Carpeta Drive: {folder_name}")
        st.rerun()

    except Exception as e:
        st.error(f"Error creando trámite: {type(e).__name__}: {e}")

st.divider()


# -------------------------
# Seleccionar trámite para trabajar
# -------------------------
st.subheader("Trabajar un trámite")

cases_df = list_cases().fillna("")
if cases_df.empty:
    st.info("No hay trámites aún.")
    st.stop()

# Si no existe client_name en cases, lo construimos con join
if "client_id" in cases_df.columns:
    clients_map = dict(zip(clients_df["client_id"], clients_df["name"]))
    cases_df["client_name"] = cases_df["client_id"].map(clients_map).fillna("")

cases_df["case_label"] = cases_df["case_id"].astype(str) + " — " + cases_df.get("client_name", "").astype(str)
selected_case_label = st.selectbox("Selecciona un trámite", cases_df["case_label"].tolist(), key="selected_case")

selected_case_id = cases_df.loc[cases_df["case_label"] == selected_case_label, "case_id"].iloc[0]
case_id = str(selected_case_id)

case = get_case(case_id)
if not case:
    st.error("No se pudo cargar el trámite.")
    st.stop()

client = get_client(case.get("client_id", "")) or {}
drive_folder_id = case.get("drive_folder_id", "")

top1, top2, top3, top4 = st.columns([2, 2, 2, 4])
with top1:
    st.write(f"**Trámite:** {case_id}")
with top2:
    st.write(f"**Cliente:** {client.get('name','')}")
with top3:
    st.write(f"**Destino:** {case.get('destination','')}")
with top4:
    st.write(f"**Drive folder id:** {drive_folder_id}")

st.divider()


# -------------------------
# Items del trámite
# -------------------------
st.subheader("Items del trámite (Vehículos y Artículos)")

items_df = list_items(case_id=case_id).fillna("") if case_id else pd.DataFrame()

if items_df.empty:
    st.info("Aún no hay vehículos ni artículos en este trámite.")
else:
    # orden y columnas
    show_cols = [c for c in ["item_id","item_type","unique_key","brand","model","year","description","quantity","weight","value","created_at"] if c in items_df.columns]
    st.dataframe(items_df[show_cols], use_container_width=True)

st.divider()


# -------------------------
# Agregar vehículo (foto VIN + validación)
# -------------------------
st.subheader("Agregar vehículo (Foto del VIN)")

vcol1, vcol2 = st.columns([2, 3])

with vcol1:
    vin_photo = st.file_uploader("Sube foto del VIN (o toma foto desde el celular)", type=["png","jpg","jpeg"], key="vin_photo")
    ocr_btn = st.button("Extraer VIN de la foto", type="secondary")

with vcol2:
    st.caption("Flujo: Foto → OCR → Validar/Editar → Decodificar → Confirmar → Guardar (VIN no se puede repetir).")

if "vin_ocr_result" not in st.session_state:
    st.session_state["vin_ocr_result"] = {"vin": "", "raw": "", "method": ""}

if ocr_btn:
    if not vin_photo:
        st.warning("Primero sube una foto del VIN.")
    else:
        file_bytes = vin_photo.read()
        result = _ocr_vin_from_image(file_bytes)
        st.session_state["vin_ocr_result"] = {
            "vin": (result.get("vin") or "") if result else "",
            "raw": result.get("raw") or "",
            "method": result.get("method") or "",
        }

ocr_state = st.session_state.get("vin_ocr_result", {})
vin_guess = (ocr_state.get("vin") or "").strip().upper()

vin_input = st.text_input("VIN detectado / VIN manual", value=vin_guess, key="vin_input").strip().upper()

decode_btn = st.button("Decodificar VIN", type="secondary")

decoded = {}
if decode_btn and vin_input:
    decoded = _decode_vin(vin_input)
    st.session_state["vin_decoded"] = decoded
else:
    decoded = st.session_state.get("vin_decoded", {}) if vin_input else {}

# Prefill con decoded (si existe)
brand_pref = decoded.get("brand") or decoded.get("make") or decoded.get("Marca") or ""
model_pref = decoded.get("model") or decoded.get("Modelo") or ""
year_pref = str(decoded.get("year") or decoded.get("Año") or "")

f1, f2, f3 = st.columns(3)
with f1:
    v_brand = st.text_input("Marca", value=str(brand_pref), key="v_brand")
with f2:
    v_model = st.text_input("Modelo", value=str(model_pref), key="v_model")
with f3:
    v_year = st.text_input("Año", value=str(year_pref), key="v_year")

f4, f5, f6 = st.columns(3)
with f4:
    v_qty = st.number_input("Cantidad", min_value=1, value=1, step=1, key="v_qty")
with f5:
    v_weight = st.text_input("Peso (texto libre: lb/kg)", value="", key="v_weight")
with f6:
    v_value = st.text_input("Valor (texto libre)", value="", key="v_value")

v_desc = st.text_area("Descripción (opcional)", value="", height=80, key="v_desc")

confirm_vehicle = st.checkbox("Confirmo que el VIN y la información del vehículo son correctos", key="confirm_vehicle")

save_vehicle_btn = st.button("Guardar vehículo en el trámite", type="primary")

if save_vehicle_btn:
    try:
        if not vin_input:
            raise ValueError("VIN requerido.")
        if not confirm_vehicle:
            raise ValueError("Debes confirmar la información antes de guardar.")

        item_id = add_vehicle_item(
            case_id=case_id,
            vin=vin_input,
            brand=_normalize_spaces(v_brand),
            model=_normalize_spaces(v_model),
            year=_normalize_spaces(v_year),
            description=_normalize_spaces(v_desc),
            quantity=int(v_qty),
            weight=_normalize_spaces(v_weight),
            value=_normalize_spaces(v_value),
            source="vin_photo" if vin_photo else "manual",
        )

        st.success(f"Vehículo guardado. Item ID: {item_id}")
        st.session_state["vin_ocr_result"] = {"vin": "", "raw": "", "method": ""}
        st.session_state["vin_decoded"] = {}
        st.rerun()

    except Exception as e:
        st.error(f"No se pudo guardar vehículo: {type(e).__name__}: {e}")

# Mostrar raw OCR para auditoría
if ocr_state.get("raw"):
    with st.expander("Ver texto OCR (debug)"):
        st.write(f"Método: {ocr_state.get('method')}")
        st.text(ocr_state.get("raw", ""))

st.divider()


# -------------------------
# Agregar artículo (dictado + validación)
# -------------------------
st.subheader("Agregar artículo (Dictado por voz o manual)")

# Lista VINs del trámite para asociar piezas
vin_list = []
if not items_df.empty and "item_type" in items_df.columns:
    vin_list = items_df.loc[items_df["item_type"] == "vehicle", "unique_key"].astype(str).tolist()

st.caption(
    "Tip: en el celular usa el mic del teclado para dictar en la caja de texto.\n"
    "Formato sugerido:\n"
    "ref: 8891-AX | marca: Milwaukee | modelo: M18 | peso: 3.5 lb | estado: usado | cantidad: 2 | parte_vehiculo: si | vin: 1HG... "
)

dictation_text = st.text_area("Dictado (texto)", value="", height=90, key="article_dictation")
parse_btn = st.button("Parsear dictado", type="secondary")

if "article_parsed" not in st.session_state:
    st.session_state["article_parsed"] = {}

if parse_btn:
    st.session_state["article_parsed"] = _parse_article_dictation(dictation_text)

parsed = st.session_state.get("article_parsed", {}) or {}

a1, a2, a3 = st.columns(3)
with a1:
    a_ref = st.text_input("Referencia/Serie (opcional)", value=parsed.get("ref", ""), key="a_ref")
with a2:
    a_brand = st.text_input("Marca", value=parsed.get("brand", ""), key="a_brand")
with a3:
    a_model = st.text_input("Modelo", value=parsed.get("model", ""), key="a_model")

b1, b2, b3 = st.columns(3)
with b1:
    a_qty = st.number_input("Cantidad", min_value=1, value=int(parsed.get("quantity", "1") or 1), step=1, key="a_qty")
with b2:
    a_weight = st.text_input("Peso (texto libre: lb/kg)", value=parsed.get("weight", ""), key="a_weight")
with b3:
    a_value = st.text_input("Valor (texto libre)", value=parsed.get("value", ""), key="a_value")

c1, c2, c3 = st.columns(3)
with c1:
    a_condition = st.selectbox("Estado", options=["", "nuevo", "usado"], index=0, key="a_condition")
with c2:
    a_is_part = st.selectbox("¿Es parte del vehículo?", options=["no", "yes"], index=0, key="a_is_part")
with c3:
    # si es parte del vehículo, selecciona VIN padre
    parent_vin_default = parsed.get("parent_vin", "")
    parent_vin = st.selectbox("VIN padre (si aplica)", options=[""] + vin_list, index=0, key="a_parent_vin")
    if parent_vin_default and parent_vin_default in vin_list:
        parent_vin = parent_vin_default

a_desc = st.text_area("Descripción del artículo", value=parsed.get("description", ""), height=80, key="a_desc")

confirm_article = st.checkbox("Confirmo que la información del artículo es correcta", key="confirm_article")
save_article_btn = st.button("Guardar artículo en el trámite", type="primary")

if save_article_btn:
    try:
        if not confirm_article:
            raise ValueError("Debes confirmar la información antes de guardar.")
        if not _normalize_spaces(a_desc) and not _normalize_spaces(a_ref):
            raise ValueError("Debes incluir al menos una descripción o una referencia/serie.")

        # tags dentro de description (sin tocar DB aún)
        desc_final = _build_article_description(
            base_desc=a_desc,
            ref=a_ref,
            condition=a_condition,
            is_vehicle_part=a_is_part,
            parent_vin=(parent_vin if a_is_part == "yes" else ""),
        )

        item_id = add_article_item(
            case_id=case_id,
            description=desc_final,
            brand=_normalize_spaces(a_brand),
            model=_normalize_spaces(a_model),
            quantity=int(a_qty),
            weight=_normalize_spaces(a_weight),
            value=_normalize_spaces(a_value),
            source="voice_dictation" if dictation_text.strip() else "manual",
        )

        st.success(f"Artículo guardado. Item ID: {item_id}")
        st.session_state["article_parsed"] = {}
        st.rerun()

    except Exception as e:
        st.error(f"No se pudo guardar artículo: {type(e).__name__}: {e}")
