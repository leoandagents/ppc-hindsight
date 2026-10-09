"""Smoke tests on a hand-built dataset. Run: python -X utf8 -m pytest -q  (or python -X utf8 tests/test_audit.py)"""
from __future__ import annotations

import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ppc_hindsight.audit import audit, load_yardstick  # noqa: E402
from ppc_hindsight.normalize import read_targeting_csv  # noqa: E402
from ppc_hindsight.report import render  # noqa: E402

D0 = dt.date(2026, 6, 1)


def _rows():
    """Three targets over 40 days.
    bleeder : 2 clicks/day, $1/day, never an order -> evidence from day 14 on (>=15 clicks, >=$10), never stops.
    cutter  : same, but spend goes to 0 from day 24 -> episode ends 'stopped'.
    healthy : 1 order/day at $1 spend, $10 sales -> never evidence.
    """
    rows = []
    for i in range(40):
        day = (D0 + dt.timedelta(days=i)).isoformat()
        rows.append(dict(date=day, campaign_id="c1", campaign_name="C1", ad_group_id="g", ad_group_name="G", target_id="k-bleed",
                         targeting="bleeder", match_type="BROAD", bid=0.5, status="ENABLED", impressions=100, clicks=2, spend=1.0, sales=0.0, orders=0))
        cut = i >= 24
        rows.append(dict(date=day, campaign_id="c1", campaign_name="C1", ad_group_id="g", ad_group_name="G", target_id="k-cut",
                         targeting="cutter", match_type="EXACT", bid=0.5, status="PAUSED", impressions=100, clicks=0 if cut else 2,
                         spend=0.0 if cut else 1.0, sales=0.0, orders=0))
        rows.append(dict(date=day, campaign_id="c1", campaign_name="C1", ad_group_id="g", ad_group_name="G", target_id="k-ok",
                         targeting="healthy", match_type="EXACT", bid=0.5, status="ENABLED", impressions=100, clicks=5, spend=1.0, sales=10.0, orders=1))
    return rows


def test_episodes_and_waste():
    y = load_yardstick()
    res = audit(_rows(), y, unsettled_days=2)
    by = {t["target_id"]: t for t in res["targets"]}
    assert "k-ok" in by and by["k-ok"]["episodes"] == 0 and by["k-ok"]["waste"] == 0
    # bleeder: evidence from index 13 (14 days * 2 clicks = 28 >= 15, spend 14 >= 10) ... actually from day index 7 (8 days: 16 clicks, $8 <10) -> index 9 ($10)
    b = by["k-bleed"]
    assert b["episodes"] == 1 and b["ongoing"] is True
    ep = [e for e in res["episodes"] if e["target_id"] == "k-bleed"][0]
    assert ep["start"] == 9, ep  # 10 days * $1 = $10 and 20 clicks
    assert ep["end_reason"] == "ongoing"
    assert ep["waste"] == ep["spend"] - 2.0  # grace = 2 days at $1
    c = by["k-cut"]
    epc = [e for e in res["episodes"] if e["target_id"] == "k-cut"][0]
    assert epc["end_reason"] == "stopped" and c["ongoing"] is False
    assert res["totals"]["ongoing_targets"] == 1
    assert 0 < res["totals"]["waste_share"] < 1


def test_score_from_clips_waste():
    y = load_yardstick()
    full = audit(_rows(), y, unsettled_days=2)
    part = audit(_rows(), y, unsettled_days=2, score_from=(D0 + dt.timedelta(days=20)).isoformat())
    assert part["totals"]["waste"] < full["totals"]["waste"]
    assert part["totals"]["spend"] < full["totals"]["spend"]


def test_csv_roundtrip_chinese_headers(tmp_path=None):
    tmp = Path(tmp_path) if tmp_path else Path(__file__).with_name("_tmp.csv")
    hdr = ["日期", "广告活动编号", "广告活动名称", "广告组编号", "广告组名称", "投放方案", "投放匹配类型-Targeting match type",
           "投放方案编号", "目标竞价", "投放状态", "展示量", "点击量", "总成本", "购买量", "销售额"]
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for r in _rows():
            w.writerow([r["date"], r["campaign_id"], r["campaign_name"], r["ad_group_id"], r["ad_group_name"], r["targeting"], r["match_type"],
                        r["target_id"], f"${r['bid']}", r["status"], r["impressions"], r["clicks"], f"${r['spend']}", r["orders"], f"${r['sales']}"])
    rows = read_targeting_csv(tmp)
    assert len(rows) == 120 and rows[0]["bid"] == 0.5 and rows[0]["spend"] == 1.0
    res = audit(rows, load_yardstick())
    md = render(res)
    assert "Still bleeding" in md and "bleeder" in md
    if not tmp_path:
        tmp.unlink()


if __name__ == "__main__":
    test_episodes_and_waste()
    test_score_from_clips_waste()
    test_csv_roundtrip_chinese_headers()
    print("ok")
