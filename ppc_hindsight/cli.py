"""ppc-hindsight CLI.

    ppc-hindsight targeting.csv                      # Markdown to stdout
    ppc-hindsight targeting.csv --out audit.md --json audit.json
    ppc-hindsight targeting.csv --target-acos 0.30 --max-acos 0.50 --grace-days 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import audit, load_yardstick
from .normalize import read_targeting_csv, summarize_input
from .report import render


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ppc-hindsight", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="Amazon Ads > Reports > Targeting template export (CSV) with the Date dimension")
    ap.add_argument("--yardstick", help="JSON with thresholds (default: packaged yardstick.default.json)")
    ap.add_argument("--target-acos", type=float)
    ap.add_argument("--max-acos", type=float, help="ACOS above which a target counts as bleeding (default 0.55)")
    ap.add_argument("--grace-days", type=int)
    ap.add_argument("--window-days", type=int)
    ap.add_argument("--unsettled-days", type=int, default=2, help="trailing days to ignore for attribution lag (default 2)")
    ap.add_argument("--from", dest="score_from", help="score only from this date (YYYY-MM-DD); earlier rows still warm up the windows")
    ap.add_argument("--to", dest="score_to", help="drop rows after this date (YYYY-MM-DD)")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", help="write Markdown here")
    ap.add_argument("--json", dest="json_out", help="write full result JSON here")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    rows = read_targeting_csv(a.csv)
    info = summarize_input(rows)
    if not a.quiet:
        print(f"[input] {info['rows']} rows, {info['targets']} targets, {info['campaigns']} campaigns, "
              f"{info['date_from']} → {info['date_to']}, spend ${info['spend']:,.2f}"
              f"{'' if info['has_bid'] else ' (no bid column)'}{'' if info['has_status'] else ' (no status column)'}", file=sys.stderr)
    y = load_yardstick(a.yardstick, target_acos=a.target_acos, max_acos=a.max_acos, grace_days=a.grace_days, window_days=a.window_days)
    res = audit(rows, y, unsettled_days=a.unsettled_days, score_from=a.score_from, score_to=a.score_to)
    md = render(res, top=a.top)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if not a.out or not a.quiet:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
