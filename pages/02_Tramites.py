decode_btn = st.button(
    "Decodificar VIN",
    key=f"decode_btn_{case_id}",
    disabled=(not vin_input_norm or len(vin_input_norm) != 17),
)

# SIEMPRE existe decoded
decoded = st.session_state.get(vin_decoded_key, {}) or {}

if decode_btn:
    out = decode_vin(vin_input_norm) or {}

    # ✅ Si hay error, NO success
    if out.get("error"):
        st.warning(out["error"])
        st.session_state[vin_decoded_key] = {}
        decoded = {}
    else:
        st.session_state[vin_decoded_key] = out
        decoded = out

        # ✅ success SOLO si vino algo útil
        if (decoded.get("brand") or "").strip() or (decoded.get("model") or "").strip() or (decoded.get("year") or "").strip():
            st.success("VIN decodificado correctamente.")
        else:
            st.warning("Se consultó el decoder pero no devolvió datos útiles. Ingresa manual.")
