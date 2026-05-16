# marinade_blacklist

Hourly-refreshed list of Solana validators that Marinade Finance has
blacklisted via its Stake Auction Marketplace (SAM) — i.e., caught
sandwiching or otherwise abusing their stake. Validators that Marinade
later removes from active enforcement automatically drop off this
list; a separate file tracks them as "rehabilitated" so consumers can
still see who has a past offense.

The list lives in this repo as committed JSON / CSV / plain-text
files. A GitHub Actions cron job refreshes from Marinade's public API
every hour and pushes any changes back to `main`. No server, no auth,
no cost to consumers.

## Quick consume

```python
import urllib.request, json

# Current Marinade SAM blacklist (5 validators today)
data = json.loads(urllib.request.urlopen(
    "https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/blacklist.json"
).read())
banned_vote_accounts = {v["vote_account"] for v in data["validators"]}

# Or, simplest one-liner — plain text, one address per line
banned = set(urllib.request.urlopen(
    "https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/vote_accounts_hard.txt"
).read().decode().split())
```

## Using this for MEV protection

If you're building MEV / sandwich protection into a transaction-landing
service, **use these two files**:

| File | Why |
|------|-----|
| `data/identities_hard.txt` | Validator IDENTITIES (node pubkeys) of validators currently on Marinade's SAM blacklist for sandwich-related slashing. Compare against the leader schedule before routing. |
| `data/health.json` | Freshness check. If `last_successful_refresh` is more than ~2 hours old, our pipeline is degraded — fail open or use a cached copy. |

```python
import urllib.request, json, time

LIST = "https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/identities_hard.txt"
HEALTH = "https://raw.githubusercontent.com/ryanSherry/marinade_blacklist/main/data/health.json"

# Refresh once an hour
def load_blacklist():
    health = json.loads(urllib.request.urlopen(HEALTH).read())
    last = health["last_successful_refresh"]
    # ... validate freshness here, decide whether to use ...
    raw = urllib.request.urlopen(LIST).read().decode()
    return set(raw.split())

BANNED_IDENTITIES = load_blacklist()

# Before routing a transaction
def should_route_through(leader_identity: str) -> bool:
    return leader_identity not in BANNED_IDENTITIES
```

### MEV-protection-specific guidance

- **Use the `_hard` variants, NOT `_all`.** Soft severity (`BondRiskFee`)
  is for risky bidding behavior, not necessarily sandwiching — including
  it in MEV protection causes false positives.
- **Use `identities_*.txt`, not `vote_accounts_*.txt`.** The Solana leader
  schedule + gossip data uses validator identity (node pubkey). You
  compare leaders against identity, so that's the matching key.
- **Validators automatically drop off when Marinade un-blacklists them**.
  No manual maintenance — the hourly refresh handles it.
- **Fail open on staleness.** If `health.json` is more than a few hours
  old, the upstream pipeline is down. Don't aggressively block
  transactions based on stale data.

### Limitations to know

- **Marinade-only**: This list catches validators slashed by Marinade's
  SAM. A sandwicher with no Marinade delegation wouldn't appear. For
  fuller coverage, combine with other community lists (Vincibles,
  Trillium reports). This is a high-precision but not exhaustive source.
- **Batch enforcement**: Marinade enforces `BlacklistPenalty` in batches,
  not every epoch. Today's `epochs_since_latest_hard` is 25 — that's not
  unusual. Monitor for gaps > 50 epochs as a sign Marinade's process has
  stopped.
- **Inactive validators excluded**: We only flag validators still
  operational. Deregistered validators (no leader slots, no stake) can't
  sandwich anyone; they live in `historical.json` for audit only.

## File reference

All files live in `data/` and are auto-updated hourly.

### Primary

