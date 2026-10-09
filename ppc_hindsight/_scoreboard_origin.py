"""Score an engine's decisions against the golden dataset.

    python -X utf8 scripts/scoreboard.py --golden baseline/golden_2026-04-12_2026-09-18 \
        --engine old --out baseline/old_engine.json

For the OLD engine the decisions are what it actually did (change_log). For a new
engine, point --decisions at a JSONL of replayed decisions with the same shape as
change_log rows (action_type, entity_type, entity_id, old_value, new_value,
executed_at, success). Everything else (yardstick, golden data) is identical, so
the two scores are comparable.

Tier 2 (decision quality, deterministic):
  waste_share        spend on keyword-days that were evidence-sufficient for a cut
                     (beyond a grace period) before any cut action landed / total keyword spend
  detection_latency  days from first evidence-sufficient day to first cut action, per episode
  false_kill_rate    bid cuts followed by orders at <= target ACOS in the next 14d;
                     pauses/negatives on keywords with a profitable 90d lifecycle
  churn_rate         bid adjustments reversed within N days / all bid adjustments
  blind_spot_share   evidence-sufficient spend that none of the engine's cut rules could reach
Tier 1 (outcome, seasonal, reference only): per settled 14-day window series.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CONFIG_DIR, ROOT, d, dumps, read_jsonl  # noqa: E402

MONEY = re.compile(r"-?\d+(?:\.\d+)?")


def money(s) -> float | None:
    if s is None:
        return None
    m = MONEY.search(str(s))
    return float(m.group()) if m else None


class Series:
    """Per-entity daily metrics with O(1) trailing/forward window sums via prefix arrays."""

    def __init__(self, day0: dt.date, ndays: int):
        self.day0, self.n = day0, ndays
        self.rows: dict[str, list[list[float]]] = {}  # entity -> [[impr,clicks,cost,sales,orders] per day]
        self.prefix: dict[str, list[list[float]]] = {}

    def idx(self, day: dt.date) -> int:
        return (day - self.day0).days

    def add(self, entity: str, day: dt.date, impr, clicks, cost, sales, orders):
        i = self.idx(day)
        if not (0 <= i < self.n):
            return
        arr = self.rows.setdefault(entity, [[0.0] * 5 for _ in range(self.n)])
        for k, v in enumerate((impr, clicks, cost, sales, orders)):
            arr[i][k] += float(v or 0)

    def finalize(self):
        for e, arr in self.rows.items():
            pre = [[0.0] * 5]
            for row in arr:
                pre.append([pre[-1][k] + row[k] for k in range(5)])
            self.prefix[e] = pre

    def window(self, entity: str, start_i: int, end_i: int) -> list[float]:
        """Sum over day indexes [start_i, end_i] inclusive, clipped to range."""
        pre = self.prefix.get(entity)
        if pre is None:
            return [0.0] * 5
        s, e = max(0, start_i), min(self.n - 1, end_i)
        if e < s:
            return [0.0] * 5
        return [pre[e + 1][k] - pre[s][k] for k in range(5)]


def acos_of(w: list[float]) -> float | None:
    return (w[2] / w[3]) if w[3] > 0 else None


def load_rules(golden: Path) -> dict[str, dict]:
    out = {}
    for r in read_jsonl(golden / "rules.jsonl"):
        cond = r["conditions"]
        out[r["rule_key"]] = {"enabled": bool(r["is_enabled"]), "cond": json.loads(cond) if isinstance(cond, str) else cond}
    return out


def old_engine_can_reach(rules: dict, w7: list[float], w14: list[float], is_target: bool, target_acos: float) -> bool:
    """Would any of the OLD engine's cut rules match this keyword-day? (thresholds read from ads_rules)."""
    def c(key, default):
        r = rules.get(key)
        return (r["cond"] if r and r["enabled"] else None), default
    a7 = acos_of(w7)
    cond, _ = c("no_conversion_cut", None)
    if cond and w7[1] >= cond.get("min_clicks_7d", 20) and w7[2] >= cond.get("min_spend_7d", 8) and w7[4] <= cond.get("max_orders_7d", 0):
        return True
    cond, _ = c("bleeding_keyword_cut", None)
    if cond and w7[2] >= cond.get("min_spend_7d", 10) and a7 is not None and a7 > cond.get("acos_ratio_min", 1.3) * target_acos:
        return True
    cond, _ = c("neg_keyword_high_click_no_sale", None)
    if cond and w7[1] >= cond.get("min_clicks_7d", 40) and w7[2] >= cond.get("min_spend_7d", 20) and w7[4] <= cond.get("max_orders_7d", 0):
        return True
    cond, _ = c("pause_wasted_keyword", None)
    if cond and w14[1] >= cond.get("min_clicks_14d", 40) and w14[2] >= cond.get("min_spend_14d", 25) and w14[4] <= cond.get("max_orders_14d", 0):
        return True
    if is_target:
        cond, _ = c("pause_auto_target_no_sale", None)
        a14 = acos_of(w14)
        if cond and w14[1] >= cond.get("min_clicks_14d", 30) and w14[2] >= cond.get("min_spend_14d", 8) and (w14[4] == 0 or (a14 is not None and a14 * 100 > cond.get("max_acos_pct", 80))):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--engine", default="old")
    ap.add_argument("--decisions", help="JSONL of decisions for a non-old engine (change_log shape)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--reach", default="old", choices=["old", "new"], help="whose cut rules define the blind spot: old = ads_rules thresholds, new = NewEngine.can_reach")
    ap.add_argument("--campaigns", help="comma-separated campaign ids: score only keywords in these campaigns (A/B arm)")
    ap.add_argument("--from", dest="score_from", help="score only days >= this (episodes, waste, actions); default golden start")
    ap.add_argument("--to", dest="score_to", help="score only days <= this; default golden end")
    a = ap.parse_args()
    golden = (ROOT / a.golden) if not Path(a.golden).is_absolute() else Path(a.golden)
    manifest = json.loads((golden / "manifest.json").read_text(encoding="utf-8"))
    Y = json.loads((CONFIG_DIR / "yardstick.json").read_text(encoding="utf-8"))
    day0, day_end = d(manifest["date_from"]), d(manifest["date_to"])
    ndays = (day_end - day0).days + 1
    W = Y["cut_evidence"]["window_days"]; G = Y["grace_days"]; T = Y["target_acos"]
    FWD = Y["false_kill"]["forward_days"]
    lo = (d(a.score_from) - day0).days if a.score_from else 0
    hi = (d(a.score_to) - day0).days if a.score_to else ndays - 1
    lo, hi = max(0, lo), min(ndays - 1, hi)

    # ---- keyword-level series ----
    kw_rows = read_jsonl(golden / "keyword_daily_metrics.jsonl")
    S = Series(day0, ndays)
    meta: dict[str, dict] = {}
    for r in kw_rows:
        e = str(r["keyword_id"])
        S.add(e, d(r["report_date"]), r["impressions"], r["clicks"], r["cost"], r["sales"], r["orders"])
        meta.setdefault(e, {"text": r.get("keyword_text") or r.get("targeting"), "match": r.get("match_type"), "campaign_id": str(r["campaign_id"])})
    S.finalize()
    if a.campaigns:
        keep = {c.strip() for c in a.campaigns.split(",") if c.strip()}
        drop = [e for e, m in meta.items() if m["campaign_id"] not in keep]
        for e in drop:
            S.prefix.pop(e, None); S.rows.pop(e, None); meta.pop(e, None)
    total_kw_spend = None  # set after the scoring range is known

    # ---- decisions ----
    if a.engine == "old":
        decisions = read_jsonl(golden / "change_log.jsonl")
    else:
        decisions = read_jsonl(Path(a.decisions))
    decisions = [x for x in decisions if int(x.get("success", 1)) == 1]
    acts: dict[str, list[dict]] = defaultdict(list)
    for x in decisions:
        if x["entity_type"] not in ("keyword", "target"):
            continue
        day = d(x["executed_at"])
        kind = None
        if x["action_type"] == "bid_adjust":
            o, n = money(x["old_value"]), money(x["new_value"])
            if o is not None and n is not None:
                kind = "bid_down" if n < o else "bid_up"
        elif x["action_type"] in ("pause", "pause_target"):
            kind = "pause"
        elif x["action_type"] == "neg_keyword":
            kind = "negative"
        if kind and lo <= S.idx(day) <= hi and str(x["entity_id"]) in S.prefix:  # only entities in the scored set (campaign filter / known keywords)
            acts[str(x["entity_id"])].append({"day": day, "i": S.idx(day), "kind": kind})
    for e in acts:
        acts[e].sort(key=lambda z: z["day"])
    CUT = {"bid_down", "pause", "negative"}

    # ---- Tier 2: waste / latency / blind spot (episode scan) ----
    rules = load_rules(golden)
    if a.reach == "new":
        from new_engine import NewEngine
        _ncfg = json.loads((CONFIG_DIR / "new_engine.json").read_text(encoding="utf-8"))
        reach = lambda w7, w14, is_t: NewEngine.can_reach(w7, w14, is_t, T, _ncfg)  # noqa: E731
    else:
        reach = lambda w7, w14, is_t: old_engine_can_reach(rules, w7, w14, is_t, T)  # noqa: E731
    ze, oa = Y["cut_evidence"]["zero_orders"], Y["cut_evidence"]["over_acos"]
    waste = 0.0; post_action_bleed = 0.0; sufficient_spend = 0.0; blind_spend = 0.0
    episodes: list[dict] = []
    for e in S.prefix:
        is_target = (meta.get(e, {}).get("match") == "TARGETING_EXPRESSION")
        cut_days = sorted(z["i"] for z in acts.get(e, []) if z["kind"] in CUT)
        in_ep = False; ep_start = 0; first_act = None
        for i in range(lo, hi + 1):
            w = S.window(e, i - W, i - 1)
            a_ = acos_of(w)
            suff = (w[4] == 0 and w[1] >= ze["min_clicks"] and w[2] >= ze["min_spend"]) or \
                   (w[2] >= oa["min_spend"] and a_ is not None and a_ > oa["acos_over"])
            today_cost = S.rows[e][i][2]
            if suff:
                sufficient_spend += today_cost
                w7 = S.window(e, i - 7, i - 1)
                if not reach(w7, w, is_target):
                    blind_spend += today_cost
                if not in_ep:
                    in_ep, ep_start, first_act = True, i, None
                if first_act is None:
                    nxt = [c for c in cut_days if c >= ep_start and c <= i]
                    if nxt:
                        first_act = nxt[0]
                if first_act is None and i >= ep_start + G:
                    waste += today_cost
                elif first_act is not None and i > first_act:
                    post_action_bleed += today_cost
            elif in_ep:
                episodes.append({"entity": e, "start": ep_start, "end": i - 1, "first_act": first_act})
                in_ep = False
        if in_ep:
            episodes.append({"entity": e, "start": ep_start, "end": hi, "first_act": first_act})
    total_kw_spend = sum(S.window(e, lo, hi)[2] for e in S.prefix)
    for ep in episodes:  # annotate for the report
        e = ep["entity"]; m = meta.get(e, {})
        w = S.window(e, ep["start"], ep["end"])
        ep.update({"text": m.get("text"), "match": m.get("match"), "campaign_id": m.get("campaign_id"),
                   "start_date": str(day0 + dt.timedelta(days=ep["start"])), "end_date": str(day0 + dt.timedelta(days=ep["end"])),
                   "days": ep["end"] - ep["start"] + 1, "spend": round(w[2], 2), "clicks": w[1], "orders": w[4],
                   "actions_in_range": sorted({z["kind"] for z in acts.get(e, []) if ep["start"] <= z["i"] <= ep["end"]})})
    min_sp = Y.get("min_episode_spend_usd", 0.0)
    episodes = [ep for ep in episodes if ep["spend"] >= min_sp]
    lat_acted = [ep["first_act"] - ep["start"] for ep in episodes if ep["first_act"] is not None]
    long_eps = [ep for ep in episodes if ep["end"] - ep["start"] + 1 >= Y["min_episode_days_for_unacted"]]
    unacted_long = [ep for ep in long_eps if ep["first_act"] is None]

    # ---- Tier 2: false kills ----
    fk_bid = fk_bid_n = 0; fk_life = fk_life_n = 0
    L = Y["false_kill"]["lifecycle_days"]
    for e, lst in acts.items():
        for z in lst:
            i = z["i"]
            if z["kind"] == "bid_down" and i + FWD <= ndays - 1:
                f = S.window(e, i + 1, i + FWD); fa = acos_of(f)
                fk_bid_n += 1
                if f[4] >= Y["false_kill"]["forward_min_orders"] and fa is not None and fa <= T:
                    fk_bid += 1
            elif z["kind"] in ("pause", "negative"):
                b = S.window(e, i - L, i - 1); ba = acos_of(b)
                fk_life_n += 1
                if b[4] >= Y["false_kill"]["lifecycle_min_orders"] and ba is not None and ba <= T:
                    fk_life += 1

    # ---- Tier 2: churn ----
    rev = tot = 0
    for e, lst in acts.items():
        bids = [z for z in lst if z["kind"] in ("bid_up", "bid_down")]
        tot += len(bids)
        for p, q in zip(bids, bids[1:]):
            if p["kind"] != q["kind"] and (q["day"] - p["day"]).days <= Y["churn_days"]:
                rev += 1

    # ---- before/after per action kind ----
    ba: dict[str, dict] = defaultdict(lambda: {"n": 0, "before": [0.0] * 5, "after": [0.0] * 5})
    for e, lst in acts.items():
        for z in lst:
            i = z["i"]
            if i - FWD < 0 or i + FWD > ndays - 1:
                continue
            b, f = S.window(e, i - FWD, i - 1), S.window(e, i + 1, i + FWD)
            rec = ba[z["kind"]]; rec["n"] += 1
            for k in range(5):
                rec["before"][k] += b[k]; rec["after"][k] += f[k]
    before_after = {k: {"n": v["n"], "before": {"clicks": v["before"][1], "spend": round(v["before"][2], 2), "sales": round(v["before"][3], 2), "orders": v["before"][4], "acos": acos_of(v["before"])},
                        "after": {"clicks": v["after"][1], "spend": round(v["after"][2], 2), "sales": round(v["after"][3], 2), "orders": v["after"][4], "acos": acos_of(v["after"])}} for k, v in ba.items()}

    # ---- Tier 1: settled 14-day window series (campaign level + snapshots) ----
    camp = read_jsonl(golden / "campaign_daily_metrics.jsonl")
    snaps = read_jsonl(golden / "campaign_snapshot.jsonl")
    last_by_day: dict[dt.date, dict[str, dict]] = defaultdict(dict)
    for s in sorted(snaps, key=lambda z: z["id"]):
        last_by_day[d(s["snapshot_at"])][str(s["campaign_id"])] = s
    sp_rows = read_jsonl(golden / "product_daily_sales.jsonl")
    tr_rows = read_jsonl(golden / "traffic_daily.jsonl")
    SW = Y["settled_window_days"]
    windows = []
    wend = day_end
    while wend - dt.timedelta(days=SW - 1) >= day0:
        wstart = wend - dt.timedelta(days=SW - 1)
        rows = [r for r in camp if wstart <= d(r["report_date"]) <= wend]
        by_c: dict[str, list[float]] = defaultdict(lambda: [0.0] * 3)
        for r in rows:
            c = by_c[str(r["campaign_id"])]; c[0] += float(r["cost"] or 0); c[1] += float(r["sales"] or 0); c[2] += float(r["orders"] or 0)
        cost = sum(v[0] for v in by_c.values()); sales = sum(v[1] for v in by_c.values()); orders = sum(v[2] for v in by_c.values())
        zero = sum(v[0] for v in by_c.values() if v[2] == 0 and v[0] > 0)
        clicks = sum(float(r["clicks"] or 0) for r in rows)
        bud_days = [dy for dy in last_by_day if wstart <= dy <= wend]
        bud = [sum(float(s["daily_budget"] or 0) for s in last_by_day[dy].values() if s["state"] == "ENABLED") for dy in bud_days]
        avg_budget = statistics.mean(bud) if bud else None
        sp = [r for r in sp_rows if r["asin"] == "__ALL__" and wstart <= d(r["report_date"]) <= wend]
        tr = [r for r in tr_rows if r["asin"] == "__ALL__" and wstart <= d(r["report_date"]) <= wend]
        # only trust store-level figures when the window is fully covered (SP-API data starts 2026-06-12)
        full_sp, full_tr = len(sp) >= SW, len(tr) >= SW
        st_orders = sum(float(r["total_orders"] or 0) for r in sp) if full_sp else 0.0
        st_sales = sum(float(r["ordered_product_sales"] or 0) for r in sp) if full_sp else 0.0
        sessions = sum(float(r["sessions"] or 0) for r in tr) if full_tr else 0.0
        units = sum(float(r["units_ordered"] or 0) for r in tr) if full_tr else 0.0
        windows.append({
            "window": [str(wstart), str(wend)], "ad_spend": round(cost, 2), "ad_sales": round(sales, 2), "ad_orders": orders,
            "acos": round(cost / sales, 4) if sales else None, "cpc": round(cost / clicks, 4) if clicks else None,
            "ad_aov": round(sales / orders, 2) if orders else None, "zero_sale_spend_share": round(zero / cost, 4) if cost else None,
            "active_campaigns_with_spend": len([v for v in by_c.values() if v[0] > 0]),
            "avg_enabled_budget_sum": round(avg_budget, 2) if avg_budget else None,
            "budget_utilization": round((cost / SW) / avg_budget, 4) if avg_budget else None,
            "store_orders": st_orders or None, "store_sales": round(st_sales, 2) or None, "tacos": round(cost / st_sales, 4) if st_sales else None,
            "sessions": sessions or None, "store_cvr": round(units / sessions, 4) if sessions else None,
        })
        wend = wstart - dt.timedelta(days=1)
    windows.reverse()

    # ---- self-assessment of the old engine (reference) ----
    verdicts = defaultdict(int)
    if (golden / "retrospectives.jsonl").exists():
        for r in read_jsonl(golden / "retrospectives.jsonl"):
            verdicts[r.get("verdict")] += 1

    out = {
        "engine": a.engine, "scored_at": dt.datetime.now().isoformat(timespec="seconds"),
        "golden": {"dir": golden.name, "date_from": str(day0), "date_to": str(day_end), "days": ndays, "campaign_filter": a.campaigns,
                   "scored_range": [str(day0 + dt.timedelta(days=lo)), str(day0 + dt.timedelta(days=hi))],
                   "keyword_rows": len(kw_rows), "decisions_scored": sum(len(v) for v in acts.values())},
        "yardstick": Y,
        "tier2_decision_quality": {
            "waste_share": round(waste / total_kw_spend, 4) if total_kw_spend else None,
            "waste_usd": round(waste, 2), "post_action_bleed_usd": round(post_action_bleed, 2), "total_keyword_spend_usd": round(total_kw_spend, 2),
            "detection_latency_days": {"episodes": len(episodes), "acted": len(lat_acted),
                                       "median": statistics.median(lat_acted) if lat_acted else None,
                                       "p75": (sorted(lat_acted)[int(0.75 * (len(lat_acted) - 1))] if lat_acted else None),
                                       "long_episodes": len(long_eps), "long_unacted": len(unacted_long),
                                       "long_unacted_share": round(len(unacted_long) / len(long_eps), 4) if long_eps else None,
                                       "long_unacted_list": sorted([{k: ep[k] for k in ("entity", "text", "match", "campaign_id", "start_date", "end_date", "days", "spend", "clicks", "orders", "actions_in_range")} for ep in unacted_long], key=lambda x: -x["spend"])[:30]},
            "false_kill_rate": {"bid_cuts": fk_bid_n, "bid_cut_false": fk_bid, "bid_cut_rate": round(fk_bid / fk_bid_n, 4) if fk_bid_n else None,
                                "kills": fk_life_n, "kills_false_lifecycle": fk_life, "kill_rate": round(fk_life / fk_life_n, 4) if fk_life_n else None},
            "churn_rate": {"bid_adjustments": tot, "reversed_within_days": rev, "rate": round(rev / tot, 4) if tot else None},
            "blind_spot_share": {"sufficient_spend_usd": round(sufficient_spend, 2), "blind_spend_usd": round(blind_spend, 2),
                                 "share": round(blind_spend / sufficient_spend, 4) if sufficient_spend else None,
                                 "reach": a.reach, "note": "share of evidence-sufficient spend that none of the scored engine's cut rules could match (old = ads_rules thresholds, new = NewEngine.can_reach)"},
            "before_after_14d": before_after,
        },
        "tier1_outcome_windows": windows,
        "old_engine_self_verdicts": dict(verdicts),
    }
    out_path = (ROOT / a.out) if not Path(a.out).is_absolute() else Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dumps(out, indent=1), encoding="utf-8")

    t2 = out["tier2_decision_quality"]
    print(f"engine={a.engine}  golden={golden.name}  scored {out['golden']['scored_range'][0]}..{out['golden']['scored_range'][1]}  decisions={out['golden']['decisions_scored']}")
    print(f"  waste_share        {t2['waste_share']}   (${t2['waste_usd']} of ${t2['total_keyword_spend_usd']}; post-action bleed ${t2['post_action_bleed_usd']})")
    dl = t2["detection_latency_days"]
    print(f"  detection_latency  median {dl['median']}  p75 {dl['p75']}  ({dl['acted']}/{dl['episodes']} episodes acted; long unacted {dl['long_unacted']}/{dl['long_episodes']})")
    fk = t2["false_kill_rate"]
    print(f"  false_kill_rate    bid cuts {fk['bid_cut_rate']} ({fk['bid_cut_false']}/{fk['bid_cuts']})   kills {fk['kill_rate']} ({fk['kills_false_lifecycle']}/{fk['kills']})")
    ch = t2["churn_rate"]
    print(f"  churn_rate         {ch['rate']} ({ch['reversed_within_days']}/{ch['bid_adjustments']})")
    bs = t2["blind_spot_share"]
    print(f"  blind_spot_share   {bs['share']} (${bs['blind_spend_usd']} of ${bs['sufficient_spend_usd']} sufficient)")
    print("  tier1 windows:")
    for w in windows:
        print(f"    {w['window'][0]}..{w['window'][1]}  spend {w['ad_spend']:>7}  acos {w['acos']}  orders {w['ad_orders']:>4}  zero-sale {w['zero_sale_spend_share']}  budget {w['avg_enabled_budget_sum']}  util {w['budget_utilization']}  store_orders {w['store_orders']}  tacos {w['tacos']}")
    print("->", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
