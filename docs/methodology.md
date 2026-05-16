# Methodology

## Data source

The single source of truth is Marinade Finance's public
`protected-events` API:

```
https://validator-bonds-api.marinade.finance/protected-events
```

Every event is one entry in Marinade's validator-bonds settlement
pipeline. Events have a `vote_account`, `epoch`, `amount` (in
lamports), and a `reason` field. See the upstream
[validator-bonds repo](https://github.com/marinade-finance/validator-bonds)
for the full schema.

## Reason classification

The API returns events for many reasons — most are normal SAM
bookkeeping, not slashing. This project includes only the two reasons
that signal malicious or risky behavior:

| Reason             | Severity | What it means |
|--------------------|----------|---------------|
| `BlacklistPenalty` | **hard** | Marinade's SAM blacklist (community-curated, catches sandwiching and similar) has flagged this validator, and Marinade has slashed the bond as penalty. Strongest "this validator is malicious" signal Marinade publishes. |
| `BondRiskFee`      | **soft** | Marinade has charged this validator a risk fee. Indicates flagged behavior — somewhere between "noticed" and "slashed". Not as strong as `BlacklistPenalty`, but worth surfacing. |

Reasons explicitly **excluded**:

| Reason             | Why excluded |
|--------------------|--------------|
| `Bidding`          | Normal CPM bid charging — what every validator pays to bid for stake. Not slashing. |
| `ProtectedEvent`   | Downtime / missed-credits compensation. Validators are paying out for performance issues, not malicious behavior. |
| `PriorityFee`      | Priority-fee related bookkeeping. |
| `BidTooLowPenalty` | Penalty for bidding too low at auction. Not malicious. |

If Marinade adds new reason categories in the future and they fit the
"malicious or risky" bucket, edit the `SEVERITY` map at the top of
`refresh.py` to include them.

## Aggregation

Multiple events for the same validator are rolled into a single record
per `vote_account`, with:

- Severity = `hard` if *any* event for that validator is
  `BlacklistPenalty`. Otherwise `soft`.
- `slashed_lamports` and `slashed_sol` = sum across all included events.
- `slashed_epochs` = sorted list of every epoch the validator had an
  event in.
- `event_count` = total count of included events.
- `reasons` = sorted list of unique reason categories seen.

## Identity resolution

Marinade keys everything by `vote_account` (the validator's vote
account pubkey). Most routing logic in production tools keys off
`validator_identity` (the validator's node pubkey, i.e. the signing
key). This project resolves the mapping via a single
`getVoteAccounts` RPC call — `getVoteAccounts` returns the full set of
1,500+ validators with both pubkeys in a single ~1 MB response, so the
cost is negligible.

If the RPC call fails (rate limit, transient network issue), the
script still writes the file with `validator_identity: null` on
affected rows — consumers can fall back to `vote_account` matching.

## Sort order

Output `validators` array is sorted by `slashed_lamports` descending,
then `vote_account` lexicographic. Largest slashings first.

## What this list is NOT

- **It's not a comprehensive sandwich detector.** Marinade's SAM
  blacklist is one of several published sources. Validators caught
  sandwiching only show up here if Marinade slashed them — a sandwicher
  not delegated through SAM won't be in this dataset.
- **It's not real-time.** Marinade's settlement pipeline runs per-epoch
  (~3 days). New entries appear at the start of each epoch's settlement
  process. The hourly cron in this repo catches them within an hour
  of publication.
- **It's not a complete validator score.** Many validators have other
  performance issues (delinquency, missed credits) that don't trigger
  `BlacklistPenalty`. This file only captures the *malicious-behavior*
  slashing signal.

For a broader validator-health view, combine this list with downtime
data, MEV revenue numbers, and other validator metrics from
Stakewiz / Vincibles / Trillium.
