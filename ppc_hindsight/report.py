"""Render an audit result as Markdown. Numbers first, names second, method last."""
from __future__ import annotations


def _usd(x: float | None) -> str:
    return "—" if x is None else f"${x:,.2f}"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _cut(s: str, n: int = 38) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def render(res: dict, top: int = 15) -> str:
    T, W, Y = res["totals"], res["window"], res["yardstick"]
    ze, oa = Y["cut_evidence"]["zero_orders"], Y["cut_evidence"]["over_acos"]
    out: list[str] = []
    out.append("# PPC hindsight audit\n")
    out.append(f"Window {W['date_from']} → {W['date_to']} (scored through {W['settled_to']}; "
               f"last {W['unsettled_days']} days treated as unsettled). {T['targets']} targets.\n")

    out.append("## The five numbers\n")
    out.append("| | | meaning |\n|---|---|---|")
    out.append(f"| Spend after the evidence was in | **{_usd(T['waste'])}** ({_pct(T['waste_share'])} of {_usd(T['spend'])}) "
               f"| money spent on targets *after* they had already met your cut criteria for {Y['grace_days']} extra days |")
    out.append(f"| Sales that money brought | {_usd(T['waste_sales'])} | what those same target-days sold (so you can judge the net) |")
    out.append(f"| Long bleeds never stopped | **{T['long_unacted']}** of {T['long_episodes']} "
               f"| episodes of ≥{Y['min_episode_days_for_unacted']} days where spend never dried up |")
    out.append(f"| Still bleeding today | **{T['ongoing_targets']}** targets, {_usd(T['ongoing_last7_spend'])} in the last 7 days "
               f"| evidence sufficient on the last settled day and still spending |")
    out.append(f"| Days from evidence to stop | median **{T['median_days_to_stop'] if T['median_days_to_stop'] is not None else '—'}** "
               f"| over {T['stopped_episodes']} episodes where spend did stop |")
    out.append("")

    ongoing = [t for t in res["targets"] if t["ongoing"]]
    if ongoing:
        out.append("## Still bleeding (act on these first)\n")
        wd = Y["cut_evidence"]["window_days"]
        out.append(f"| target | match | campaign | status | bid | last {wd}d spend | last {wd}d clicks | last {wd}d orders | last {wd}d ACOS |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for t in ongoing[:top]:
            out.append(f"| {_cut(t['targeting'])} | {t['match_type']} | {_cut(t['campaign'], 28)} | {t['status'] or '—'} | "
                       f"{_usd(t['bid']) if t['bid'] is not None else '—'} | {_usd(t['w14_spend'])} | {t['w14_clicks']} | {t['w14_orders']} | {_pct(t['w14_acos'])} |")
        if len(ongoing) > top:
            out.append(f"\n…and {len(ongoing) - top} more.")
        out.append("")

    out.append(f"## Where the money went (top {top} by spend after evidence)\n")
    out.append("| target | match | campaign | episodes | spend after evidence | sales on those days | total spend | ACOS |")
    out.append("|---|---|---|---|---|---|---|---|")
    for t in [x for x in res["targets"] if x["waste"] > 0][:top]:
        out.append(f"| {_cut(t['targeting'])} | {t['match_type']} | {_cut(t['campaign'], 28)} | {t['episodes']} | "
                   f"**{_usd(t['waste'])}** | {_usd(sum(e['waste_sales'] for e in res['episodes'] if e['target_id'] == t['target_id']))} | "
                   f"{_usd(t['spend'])} | {_pct(t['acos'])} |")
    out.append("")

    out.append("## Episodes (what happened, in order of cost)\n")
    out.append("| target | from | to | days | spend | sales | orders | after grace | ended |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    name = {t["target_id"]: t for t in res["targets"]}
    for e in res["episodes"][:top * 2]:
        t = name[e["target_id"]]
        out.append(f"| {_cut(t['targeting'], 30)} ({t['match_type']}) | {e['start_date']} | {e['end_date']} | {e['days']} | "
                   f"{_usd(e['spend'])} | {_usd(e['sales'])} | {e['orders']} | {_usd(e['waste'])} | {e['end_reason']} |")
    out.append("")

    out.append("## Method\n")
    out.append(f"- A target-day counts as *evidence sufficient* when, over the trailing {Y['cut_evidence']['window_days']} days, "
               f"either it had **0 orders with ≥{ze['min_clicks']} clicks and ≥{_usd(ze['min_spend'])} spend**, "
               f"or it spent ≥{_usd(oa['min_spend'])} at **ACOS above {_pct(oa['acos_over'])}** (target ACOS {_pct(Y['target_acos'])}).")
    out.append(f"- The first {Y['grace_days']} days of every episode are forgiven. Only spend after that is counted.")
    out.append(f"- Episodes under {_usd(Y['min_episode_spend_usd'])} total spend are ignored (dead keywords are not a failure to act).")
    out.append("- `stopped` = spend in the 7 days after the episode fell below 20% of the in-episode daily rate. "
               "`recovered` = orders came back. `faded` = still spending, just slipped under the evidence thresholds "
               "(usually a bid cut that slowed the bleed without stopping it). `ongoing` = still qualifying on the last settled day.")
    out.append("- This audit only sees what the export sees: no bid history, no attribution beyond what the report column carries. "
               "It prices decisions, it does not make them.")
    out.append("")
    return "\n".join(out)