| File | Contents |
|------|---------|
| **`blacklist.json`** | The currently-enforced Marinade SAM blacklist — active validators in the most recent enforcement batch. Each record includes severity, slashing history, current stake, identity, and a `still_blacklisted` flag. |
| **`blacklist.csv`** | Same data, spreadsheet format. GitHub auto-renders this as a sortable HTML table when viewed via `github.com`. |
| **`hard_only.json`** | Subset of `blacklist.json` — only `BlacklistPenalty` (hard) severity. |
| **`vote_accounts_hard.txt`** | Plain text, one vote_account per line, hard severity only. |
| **`vote_accounts_all.txt`** | Plain text, one per line, all severities. |
| **`identities_hard.txt`** | Same as above but `validator_identity` (node pubkey) — useful when your routing logic keys off identity not vote_account. |
| **`identities_all.txt`** | Same, all severities. |

### Status views

| File | Contents |
|------|---------|
| **`rehabilitated.json`** | Active validators slashed in the past but no longer in the latest enforcement batch — Marinade has removed them from active enforcement. Their history is preserved here so consumers can see "this wallet has a past offense even though it's not currently enforced against." |
| **`stats.json`** | Aggregate counters — `active_counts`, `historical_counts`, `latest_enforcement_per_reason`, total slashed SOL. Useful for dashboards / status badges. |
| **`health.json`** | One-shot pipeline-health view. Includes `last_successful_refresh`, `marinade_events_pulled`, `blacklist_size`, and `epochs_since_latest_hard`/`_soft`. Consumers should check this before trusting the lists — stale `last_successful_refresh` = pipeline degraded. |
| **`recent.json`** | Top 50 currently-blacklisted validators sorted by `last_slashed_epoch` desc. Most-recently-enforced first. |
| **`by_epoch.json`** | `{ epoch: [vote_accounts] }` map. For time-series queries. Includes all historical validators (active and deregistered). |

### Historical / audit

| File | Contents |
|------|---------|
| **`historical.json`** | Every validator ever slashed by Marinade for blacklist/risk reasons, including those that have since been deregistered. Full audit trail. |
| **`history/<date>.json`** | Daily snapshot of the full state. Lets you reconstruct what the list looked like on any given day. |

### Why so many files?

Each file is optimized for one query pattern, so consumers don't have
to parse the full JSON when they want a simple set-membership check.
Pick the one that matches your use case:

- **Routing decision** (skip block-leader if blacklisted): `vote_accounts_hard.txt` or `identities_hard.txt` — fetch once, build a `set`, one-line check.
- **Sales / research review**: open `blacklist.csv` on GitHub — sortable table view.
- **Dashboard / monitoring**: `stats.json` for the counters.
- **"Has this validator ever been slashed?"**: `historical.json`.

## Per-validator schema

```json
{
  "vote_account": "EJHf5N9is5spAF...",
  "validator_identity": "4uH4G6YiD5G8rU3mtPg73C2Uqamrqedy3FboTZcZrh6x",
  "severity": "hard",
  "still_blacklisted": true,
  "is_active": true,
  "current_stake_sol": 144646.7,
  "commission_pct": 10,
  "last_vote_slot": 312456789,
  "slashed_sol": 75.34,
  "slashed_lamports": 75340000000,
  "event_count": 1,
  "first_slashed_epoch": 946,
  "last_slashed_epoch": 946,
  "epochs_since_last_slash": 25,
  "slashed_epochs": [946],
  "reasons": ["BlacklistPenalty"]
}
```

### Key fields

| Field | What it means |
|-------|---------------|
| **`vote_account`** | The validator's vote account pubkey. Stable identifier. Always populated. |
| **`validator_identity`** | The validator's node pubkey (signing key). Only set for validators in the current `getVoteAccounts` response. Use this when your routing keys off identity. |
| **`severity`** | `hard` = `BlacklistPenalty` (Marinade SAM blacklist — sandwich/abuse). `soft` = `BondRiskFee` (flagged but not full blacklist). |
| **`still_blacklisted`** | `true` if the validator appears in the LATEST enforcement batch for their reason type. Validators in `blacklist.json` always have `still_blacklisted: true` by definition; those in `rehabilitated.json` always have `false`. |
| **`is_active`** | `true` if the validator is in the current Solana validator set (per `getVoteAccounts`). Deregistered validators are `false` and live only in `historical.json`. |
| **`current_stake_sol`** | Total stake currently delegated to them — from ALL sources (Marinade + Jito + individuals + CEXes). This is what determines their leader-slot frequency, so it's what matters for routing-risk assessment. |
| **`epochs_since_last_slash`** | How many epochs since the validator's last slashing event, vs. the network's latest enforcement epoch. Large = probably rehabilitated. |
| **`slashed_epochs`** | Every epoch the validator was slashed in. |
| **`reasons`** | Unique reason categories the validator was slashed under — `BlacklistPenalty` and/or `BondRiskFee`. |

