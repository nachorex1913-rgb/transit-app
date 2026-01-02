# transit_core/ids.py
from __future__ import annotations

import re
from typing import List


def next_case_id(existing_case_ids: List[str], year: int) -> str:
    """
    TR-YYYY-000001
    contador anual
    """
    mx = 0
    pat = re.compile(rf"^TR-{year}-\d{{6}}$")
    for cid in existing_case_ids or []:
        cid = str(cid).strip()
        if pat.match(cid):
            try:
                mx = max(mx, int(cid.split("-")[-1]))
            except Exception:
                pass
    return f"TR-{year}-{mx+1:06d}"


def next_item_id(existing_item_ids: List[str]) -> str:
    """
    ITEM-000001 global
    """
    mx = 0
    pat = re.compile(r"^IT-\d{6}$")
    for x in existing_item_ids or []:
        s = str(x).strip()
        if pat.match(s):
            try:
                mx = max(mx, int(s.split("-")[-1]))
            except Exception:
                pass
    return f"IT-{mx+1:06d}"


def next_doc_id(existing_doc_ids: List[str]) -> str:
    mx = 0
    pat = re.compile(r"^DC-\d{6}$")
    for x in existing_doc_ids or []:
        s = str(x).strip()
        if pat.match(s):
            try:
                mx = max(mx, int(s.split("-")[-1]))
            except Exception:
                pass
    return f"DC-{mx+1:06d}"


def next_article_seq(existing_keys_case: List[str], case_id: str) -> str:
    """
    A-<CASE_ID>-0001  (por trámite)
    """
    mx = 0
    pat = re.compile(rf"^A-{re.escape(case_id)}-\d{{4}}$")
    for k in existing_keys_case or []:
        s = str(k).strip()
        if pat.match(s):
            try:
                mx = max(mx, int(s.split("-")[-1]))
            except Exception:
                pass
    return f"A-{case_id}-{mx+1:04d}"
