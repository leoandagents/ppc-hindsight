"""Metrics-only hindsight audit.

Given daily keyword/target metrics, find every stretch of days where a target had
already proven itself unprofitable under *your* thresholds (an "evidence episode"),
and price what was spent after that point. No action log is needed: the money is
counted whether or not you reacted, because it left your account either way.

Definitions (yardstick, all deterministic):
  evidence-sufficient day  : over the trailing `window_days` ending on that day, EITHER
                             zero orders with >= min_clicks clicks and >= min_spend spend,
                             OR spend >= min_spend and ACOS > acos_over.
  episode                  : a maximal run of consecutive evidence-sufficient days.
  grace                    : the first `grace_days` of an episode are forgiven (a human
                             needs time to notice).
  waste                    : spend inside an episode after the grace days.
  long unacted episode     : an episode of >= min_episode_days whose spend never stopped.
  end reason               : "stopped"  = spend dried up after the episode (you cut/paused it,
                                          or it ran out of impressions)
                             "recovered"= orders came back and the window no longer qualifies
                             "ongoing"  = still bleeding on the last settled day
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

DEFAULT_YARDSTICK = Path(__file__).with_name("yardstick.default.json")

IMPR, CLICKS, SPEND, SALES, ORDERS = range(5)


def load_yardstick(path: str | Path | None = None, **overrides) -> dict:
    y = json.loads(Path(path or DEFAULT_YARDSTICK).read_text(encoding="utf-8"))
    y = {k: v for k, v in y.items() if not k.startswith("_")}
    for k, v in overrides.items():
        if v is None:
            continue
        if k == "target_acos":
            y["target_acos"] = v
        elif k == "max_acos":
            y["max_acos"] = v
            y["cut_evidence"]["over_acos"]["acos_over"] = v
        elif k == "grace_days":
            y["grace_days"] = v
        elif k == "window_days":
            y["cut_evidence"]["window_days"] = v
    return y


class Series:
    """Per-target daily metrics with prefix sums for O(1) window queries."""

    def __init__(self, day0: dt.date, ndays: int):
        self.day0, self.n = day0, ndays
        self.rows: dict[str, list[list[float]]] = {}
        self.prefix: dict[str, list[list[float]]] = {}

    def idx(self, day: dt.date) -> int:
        return (day - self.day0).days

    def add(self, key: str, day: dt.date, vals: list[float]):
        i = self.idx(day)
        if not (0 <= i < self.n):
            return
        arr = self.rows.setdefault(key, [[0.0] * 5 for _ in range(self.n)])
        for k in range(5):
            arr[i][k] += float(vals[k] or 0)

    def finalize(self):
        for e, arr in self.rows.items():
            pre = [[0.0] * 5]
            for row in arr:
                pre.append([pre[-1][k] + row[k] for k in range(5)])
            self.prefix[e] = pre

    def window(self, key: str, s: int, e: int) -> list[float]:
        pre = self.prefix.get(key)
        if pre is None:
            return [0.0] * 5
        s, e = max(0, s), min(self.n - 1, e)
        if e < s:
            return [0.0] * 5
        return [pre[e + 1][k] - pre[s][k] for k in range(5)]


def _acos(w: list[float]) -> float | None:
    return (w[SPEND] / w[SALES]) if w[SALES] > 0 else None


def audit(rows: list[dict], yardstick: dict, unsettled_days: int = 2,
          score_from: str | None = None, score_to: str | None = None) -> dict:
    """Audit `rows`. `score_from`/`score_to` (ISO dates) limit what is *scored*; rows before
    `score_from` still feed the trailing windows so the first scored days are not blind."""
    if not rows:
        raise ValueError("no rows")
    if score_to:
        rows = [r for r in rows if r["date"] <= score_to]
    days = sorted({dt.date.fromisoformat(r["date"]) for r in rows})
    day0, day_last = days[0], days[-1]
    ndays = (day_last - day0).days + 1
    settled_end = ndays - 1 - max(0, unsettled_days)  # last index we trust
    if settled_end < 0:
        raise ValueError("not enough settled days in the export")
    score_start = max(0, (dt.date.fromisoformat(score_from) - day0).days) if score_from else 0
    if score_start > settled_end:
        raise ValueError("score_from is after the last settled day")

    S = Series(day0, ndays)
    meta: dict[str, dict] = {}
    last_seen: dict[str, tuple[str, dict]] = {}
    for r in rows:
        k = r["target_id"]
        S.add(k, dt.date.fromisoformat(r["date"]), [r["impressions"], r["clicks"], r["spend"], r["sales"], r["orders"]])
        if k not in meta:
            meta[k] = {"targeting": r["targeting"], "match_type": r["match_type"],
                       "campaign": r["campaign_name"] or r["campaign_id"], "ad_group": r["ad_group_name"] or r["ad_group_id"]}
        if k not in last_seen or r["date"] >= last_seen[k][0]:
            last_seen[k] = (r["date"], r)
    S.finalize()
    for k, (_, r) in last_seen.items():
        meta[k]["bid"] = r.get("bid")
        meta[k]["status"] = r.get("status") or ""

    W = int(yardstick["cut_evidence"]["window_days"])
    G = int(yardstick["grace_days"])
    ze, oa = yardstick["cut_evidence"]["zero_orders"], yardstick["cut_evidence"]["over_acos"]
    min_ep_days = int(yardstick.get("min_episode_days_for_unacted", 7))
    min_ep_spend = float(yardstick.get("min_episode_spend_usd", 0.0))

    def sufficient(k: str, i: int) -> bool:
        w = S.window(k, i - W + 1, i)
        if w[ORDERS] == 0 and w[CLICKS] >= ze["min_clicks"] and w[SPEND] >= ze["min_spend"]:
            return True
        a = _acos(w)
        return w[SPEND] >= oa["min_spend"] and a is not None and a > oa["acos_over"]

    episodes: list[dict] = []
    per_target: dict[str, dict] = {}
    for k in S.prefix:
        flags = [sufficient(k, i) for i in range(ndays)]
        i = 0
        eps_k = []
        while i <= settled_end:
            if not flags[i]:
                i += 1
                continue
            j = i
            while j + 1 <= settled_end and flags[j + 1]:
                j += 1
            if j < score_start:  # episode entirely before the scored range: skip
                i = j + 1
                continue
            w_ep = S.window(k, max(i, score_start), j)
            ws = max(i + G, score_start)
            waste_w = S.window(k, ws, j) if j >= ws else [0.0] * 5
            # classify how it ended
            if j == settled_end:
                reason = "ongoing"
            else:
                after = S.window(k, j + 1, min(settled_end, j + 7))
                daily_in = S.window(k, i, j)[SPEND] / max(1, j - i + 1)
                after_daily = after[SPEND] / max(1, min(settled_end, j + 7) - j)
                nxt = S.window(k, j + 2 - W, j + 1)  # the first window that no longer qualified
                if after_daily < 0.2 * daily_in:
                    reason = "stopped"      # spend dried up: a cut, a pause, or no more impressions
                elif nxt[ORDERS] > 0:
                    reason = "recovered"    # orders came back
                else:
                    reason = "faded"        # still spending, just slipped under the evidence thresholds
            ep = {
                "target_id": k, "start": i, "end": j, "days": j - i + 1,
                "start_date": (day0 + dt.timedelta(days=i)).isoformat(),
                "end_date": (day0 + dt.timedelta(days=j)).isoformat(),
                "spend": round(w_ep[SPEND], 2), "sales": round(w_ep[SALES], 2),
                "clicks": int(w_ep[CLICKS]), "orders": int(w_ep[ORDERS]),
                "waste": round(waste_w[SPEND], 2), "waste_sales": round(waste_w[SALES], 2),
                "end_reason": reason,
            }
            eps_k.append(ep)
            i = j + 1
        eps_k = [e for e in eps_k if e["spend"] >= min_ep_spend]
        episodes.extend(eps_k)
        tot = S.window(k, score_start, settled_end)
        last7 = S.window(k, settled_end - 6, settled_end)
        w14 = S.window(k, settled_end - W + 1, settled_end)
        if tot[SPEND] <= 0 and not eps_k:
            continue  # nothing spent in the scored range
        per_target[k] = {
            **meta[k], "target_id": k,
            "spend": round(tot[SPEND], 2), "sales": round(tot[SALES], 2), "orders": int(tot[ORDERS]), "clicks": int(tot[CLICKS]),
            "acos": round(_acos(tot), 3) if _acos(tot) is not None else None,
            "waste": round(sum(e["waste"] for e in eps_k), 2),
            "episodes": len(eps_k),
            "ongoing": any(e["end_reason"] == "ongoing" for e in eps_k),
            "last7_spend": round(last7[SPEND], 2),
            "w14_spend": round(w14[SPEND], 2), "w14_clicks": int(w14[CLICKS]), "w14_orders": int(w14[ORDERS]),
            "w14_acos": round(_acos(w14), 3) if _acos(w14) is not None else None,
        }

    total_spend = sum(t["spend"] for t in per_target.values())
    total_sales = sum(t["sales"] for t in per_target.values())
    waste = sum(e["waste"] for e in episodes)
    waste_sales = sum(e["waste_sales"] for e in episodes)
    long_eps = [e for e in episodes if e["days"] >= min_ep_days]
    long_unacted = [e for e in long_eps if e["end_reason"] != "stopped"]
    ongoing = [t for t in per_target.values() if t["ongoing"]]
    stopped = [e for e in episodes if e["end_reason"] == "stopped"]
    latencies = sorted(e["days"] for e in stopped)  # days from first evidence to spend stopping
    med_latency = latencies[len(latencies) // 2] if latencies else None

    return {
        "window": {"date_from": day0.isoformat(), "date_to": day_last.isoformat(),
                   "scored_from": (day0 + dt.timedelta(days=score_start)).isoformat(),
                   "settled_to": (day0 + dt.timedelta(days=settled_end)).isoformat(), "unsettled_days": unsettled_days},
        "yardstick": yardstick,
        "totals": {
            "targets": len(per_target), "spend": round(total_spend, 2), "sales": round(total_sales, 2),
            "acos": round(total_spend / total_sales, 3) if total_sales else None,
            "waste": round(waste, 2), "waste_share": round(waste / total_spend, 4) if total_spend else 0.0,
            "waste_sales": round(waste_sales, 2),
            "episodes": len(episodes), "long_episodes": len(long_eps), "long_unacted": len(long_unacted),
            "ongoing_targets": len(ongoing), "ongoing_last7_spend": round(sum(t["last7_spend"] for t in ongoing), 2),
            "stopped_episodes": len(stopped), "median_days_to_stop": med_latency,
        },
        "targets": sorted(per_target.values(), key=lambda t: (-t["waste"], -t["spend"])),
        "episodes": sorted(episodes, key=lambda e: -e["waste"]),
    }
