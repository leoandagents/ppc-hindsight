"""Turn an Amazon Ads console *Targeting* report (unified reporting, CSV) into normalized rows.

The console localizes column headers, so we match by alias. Add a new alias when a
locale is missing; never hard-code positions.

Normalized row keys:
    date (YYYY-MM-DD), campaign_id, campaign_name, ad_group_id, ad_group_name,
    target_id, targeting, match_type, bid, status, impressions, clicks, spend, sales, orders
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from pathlib import Path
from typing import Iterable

ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "日期", "day", "start date"),
    "campaign_id": ("campaign id", "广告活动编号", "campaignid"),
    "campaign_name": ("campaign name", "广告活动名称", "campaign"),
    "ad_group_id": ("ad group id", "广告组编号", "adgroupid"),
    "ad_group_name": ("ad group name", "广告组名称", "ad group"),
    "target_id": ("targeting id", "target id", "投放方案编号", "keyword id", "targetid", "keywordid"),
    "targeting": ("targeting", "投放方案", "keyword", "keyword text", "targeting expression"),
    "match_type": ("match type", "targeting match type", "投放匹配类型", "投放匹配类型-targeting match type", "匹配类型"),
    "bid": ("target bid", "目标竞价", "bid", "keyword bid", "竞价"),
    "status": ("targeting status", "投放状态", "status", "keyword status"),
    "impressions": ("impressions", "展示量", "impr"),
    "clicks": ("clicks", "点击量"),
    "spend": ("spend", "总成本", "cost", "total cost", "花费"),
    "sales": ("sales", "销售额", "14 day total sales", "7 day total sales", "total sales", "purchases sales", "销售额（总）"),
    "orders": ("purchases", "购买量", "orders", "14 day total orders (#)", "7 day total orders (#)", "total orders", "订单量"),
}

REQUIRED = ("date", "targeting", "clicks", "spend", "sales", "orders")

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def _norm_header(h: str) -> str:
    h = h.strip().lstrip("﻿").lower()
    h = re.sub(r"\s+", " ", h)
    return h


def _num(v) -> float:
    if v is None:
        return 0.0
    s = str(v).strip()
    if not s or s in ("—", "-", "–"):
        return 0.0
    # strip currency symbols and thousands separators ("$1,234.56", "US$12", "1.234,56" is NOT handled)
    s = s.replace(",", "")
    m = _NUM.search(s)
    return float(m.group()) if m else 0.0


_EXCEL_WRAP = re.compile(r'^="?(.*?)"?$')


def _txt(v) -> str:
    """Strip Excel's ="..." wrapper the console puts around long numeric IDs."""
    s = str(v or "").strip()
    if s.startswith("="):
        s = _EXCEL_WRAP.sub(r"\1", s)
    return s.strip()


def _date(v) -> str | None:
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y年%m月%d日", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s[:19] if "T" in s else s, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def map_headers(headers: list[str]) -> dict[str, int]:
    """Return {normalized_key: column_index}. Raises if a required column is missing."""
    normed = [_norm_header(h) for h in headers]
    out: dict[str, int] = {}
    for key, aliases in ALIASES.items():
        for a in aliases:
            if a in normed:
                out[key] = normed.index(a)
                break
    missing = [k for k in REQUIRED if k not in out]
    if missing:
        raise ValueError(
            f"Missing required columns {missing}. Headers seen: {headers}. "
            "Export the *Targeting* template from Amazon Ads > Reports with the 'Date' dimension added "
            "(and 'Date range' removed), or add an alias in ppc_hindsight/normalize.py."
        )
    return out


def read_targeting_csv(path: str | Path) -> list[dict]:
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    # Amazon sometimes prefixes a title/metadata line before the header; find the header row.
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines[:10]):
        if sum(1 for a in ("date", "日期", "clicks", "点击量", "spend", "总成本") if a in line.lower()) >= 2:
            start = i
            break
    reader = csv.reader(io.StringIO("\n".join(lines[start:])))
    headers = next(reader)
    idx = map_headers(headers)
    rows: list[dict] = []
    for rec in reader:
        if not rec or all(not c.strip() for c in rec):
            continue
        g = lambda k: _txt(rec[idx[k]]) if k in idx and idx[k] < len(rec) else ""  # noqa: E731
        day = _date(g("date"))
        if not day:
            continue
        targeting = g("targeting").strip()
        if not targeting:
            continue
        match = g("match_type").strip() or "UNKNOWN"
        tid = g("target_id").strip() or f"{g('campaign_name').strip()}|{g('ad_group_name').strip()}|{targeting}|{match}"
        rows.append({
            "date": day,
            "campaign_id": g("campaign_id").strip(),
            "campaign_name": g("campaign_name").strip(),
            "ad_group_id": g("ad_group_id").strip(),
            "ad_group_name": g("ad_group_name").strip(),
            "target_id": tid,
            "targeting": targeting,
            "match_type": match,
            "bid": _num(g("bid")) if "bid" in idx else None,
            "status": g("status").strip() if "status" in idx else "",
            "impressions": _num(g("impressions")),
            "clicks": _num(g("clicks")),
            "spend": _num(g("spend")),
            "sales": _num(g("sales")),
            "orders": _num(g("orders")),
        })
    return rows


def summarize_input(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    days = sorted({r["date"] for r in rows})
    return {
        "rows": len(rows),
        "targets": len({r["target_id"] for r in rows}),
        "campaigns": len({r["campaign_name"] or r["campaign_id"] for r in rows}),
        "date_from": days[0] if days else None,
        "date_to": days[-1] if days else None,
        "spend": round(sum(r["spend"] for r in rows), 2),
        "has_bid": any(r["bid"] is not None for r in rows),
        "has_status": any(r["status"] for r in rows),
    }
