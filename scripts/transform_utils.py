#!/usr/bin/env python3
"""
Shared transform helpers: tennisdata.app CSV fields → frontend-friendly values.

CSVs are never modified; these helpers only change what we write into rankings.json.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta


_INITIAL_RE = re.compile(r"^[A-Za-z]\.?$")


def display_name(raw: str) -> str:
    """
    Convert tennisdata.app 'Last F.' / 'De Minaur A.' into 'F. Last' / 'A. De Minaur'.

    The frontend short-labels players with the last whitespace token; 'Sinner J.'
    therefore rendered as 'J.'. Flipping initials first restores usable labels
    ('Sinner') without needing a separate name dictionary.
    """
    raw = (raw or "").strip()
    if not raw:
        return raw
    parts = raw.split()
    if len(parts) < 2:
        return raw

    initials = []
    i = len(parts) - 1
    while i >= 0 and _INITIAL_RE.fullmatch(parts[i]):
        tok = parts[i]
        initials.append(tok if tok.endswith(".") else f"{tok}.")
        i -= 1
    if not initials or i < 0:
        return raw
    last = " ".join(parts[: i + 1])
    return f"{' '.join(reversed(initials))} {last}"


def title_surface(raw: str) -> str:
    s = (raw or "").strip()
    return s[:1].upper() + s[1:].lower() if s else s


# Match-embedded home_rank/home_points are frozen as of that match. Players who
# haven't competed recently keep an inflated snapshot while real tour rankings
# decay (e.g. Mboko still showing June's #9 / 3710 in September). Prefer the
# embed when it's fresh; otherwise fall back to our in-window replay sum.
EMBED_STALE_DAYS = 14


def resolve_ranking_points(last_match_ts, latest_ts, official_pts, computed_pts, stale_days=EMBED_STALE_DAYS):
    """Return official embed points if recent enough, else computed tourney sum."""
    if not last_match_ts or not latest_ts:
        return computed_pts
    age_days = (latest_ts - last_match_ts) / 86400.0
    if age_days <= stale_days:
        return official_pts
    return computed_pts


def build_weekly_rankings(all_tour_rows, cutoff_ts: int, end_ts: int, max_rank: int = 500):
    """
    Approximate official Monday ranking lists from match-embedded ranks.

    Timelines are seeded from ALL tour rows (not only the 52-week window) so
    early weeks in the window are already dense. Sparse weeks (<50 players)
    are dropped so the bump chart never starts on near-empty snapshots.
    """
    timelines = defaultdict(list)
    for r in all_tour_rows:
        if r.get("status") != "FINISHED":
            continue
        try:
            ts = int(r.get("date_timestamp", "0"))
        except ValueError:
            continue
        for side in ("home", "away"):
            pid = r.get(f"{side}_id", "")
            rank = r.get(f"{side}_rank")
            pts = r.get(f"{side}_points")
            if not pid or rank in (None, ""):
                continue
            try:
                timelines[pid].append((ts, int(rank), int(pts) if pts not in (None, "") else 0))
            except ValueError:
                continue

    for pid in timelines:
        timelines[pid].sort(key=lambda x: x[0])

    if not end_ts:
        return []

    start_d = date.fromtimestamp(cutoff_ts)
    end_d = date.fromtimestamp(end_ts)
    # Monday on or before the cutoff week, then step weekly (official lists are Mondays).
    d = start_d - timedelta(days=start_d.weekday())

    weekly_list = []
    while d <= end_d:
        cursor_ts = int(datetime.combine(d, datetime.min.time()).timestamp())
        snapshot = []
        for pid, points_list in timelines.items():
            past = None
            for entry in points_list:
                if entry[0] <= cursor_ts:
                    past = entry
                else:
                    break
            if past and past[1] <= max_rank:
                snapshot.append({"id": pid, "rank": past[1], "points": past[2]})
        if len(snapshot) >= 50:
            snapshot.sort(key=lambda x: x["rank"])
            # De-dupe ranks if two players share a stale embedded rank; keep best points.
            seen_ranks = set()
            cleaned = []
            for row in snapshot:
                if row["rank"] in seen_ranks:
                    continue
                seen_ranks.add(row["rank"])
                cleaned.append(row)
                if len(cleaned) >= max_rank:
                    break
            weekly_list.append({"date": d.strftime("%Y%m%d"), "rankings": cleaned})
        d += timedelta(days=7)

    return weekly_list
