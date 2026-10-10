# Privacy policy

**ppc-hindsight runs entirely on your machine and sends nothing anywhere.**

## What it reads

One file you choose: a Targeting report CSV you exported yourself from the Amazon Ads console. That file
contains your campaign, ad group and target names and IDs, bids, status, and daily performance metrics
(impressions, clicks, spend, sales, orders). It does not contain shopper data.

## What it writes

Only what you ask for: a Markdown report and, optionally, a JSON file, both written next to the input file
or to the paths you pass with `--out` / `--json`. Nothing else is written. No cache, no telemetry, no logs.

## What it sends

Nothing. The tool makes no network requests. There is no server, no account, no analytics, no update check.
It depends only on the Python standard library, so no third-party package receives your data either.

## Claude Code plugin

When used as a Claude Code skill, the skill runs the same local script and reads the resulting report back
into your Claude conversation. What Claude does with conversation content is governed by Anthropic's own
privacy policy for the product you are using, not by this project. The skill never modifies your ad account
and has no credentials to do so.

## Data retention

None. The project retains nothing because it stores nothing beyond the files you asked it to write.

## Contact

Open an issue at https://github.com/leoandagents/ppc-hindsight/issues.
