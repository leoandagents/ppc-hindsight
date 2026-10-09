# ppc-hindsight

**What did your late, missing and wrong keyword decisions actually cost?**

One export from Amazon Ads, one command, five numbers with the keywords and dollars behind each.
No API keys, no account access, nothing leaves your machine.

```
| Spend after the evidence was in | $325.87 (13.4% of $2,434.11) |
| Sales that money brought        | $284.10                      |
| Long bleeds never stopped       | 9 of 15                      |
| Still bleeding today            | 3 targets, $33.99 last 7d    |
| Days from evidence to stop      | median 16                    |
```

It is an **audit**, not an optimizer. It does not touch bids. It tells you, in hindsight and by your own
thresholds, how much money kept flowing after a keyword had already proven it should be cut, and which
keywords are doing that right now.

## 1. Export the report (2 minutes, once)

Amazon Ads console → **Reports** (the unified "Reports" page, not the legacy "Sponsored ads reports") →
**Create report** → template **Targeting**.

In *Custom columns*:

- **add** the dimension **Date** (under *Time*);
- **remove** the dimension **Date range** (it conflicts with *Date*; the console will refuse to submit otherwise).

Keep the default metrics. Set *Report period* to **Last 90 days** (or a quarter / year). Format CSV. Submit, wait
a few minutes, download.

Column names are localized by the console; English and Simplified Chinese are recognized out of the box.
If yours are not, add an alias in `ppc_hindsight/normalize.py` (one line) and open an issue.

## 2. Run

```bash
pip install -e .
ppc-hindsight targeting.csv                      # Markdown report to stdout
ppc-hindsight targeting.csv --out audit.md --json audit.json
ppc-hindsight targeting.csv --target-acos 0.30 --max-acos 0.50
ppc-hindsight targeting.csv --from 2026-07-01   # score a sub-window; earlier rows still warm up the trailing windows
```

Or as a Claude Code skill: install the plugin and say *"audit my PPC with targeting.csv"*.

## 3. What the numbers mean

A target-day is **evidence sufficient** when, over the trailing 14 days, either

- it had **0 orders with ≥15 clicks and ≥$10 spend**, or
- it spent **≥$15 at ACOS above 55%**.

Consecutive evidence days form an **episode**. The first 2 days of each episode are forgiven (someone has to
notice). Everything spent after that is **spend after evidence**. An episode ends when spend dries up
(*stopped*), when orders come back (*recovered*), or not at all (*ongoing*).

Every threshold lives in `yardstick.default.json` and can be overridden on the command line. The point of
fixed, boring thresholds is that two runs are comparable: this month against last month, your rules against
the console's automation, one account against another.

### Known blind spots

- The export has no bid history, so the audit cannot tell *who* stopped the bleed (you, a rule, or Amazon's
  budget cap). It only sees that spend stopped.
- Sales attribution in the export is whatever the report column carries (14-day by default in the unified
  report). The last 2 days are ignored for that reason (`--unsettled-days`).
- Product targets (`substitutes`, `complements`, `close-match`…) are scored like keywords. That is deliberate:
  auto-campaign targets are where most unseen waste lives.

## Why this exists

I ran a rule engine on my own Amazon account for six months and built a replay harness to score it. The
scoreboard said the engine *acted* within 4 days of evidence, median. Then I measured when the spend actually
stopped: 16 days. Bid cuts of 15–35% don't stop a bleeding keyword, they just slow it. Pricing that gap turned
out to be the one number nobody's dashboard shows. This is that number, for any account.

## License

MIT
