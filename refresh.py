#!/usr/bin/env python3
"""Dynamic Solana validator blacklist generator.

Pulls Marinade Finance's published ``protected-events`` feed, filters for
events that indicate malicious or risky validator behavior, and writes
machine-readable + human-readable blacklist files.

Two severities:
  - ``hard``  — ``BlacklistPenalty`` events.  Marinade's Stake Auction
                Marketplace (SAM) has slashed the validator's bond for
                being on its blacklist, which is community-curated to
                catch sandwiching and similar abuse.  Definitively bad.
  - ``soft``  — ``BondRiskFee`` events.  Marinade has charged the
                validator a risk fee.  Indicative of flagged behavior
                but not full blacklist-grade.

Output files written to ``data/``:
  - ``blacklist.json`` — canonical machine-readable union of both severities
  - ``blacklist.csv``  — same data, spreadsheet-friendly
  - ``hard_only.json`` — strict slashed-by-blacklist subset
  - ``history/<date>.json`` — daily snapshot for audit trail

Runs from anywhere — defaults to the public Solana RPC so it works
without a private endpoint.  Override with ``RPC_URL`` env var if you
want to use your own node.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

MARINADE_API = "https://validator-bonds-api.marinade.finance/protected-events"
RPC_URL = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")

# Reason → severity mapping.  Reasons NOT listed here are ignored
# (e.g. ``Bidding`` is normal SAM bid charging, not slashing).
SEVERITY: dict[str, str] = {
    "BlacklistPenalty": "hard",
    "BondRiskFee": "soft",
}

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"


def http_get_json(url: str, *, timeout: int = 60, retries: int = 3) -> Any:
    """Plain HTTP GET → JSON, with retries on transient network failure."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(2 ** attempt)
        try:
            req = Request(url, headers={"User-Agent": "marinade_blacklist/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except (URLError, json.JSONDecodeError, TimeoutError) as exc:
            last_exc = exc
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_exc}")


def rpc_call(method: str, params: list, *, timeout: int = 60) -> Any:
    """Solana JSON-RPC POST.  Returns the ``result`` field on success."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    }).encode()
    req = Request(
        RPC_URL, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    if "error" in payload:
        raise RuntimeError(f"RPC error: {payload['error']}")
    return payload.get("result")


def fetch_protected_events() -> list[dict]:
    """Pull every protected-event from Marinade's public API."""
    data = http_get_json(MARINADE_API)
    return data.get("protected_events") or []


def reason_label(reason: Any) -> str:
    """Marinade uses both bare-string and tagged-dict reasons.
    Normalize to the outer tag name so ``{'ProtectedEvent': {...}}``
    becomes ``'ProtectedEvent'``.
    """
    if isinstance(reason, dict):
        return next(iter(reason.keys()), "")
    return str(reason or "")


def aggregate_events(events: list[dict]) -> dict[str, dict]:
    """Roll events up per (vote_account, severity).

    A validator that appears in both ``BlacklistPenalty`` and
    ``BondRiskFee`` is recorded with severity=hard (the stricter wins).
    """
    by_vote: dict[str, dict] = {}
    for event in events:
        sev = SEVERITY.get(reason_label(event.get("reason")))
        if sev is None:
            continue
        vote = event.get("vote_account")
        if not vote:
            continue
        rec = by_vote.setdefault(vote, {
            "vote_account": vote,
            "validator_identity": None,
            "severity": sev,
            "slashed_lamports": 0,
            "slashed_sol": 0.0,
            "event_count": 0,
            "epochs": set(),
            "reasons": set(),
        })
        # Upgrade severity to hard if either severity hits hard.
        if sev == "hard":
            rec["severity"] = "hard"
        rec["slashed_lamports"] += int(event.get("amount", 0) or 0)
        rec["event_count"] += 1
        if event.get("epoch") is not None:
            rec["epochs"].add(int(event["epoch"]))
        rec["reasons"].add(reason_label(event.get("reason")))
    for rec in by_vote.values():
        rec["slashed_sol"] = round(rec["slashed_lamports"] / 1e9, 4)
        epochs = sorted(rec["epochs"])
        rec["first_slashed_epoch"] = epochs[0] if epochs else None
        rec["last_slashed_epoch"] = epochs[-1] if epochs else None
        rec["slashed_epochs"] = epochs
        rec["reasons"] = sorted(rec["reasons"])
        rec.pop("epochs", None)
    return by_vote


def resolve_vote_account_info(vote_accounts: list[str]) -> dict[str, dict]:
    """Map vote_account → full validator info via getVoteAccounts.

    One RPC call covers all ~1500 validators.  Returns a dict per
    vote_account with:
      - validator_identity (nodePubkey)
      - current_stake_lamports (activatedStake)
      - commission_pct
      - last_vote_slot
      - is_active (True if in ``current`` bucket, False if delinquent)
    If the RPC fails, returns empty dict — enrichment is best-effort.
    """
    try:
        result = rpc_call("getVoteAccounts", [])
    except Exception as exc:
        print(f"warning: getVoteAccounts failed: {exc}", file=sys.stderr)
        return {}
    mapping: dict[str, dict] = {}
    for bucket in ("current", "delinquent"):
        is_active = (bucket == "current")
        for v in (result or {}).get(bucket, []) or []:
            vp = v.get("votePubkey")
            if not vp:
                continue
            mapping[vp] = {
                "validator_identity": v.get("nodePubkey"),
                "current_stake_lamports": int(v.get("activatedStake", 0) or 0),
                "commission_pct": v.get("commission"),
                "last_vote_slot": v.get("lastVote"),
                "is_active": is_active,
            }
    return mapping


def write_outputs(records: list[dict]) -> None:
    """Write the canonical blacklist files into ``data/``."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_epochs = sorted({e for r in records for e in r.get("slashed_epochs", [])})
    epoch_range = [all_epochs[0], all_epochs[-1]] if all_epochs else [None, None]

    full = {
        "generated_at": now_iso,
        "source": MARINADE_API,
        "methodology": (
            "Events fetched from Marinade Finance's validator-bonds "
            "protected-events feed.  BlacklistPenalty = hard severity "
            "(definitively slashed for being on SAM blacklist).  "
            "BondRiskFee = soft severity (flagged, risk fee charged).  "
            "Other reasons (Bidding, ProtectedEvent, PriorityFee, "
            "BidTooLowPenalty) are NOT in this list."
        ),
        "epoch_range": epoch_range,
        "counts": {
            "total": len(records),
            "hard": sum(1 for r in records if r["severity"] == "hard"),
            "soft": sum(1 for r in records if r["severity"] == "soft"),
        },
        "validators": sorted(
            records,
            key=lambda r: (-int(r["slashed_lamports"]), r["vote_account"]),
        ),
    }

    (DATA_DIR / "blacklist.json").write_text(
        json.dumps(full, indent=2, sort_keys=False) + "\n"
    )

    # Hard-only subset — what most consumers actually want
    hard_records = [r for r in records if r["severity"] == "hard"]
    hard = {**full,
            "counts": {"total": len(hard_records), "hard": len(hard_records), "soft": 0},
            "validators": [r for r in full["validators"] if r["severity"] == "hard"]}
    (DATA_DIR / "hard_only.json").write_text(
        json.dumps(hard, indent=2) + "\n"
    )

    # CSV for spreadsheet use
    csv_fields = [
        "severity", "vote_account", "validator_identity",
        "is_active", "current_stake_sol", "commission_pct",
        "slashed_sol", "slashed_lamports", "event_count",
        "first_slashed_epoch", "last_slashed_epoch",
        "last_vote_slot", "reasons",
    ]
    with open(DATA_DIR / "blacklist.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=csv_fields, extrasaction="ignore")
        w.writeheader()
        for r in full["validators"]:
            row = dict(r)
            row["reasons"] = ",".join(r.get("reasons", []))
            w.writerow(row)

    # Plain-text identifier lists — the cheapest possible consumer
    # path.  Loaded as a Python set in one line:
    #     banned = set(open("vote_accounts_hard.txt").read().split())
    # vote_account is the canonical key (every record has one).
    # validator_identity is only populated for ~35% of records — the
    # rest are delinquent/deregistered validators that don't appear in
    # ``getVoteAccounts`` any more.
    def write_lines(name: str, items: list[str]) -> None:
        items = sorted(set(s for s in items if s))
        (DATA_DIR / name).write_text("\n".join(items) + ("\n" if items else ""))

    write_lines("vote_accounts_hard.txt",
                [r["vote_account"] for r in records if r["severity"] == "hard"])
    write_lines("vote_accounts_all.txt",
                [r["vote_account"] for r in records])
    write_lines("identities_hard.txt",
                [r["validator_identity"] for r in records
                 if r["severity"] == "hard" and r["validator_identity"]])
    write_lines("identities_all.txt",
                [r["validator_identity"] for r in records if r["validator_identity"]])

    # ── Derived endpoint files (each optimised for one query pattern) ──

    # stats.json — aggregate counters for dashboards / status badges.
    total_stake_sol = round(
        sum(r.get("current_stake_sol") or 0 for r in full["validators"]), 2
    )
    active_count = sum(1 for r in full["validators"] if r.get("is_active"))
    stats = {
        "generated_at": now_iso,
        "source": MARINADE_API,
        "epoch_range": epoch_range,
        "counts": full["counts"],
        "active_validators": active_count,
        "deregistered_validators": full["counts"]["total"] - active_count,
        "total_slashed_sol": round(
            sum(r["slashed_lamports"] for r in full["validators"]) / 1e9, 2
        ),
        "currently_staked_sol": total_stake_sol,
    }
    (DATA_DIR / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")

    # by_epoch.json — { epoch: [vote_account, ...] } for time-series.
    by_epoch: dict[str, list[str]] = defaultdict(list)
    for r in full["validators"]:
        for e in r.get("slashed_epochs", []):
            by_epoch[str(e)].append(r["vote_account"])
    by_epoch_sorted = {k: sorted(set(v)) for k, v in sorted(by_epoch.items(),
                       key=lambda kv: int(kv[0]))}
    (DATA_DIR / "by_epoch.json").write_text(
        json.dumps({
            "generated_at": now_iso,
            "by_epoch": by_epoch_sorted,
        }, indent=2) + "\n"
    )

    # recent.json — top 50 sorted by last_slashed_epoch desc.  Easiest
    # answer to "what's been added lately?"
    recent_sorted = sorted(
        full["validators"],
        key=lambda r: (-(r.get("last_slashed_epoch") or 0),
                       -r["slashed_lamports"]),
    )[:50]
    (DATA_DIR / "recent.json").write_text(
        json.dumps({
            "generated_at": now_iso,
            "epoch_range": epoch_range,
            "count": len(recent_sorted),
            "validators": recent_sorted,
        }, indent=2) + "\n"
    )

    # active_only.json — subset still in the current validator set.
    # This is the "actually dangerous right now" list for routing
    # decisions — deregistered validators can't sandwich anyone.
    active_records = [r for r in full["validators"] if r.get("is_active")]
    (DATA_DIR / "active_only.json").write_text(
        json.dumps({
            "generated_at": now_iso,
            "source": MARINADE_API,
            "count": len(active_records),
            "validators": active_records,
        }, indent=2) + "\n"
    )

    # currently_blacklisted.json — validators that appear in the LATEST
    # epoch's events.  This is the "still on Marinade's blacklist
    # right now" signal — Marinade re-evaluates each epoch, so a
    # validator that stops appearing in new epochs has been removed
    # (or rehabilitated).  Done per severity so consumers can pick:
    # "currently_hard" = on the blacklist this epoch; "currently_soft"
    # = currently being charged BondRiskFee.
    by_severity_max_epoch: dict[str, int] = {}
    for r in records:
        if not r["slashed_epochs"]:
            continue
        # Reasons array contains either BlacklistPenalty or BondRiskFee
        # (or both — but severity already collapsed to "hard" if both).
        for sev_key, reason_tag in (("hard", "BlacklistPenalty"),
                                     ("soft", "BondRiskFee")):
            if reason_tag in r.get("reasons", []):
                cur = by_severity_max_epoch.get(sev_key, 0)
                by_severity_max_epoch[sev_key] = max(cur, max(r["slashed_epochs"]))

    def currently(reason_tag: str, latest_epoch: int) -> list[dict]:
        """Validators with `reason_tag` event in latest_epoch."""
        out = []
        for r in records:
            if reason_tag in r.get("reasons", []) and latest_epoch in r.get("slashed_epochs", []):
                out.append(r)
        return out

    latest_hard_epoch = by_severity_max_epoch.get("hard")
    latest_soft_epoch = by_severity_max_epoch.get("soft")
    currently_hard = currently("BlacklistPenalty", latest_hard_epoch) if latest_hard_epoch else []
    currently_soft = currently("BondRiskFee", latest_soft_epoch) if latest_soft_epoch else []
    # Union: a validator with BlacklistPenalty in latest hard epoch OR
    # BondRiskFee in latest soft epoch.  Dedupe by vote_account.
    seen = set()
    currently_all = []
    for r in currently_hard + currently_soft:
        if r["vote_account"] in seen:
            continue
        seen.add(r["vote_account"])
        currently_all.append(r)

    (DATA_DIR / "currently_blacklisted.json").write_text(
        json.dumps({
            "generated_at": now_iso,
            "source": MARINADE_API,
            "latest_hard_epoch": latest_hard_epoch,
            "latest_soft_epoch": latest_soft_epoch,
            "counts": {
                "total": len(currently_all),
                "hard": len(currently_hard),
                "soft": len(currently_soft),
            },
            "note": (
                "Validators that appear in the LATEST epoch's events.  "
                "Marinade re-evaluates the blacklist each epoch; a validator "
                "that stops appearing in new epochs has been removed."
            ),
            "validators": currently_all,
        }, indent=2) + "\n"
    )

    # Plain-text versions of the currently_blacklisted set
    write_lines("currently_blacklisted_vote_accounts.txt",
                [r["vote_account"] for r in currently_all])
    write_lines("currently_blacklisted_hard.txt",
                [r["vote_account"] for r in currently_hard])

    # Append daily snapshot to history (overwrites within the same day)
    snap_path = HISTORY_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    snap_path.write_text(json.dumps(full, indent=2) + "\n")

    print(
        f"Wrote {len(records)} validators "
        f"({full['counts']['hard']} hard, {full['counts']['soft']} soft) "
        f"to {DATA_DIR}",
        file=sys.stderr,
    )


def main() -> int:
    print(f"[{datetime.now(timezone.utc).isoformat()}] fetching Marinade events...",
          file=sys.stderr)
    events = fetch_protected_events()
    print(f"  got {len(events)} protected events", file=sys.stderr)

    by_vote = aggregate_events(events)
    print(f"  {len(by_vote)} unique validators after filter", file=sys.stderr)

    print(f"  resolving vote-account info via {RPC_URL}...", file=sys.stderr)
    vote_info = resolve_vote_account_info(list(by_vote.keys()))
    matched = 0
    for vote, rec in by_vote.items():
        info = vote_info.get(vote)
        if info:
            rec["validator_identity"] = info["validator_identity"]
            rec["current_stake_sol"] = round(info["current_stake_lamports"] / 1e9, 4)
            rec["commission_pct"] = info["commission_pct"]
            rec["last_vote_slot"] = info["last_vote_slot"]
            rec["is_active"] = info["is_active"]
            matched += 1
        else:
            rec["validator_identity"] = None
            rec["current_stake_sol"] = None
            rec["commission_pct"] = None
            rec["last_vote_slot"] = None
            rec["is_active"] = False  # not in cluster's current/delinquent set → deregistered
    print(f"  matched {matched} of {len(by_vote)} blacklisted vote_accounts "
          f"to current cluster state ({len(vote_info)} total validators in cluster)",
          file=sys.stderr)

    records = list(by_vote.values())
    write_outputs(records)
    print("done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
