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


# Match-embedded home_rank/home_points are frozen as of that match.
# - Stale embeds (no recent matches): real rankings decay while the snapshot
#   stays high (e.g. Mboko still showing June's #9 / 3710 in September) → use
#   the in-window replay sum.
# - Embeds also lag the Monday ranking update after a tournament week ends
#   (Sunday final still carries pre-update points on Monday). Once a ranking
#   Monday has passed since the player's last match, trust the replay — for
#   both ATP and WTA.
EMBED_STALE_DAYS = 14


def _ranking_mondays_since(last_match_day: date, as_of: date) -> int:
    """Count ATP/WTA ranking Mondays strictly after last_match_day through as_of."""
    if as_of <= last_match_day:
        return 0
    # First Monday strictly after the match day.
    days_until_mon = (7 - last_match_day.weekday()) % 7
    first_monday = last_match_day + timedelta(days=days_until_mon or 7)
    if first_monday > as_of:
        return 0
    return (as_of - first_monday).days // 7 + 1


def resolve_ranking_points(last_match_ts, latest_ts, official_pts, computed_pts, stale_days=EMBED_STALE_DAYS, as_of=None):
    """Pick embed points or computed tourney sum for the Points column."""
    if not last_match_ts:
        return computed_pts
    as_of = as_of or date.today()
    last_day = date.fromtimestamp(int(last_match_ts))
    age_days = (as_of - last_day).days
    if age_days > stale_days:
        return computed_pts
    # Match embeds freeze mid-event; Monday's published ranking is the truth.
    if _ranking_mondays_since(last_day, as_of) >= 1:
        return computed_pts
    if computed_pts > official_pts:
        return computed_pts
    return official_pts


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
