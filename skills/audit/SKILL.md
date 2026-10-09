---
name: audit
description: Audit wasted Amazon Sponsored Products spend from one Targeting report CSV. Use when the user says "audit my PPC", "where is my ad spend bleeding", "hindsight audit", "which keywords should I have cut", or hands over an Amazon Ads Targeting export (daily, with a Date column). Runs locally, no API keys, nothing leaves the machine.
---

# PPC hindsight audit

You price, in hindsight and by fixed thresholds, how much money kept flowing after a keyword or target had
already met the user's own cut criteria, and which targets are still bleeding today. You do not change bids.

## 1. Get the input

The only input is a CSV exported from the Amazon Ads console, **Reports** page (unified reporting, not the legacy
"Sponsored ads reports"), template **Targeting**, with the **Date** dimension added and the **Date range**
dimension removed (the console refuses the combination otherwise), period **Last 90 days** or longer, format CSV.
English and Simplified Chinese column headers are recognized automatically.

If the user has not given you a file path, ask for one and paste the export steps above. Do not try to download
the report yourself unless the user explicitly asks you to drive their browser.

## 2. Run

```bash
python "${CLAUDE_PLUGIN_ROOT}/audit.py" "<csv path>" --out "<csv dir>/ppc-hindsight.md" --json "<csv dir>/ppc-hindsight.json" --quiet
```

Requires Python 3.10+, standard library only. Useful flags:

- `--target-acos 0.30 --max-acos 0.50` to use the user's own ACOS thresholds (defaults 0.35 / 0.55).
- `--grace-days 3` to forgive more days before counting spend as waste (default 2).
- `--from 2026-07-01 --to 2026-09-30` to score a sub-window (earlier rows still warm up the trailing windows).
- `--unsettled-days 2` trailing days ignored for attribution lag.

If it exits with "Missing required columns", show the user the headers it printed and the export steps in step 1.
Do not guess column mappings; if the locale is unsupported, add an alias to `ppc_hindsight/normalize.py`
only when the user asks you to.

## 3. Report back

Open the generated Markdown and lead with the five numbers table, then the **Still bleeding** list (these are the
actions), then the top of **Where the money went**. Quote dollar amounts and target names exactly as the report
prints them. Then say, in one or two sentences, what the numbers mean for this account:

- Waste share under ~5% and nothing ongoing: the account is being managed tightly; the remaining spend is the
  cost of discovering new keywords.
- Waste share 10–30% with several `faded` or `recovered` episodes: decisions are being made but too gently
  (bid cuts that slow the bleed without stopping it). Suggest pausing or negating the ongoing ones.
- A single target dominating waste, especially a product-target (`substitutes`, `complements`, `close-match`,
  `loose-match`) or a Sponsored Brands theme target: that is a campaign-level problem, not a bid problem.

State the limits once: the export has no bid history, so the audit sees *that* spend stopped, not *who* stopped
it; the last two days are excluded for attribution lag; the thresholds are configurable and were chosen to be
boring and comparable across runs, not to be right for every category.

Never pause, negate or change anything in the user's ad account from this skill. Give them the list.
