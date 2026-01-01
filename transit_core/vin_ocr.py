def extract_vin_from_image(image_bytes: bytes) -> dict:
    """
    Return:
      {
        "vin": "1HGCM82633A004352" or "",
        "confidence": 0.0-1.0,
        "raw_text": "... opcional ..."
      }
    """

