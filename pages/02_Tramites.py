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

ocr_state = st.session_sta
