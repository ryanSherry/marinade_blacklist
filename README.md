# marinade_blacklist

Hourly-refreshed list of Solana validators that Marinade Finance has
slashed for being on its Stake Auction Marketplace (SAM) blacklist —
i.e., caught sandwiching or otherwise abusing their stake.

The list lives in this repo as committed JSON / CSV. A GitHub Actions
cron job hits Marinade's public `protected-events` API every hour,
filters for slashing-related reasons, resolves vote-account → validator
identity via a Solana RPC call, and commits the updated data back to
`main`. No server, no auth, no cost to consumers.

## Consume

Single-URL fetch — always serves the latest data:

```
https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/blacklist.json
```

```python
import requests
data = requests.get(
    "https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/blacklist.json"
).json()

# Strict — only validators slashed for blacklist behavior
banned = {v["validator_identity"] for v in data["validators"]
          if v["severity"] == "hard" and v["validator_identity"]}

# Including flagged (BondRiskFee)
flagged_or_worse = {v["validator_identity"] for v in data["validators"]
                    if v["validator_identity"]}
```

Or if you want the strict-only file directly:

```
https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/hard_only.json
```

CSV variant (spreadsheet-friendly):

```
https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/blacklist.csv
```

**Plain-text lists** (one address per line — the simplest consumer path):

```
https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/vote_accounts_hard.txt
https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/vote_accounts_all.txt
https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/identities_hard.txt
https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/identities_all.txt
```

```python
import urllib.request
URL = "https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/vote_accounts_hard.txt"
banned = set(urllib.request.urlopen(URL).read().decode().split())
if validator_vote_account in banned:
    skip_this_validator()
```

> **Use `vote_accounts_*` not `identities_*` as your primary key.** Every
> record has a vote_account. Only ~35% have a resolvable
> `validator_identity` because the rest are delinquent/deregistered
> validators that no longer appear in the cluster's `getVoteAccounts`.

## Severity

| severity | source | meaning |
|----------|--------|---------|
| `hard`   | `BlacklistPenalty` events | Marinade's SAM has slashed this validator's bond for blacklist behavior. Definitively malicious. |
| `soft`   | `BondRiskFee` events      | Marinade has charged this validator a risk fee. Flagged behavior, less severe. |

Other event reasons (`Bidding`, `ProtectedEvent` for downtime,
`PriorityFee`, `BidTooLowPenalty`) are **not** in this list — those
aren't malicious-behavior signals.

## All endpoints

| URL suffix | Purpose |
|------------|---------|
| `data/blacklist.json` | Full data — every flagged validator, both severities, with cluster-state enrichment |
| `data/hard_only.json` | `BlacklistPenalty`-only subset |
| `data/active_only.json` | Subset still in the current validator set — the "actually dangerous now" list |
| `data/recent.json` | Top 50 sorted by `last_slashed_epoch` desc |
| `data/by_epoch.json` | `{ epoch: [vote_account, ...] }` map for time-series queries |
| `data/stats.json` | Aggregate counters for dashboards / status badges |
| `data/blacklist.csv` | Same data as `blacklist.json`, spreadsheet format |
| `data/vote_accounts_hard.txt` | Plain text, one per line, hard severity |
| `data/vote_accounts_all.txt` | Plain text, one per line, all severities |
| `data/identities_hard.txt` | Plain text identities, hard, only resolvable ones |
| `data/identities_all.txt` | Plain text identities, all, only resolvable ones |

All served from `https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/<URL suffix>`.

## Per-validator schema (`blacklist.json`)

```json
{
  "generated_at": "2026-05-15T18:00:00Z",
  "source": "https://validator-bonds-api.marinade.finance/protected-events",
  "methodology": "...",
  "epoch_range": [807, 971],
  "counts": { "total": 88, "hard": 65, "soft": 23 },
  "validators": [
    {
      "vote_account": "GhBWWed6j9tXLEnKiw9CVDHyQCYunAVGnssrbYxbBmFm",
      "validator_identity": "...",         // null if deregistered
      "severity": "hard",
      "slashed_sol": 310.04,
      "slashed_lamports": 310040000000,
      "event_count": 1,
      "first_slashed_epoch": 807,
      "last_slashed_epoch": 807,
      "slashed_epochs": [807],
      "reasons": ["BlacklistPenalty"],
      "is_active": true,                   // still in current validator set
      "current_stake_sol": 133457.9,       // SOL delegated to them right now
      "commission_pct": 5,
      "last_vote_slot": 312456789
    }
  ]
}
```

Sort order in `validators`: highest total slashed lamports first.

### Field semantics — `is_active`

The most useful filter for routing decisions. A blacklisted validator
that's been deregistered (`is_active: false`) has no delegated stake
and can't sandwich anyone right now. The actively dangerous list is
`is_active: true` — currently ~30 of 88 entries. Use `active_only.json`
for that subset directly.

## Run locally

```sh
python3 refresh.py
```

Outputs are written to `data/`. No dependencies — Python 3.10+ stdlib
only. To use a faster RPC than the public mainnet-beta default:

```sh
RPC_URL="https://your-rpc.example.com" python3 refresh.py
```

In the GitHub Action, set a repo secret named `RPC_URL` and the
workflow will use it automatically.

## Update cadence

- The Actions cron runs **every hour, top of the hour, UTC**.
- Marinade publishes new protected-events per-epoch (~3 days), so most
  hourly runs produce no diff and the workflow skips the commit.
- When new entries appear, the workflow commits and pushes within an
  hour.

## Methodology

See [`docs/methodology.md`](docs/methodology.md) for what counts as a
"blacklist" event and what does not.

## License

Public domain — fork it, embed it, build on top of it.
