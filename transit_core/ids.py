# transit_core/ids.py
from __future__ import annotations

import re
from typing import List


def next_case_id(existing_case_ids: List[str], year: int) -> str:
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


def next_vehicle_id(existing_ids: List[str]) -> str:
    mx = 0
    pat = re.compile(r"^VH-\d{6}$")
    for x in existing_ids or []:
        s = str(x).strip()
        if pat.match(s):
            try:
                mx = max(mx, int(s.split("-")[-1]))
            except Exception:
                pass
    return f"VH-{mx+1:06d}"


def next_article_id(existing_ids: List[str]) -> str:
    mx = 0
    pat = re.compile(r"^AR-\d{6}$")
    for x in existing_ids or []:
        s = str(x).strip()
        if pat.match(s):
            try:
                mx = max(mx, int(s.split("-")[-1]))
            except Exception:
                pass
    return f"AR-{mx+1:06d}"


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
