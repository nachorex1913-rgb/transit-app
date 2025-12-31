# transit_core/ids.py
from __future__ import annotations
from datetime import datetime
import re

CASE_RE = re.compile(r"^TR-(\d{4})-(\d{6})$")

def normalize_name_for_folder(name: str) -> str:
    # limpio para nombre de carpeta Drive
    safe = re.sub(r"[^a-zA-Z0-9\s_-]", "", name or "").strip()
    safe = re.sub(r"\s+", "-", safe)
    return safe[:60] if safe else "CLIENTE"

def next_case_id(existing_case_ids: list[str], year: int | None = None) -> str:
    """Devuelve siguiente TR-YYYY-000001 basado en los existentes en el Sheet."""
    y = year or datetime.now().year
    max_n = 0
    for cid in existing_case_ids:
        m = CASE_RE.match(str(cid).strip())
        if not m:
            continue
        yy = int(m.group(1))
        nn = int(m.group(2))
        if yy == y and nn > max_n:
            max_n = nn
    return f"TR-{y}-{max_n+1:06d}"

def next_article_seq(existing_unique_keys: list[str], case_id: str) -> str:
    """
    Genera consecutivo por trámite para artículos:
    A-<case_id>-0001
    """
    prefix = f"A-{case_id}-"
    max_n = 0
    for k in existing_unique_keys:
        s = str(k).strip()
        if not s.startswith(prefix):
            continue
        tail = s.replace(prefix, "")
        if tail.isdigit():
            max_n = max(max_n, int(tail))
    return f"{prefix}{max_n+1:04d}"

def next_item_id(existing_item_ids: list[str]) -> str:
    """
    Item IDs globales:
    IT-0000001
    """
    max_n = 0
    for iid in existing_item_ids:
        s = str(iid).strip()
        if s.startswith("IT-") and s[3:].isdigit():
            max_n = max(max_n, int(s[3:]))
    return f"IT-{max_n+1:07d}"

def next_doc_id(existing_doc_ids: list[str]) -> str:
    max_n = 0
    for did in existing_doc_ids:
        s = str(did).strip()
        if s.startswith("DOC-") and s[4:].isdigit():
            max_n = max(max_n, int(s[4:]))
    return f"DOC-{max_n+1:07d}"