## What counts as "blacklisted"

We include events with `reason` in:

| Reason | Severity | Meaning |
|--------|----------|---------|
| `BlacklistPenalty` | **hard** | Marinade's SAM has slashed this validator's bond for being on the community-curated blacklist. Strongest "this validator is malicious" signal Marinade publishes. |
| `BondRiskFee`      | **soft** | Marinade has charged this validator a risk fee — flagged for risky behavior. Not as severe as `BlacklistPenalty` but worth surfacing. |

Reasons explicitly **excluded** (these aren't malicious-behavior signals):

| Reason | Why excluded |
|--------|--------------|
| `Bidding` | Normal SAM CPM bid charging — what every validator pays to bid for stake. |
| `ProtectedEvent` | Downtime / missed-credits compensation — performance, not malice. |
| `PriorityFee` | Priority-fee bookkeeping. |
| `BidTooLowPenalty` | Penalty for bidding too low at auction. Not malicious. |

If Marinade adds new reason categories, edit the `SEVERITY` map at
the top of `refresh.py` to include them.

## How rehabilitation works

Marinade re-evaluates the blacklist each enforcement batch (every few
epochs). A validator stops appearing in new `BlacklistPenalty` /
`BondRiskFee` events once Marinade removes them from active
enforcement.

We detect this by tracking the **latest enforcement epoch per reason
type** and flagging validators not in that latest batch as
`still_blacklisted: false`. The pipeline then:

1. Drops them from `blacklist.json`, `hard_only.json`, and all
   plain-text consumer lists.
2. Moves them into `rehabilitated.json` so their history is still
   visible.
3. Keeps them in `historical.json` for the full audit trail.

This means: **if Marinade un-blacklists a validator, they
automatically drop off the lists we publish.** No manual maintenance.

## Update cadence

- Refresh runs **every hour, top of the hour, UTC** via GitHub
  Actions.
- Marinade publishes new `protected-events` per-epoch (~3 days), so
  most hourly runs produce no diff and the workflow skips the commit.
- When new entries appear (or rehabilitated entries drop off), the
  workflow commits and pushes within an hour.

## Run locally

```sh
python3 refresh.py
```

Outputs go to `data/`. No third-party dependencies — Python 3.10+
stdlib only.

To use a private RPC instead of the public default (the script uses
`getVoteAccounts`, which the public RPC handles fine but slowly):

```sh
RPC_URL="https://your-rpc.example.com" python3 refresh.py
```

In the GitHub Action, set a repo secret named `RPC_URL` and the
workflow will use it automatically.

## Robustness

The script has three protections against bad refresh runs:

1. **Multi-RPC fallback**: tries a primary RPC then falls through a
   chain of free public providers (Solana foundation, PublicNode,
   Ankr). All public — no private endpoints exposed in the workflow.
2. **Per-URL retries**: 2 attempts per RPC with exponential backoff
   before falling through to the next URL.
3. **Empty-write guard**: if every validator comes back
   `is_active=False` AND a prior `blacklist.json` had entries, the
   script exits non-zero rather than overwriting the data with an
   empty result. The workflow then skips the commit and preserves the
   last good state.

## Methodology details

See [`docs/methodology.md`](docs/methodology.md) for the detailed
reason classification logic and explanation of how aggregation works
across multiple events per validator.

## License

Public domain. Fork it, embed it, build on top of it.
