#!/usr/bin/env python3
"""
Transforms tennisdata.app WTA season CSV(s) → data/wta/rankings.json.

Mirrors process_data.py. CSVs are never modified; output matches the frontend
contract (display names, surfaces, dense weekly bump snapshots).
"""

import csv
import glob
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transform_utils import build_weekly_rankings, display_name as format_player_name, title_surface

# "RR" (round-robin, used only by the season-ending WTA Finals) sits at the
# bottom: it must outrank the "-1" not-yet-reached sentinel so an RR-only
# result (eliminated in the group stage, never reaching the SF/F bracket)
# actually registers instead of silently scoring 0.
ROUND_ORDER = ["RR", "R128", "R64", "R32", "R16", "QF", "SF", "F", "W"]
ROUND_RANK = {r: i for i, r in enumerate(ROUND_ORDER)}

# WTA points schedule — confirmed against the official WTA points-distribution
# table (en.wikipedia.org/wiki/WTA_rankings#Ranking_method). Notably the
# "1000" table's R32-and-deeper values are IDENTICAL for both the 96-draw and
# 56-draw variants — only the true first round differs (R128=10 vs R64=10,
# which are themselves equal) — so unlike the ATP script, WTA doesn't need a
# separate "small draw" table at all; the same bye-detection logic used for
# Masters below naturally lands on the right value either way.
# "F" (WTA Finals) isn't used for scoring directly — see the additive
# per-round-robin-win handling in the tournament loop below, matching the
# real "1500 for undefeated champion = 3x200 RR wins + 400 SF win + 500 F
# win" structure (identical shape to the ATP Finals, per the same source).
POINTS = {
    "G":    {"W": 2000, "F": 1300, "SF": 780,  "QF": 430, "R16": 240, "R32": 130, "R64": 70, "R128": 10},
    "1000": {"W": 1000, "F": 650,  "SF": 390,  "QF": 215, "R16": 120, "R32": 65,  "R64": 35, "R128": 10},
    "500":  {"W": 500,  "F": 325,  "SF": 195,  "QF": 108, "R16": 60,  "R32": 32},
    "250":  {"W": 250,  "F": 163,  "SF": 98,   "QF": 54,  "R16": 30,  "R32": 1},
}

ROUND_MAP = {
    "1/64-finals": "R128",
    "1/32-finals": "R64",
    "1/16-finals": "R32",
    "1/8-finals": "R16",
    "Quarter-finals": "QF",
    "Semi-finals": "SF",
    "Final": "F",
    "": "RR",
}

GRAND_SLAMS = {"Australian Open", "French Open", "Roland Garros", "Wimbledon", "US Open"}

# WTA 1000-level events — roster has shifted a bit year to year; verify against
# the current WTA calendar if scoring looks off for one of these.
WTA_1000 = {
    "Indian Wells", "Miami", "Madrid", "Rome", "Montreal", "Toronto",
    "Cincinnati", "Wuhan", "Beijing", "Doha", "Dubai",
}

WTA_FINALS_NAMES = {"WTA Finals"}

# tennisdata.app splits the season-ending Finals into two separate "tournament"
# entries: "Finals - <city> WTA (indoor)" (round-robin stage) and
# "Finals - <city> WTA - Play Offs (indoor)" (SF/F knockout stage) — both need to
# be recognized as category "F" and merged into one tourney key.
def is_tour_finals(raw_name):
    return raw_name.startswith("Finals - ") and " WTA" in raw_name


# United Cup is a mixed-team event whose singles rubbers carry real ranking
# points for BOTH tours from the same unified formula (a flat amount per win,
# based on the opponent's ranking tier — see UNITED_CUP_TABLE). This mirrors
# process_data.py's ATP implementation exactly; the tier values themselves
# are carried over unconfirmed against a real WTA breakdown page specifically
# (they were sourced from Wikipedia's general United Cup points table, not
# verified against a WTA player's page the way the ATP tiers were) — worth
# double-checking against a real WTA page if a player's total looks off by a
# small, United-Cup-shaped amount.
def is_united_cup(raw_name):
    return "United Cup" in raw_name


def is_united_cup_playoffs(raw_name):
    return is_united_cup(raw_name) and "Play Offs" in raw_name


UNITED_CUP_TABLE = {
    "group": ([(10, 55), (20, 45), (30, 40), (50, 35), (100, 25), (250, 20)], 15),
    "QF":    ([(10, 80), (20, 65), (30, 55), (50, 40), (100, 35), (250, 25)], 20),
    "SF":    ([(10, 130), (20, 105), (30, 90), (50, 60), (100, 40), (250, 35)], 25),
    "F":     ([(10, 180), (20, 140), (30, 120), (50, 90), (100, 60), (250, 40)], 35),
}


def united_cup_points(opponent_rank, stage="group"):
    tiers, unranked_pts = UNITED_CUP_TABLE.get(stage, UNITED_CUP_TABLE["group"])
    for max_rank, pts in tiers:
        if opponent_rank <= max_rank:
            return pts
    return unranked_pts

# WTA 500-level tournaments identified by name. Sackmann's source gave this tier
# explicitly (level "P"); tennisdata.app doesn't, so this list has to be maintained
# by hand — double-check against the current WTA calendar if scoring looks off for
# one of these (roster shifts a little year to year).
WTA_500_NAME_OVERRIDES = {
    "Strasbourg", "Adelaide", "Abu Dhabi", "Berlin", "Charleston", "Stuttgart",
    "Eastbourne", "Washington", "Guadalajara", "Tokyo", "Ningbo", "Hamburg",
}

# Real WTA cap structure (per en.wikipedia.org/wiki/WTA_rankings — genuinely
# DIFFERENT from the ATP structure, not just different numbers): 4 Grand
# Slams + the WTA Finals bonus + ALL WTA 1000 results a player earns (the "6
# combined + 1 WTA-only mandatory" tournaments are described as an
# unconditional tier, with no ATP-style "replace a weak one with a later
# 500/250" mechanism) + the player's best 7 results from United Cup/500/250
# (the source also lists 125s and ITF W15+ in this pool, which this pipeline
# doesn't track). This is NOT the same cap arithmetic as ATP's — no
# replacement rule, and the "best of the rest" pool is 7 slots, not 14, since
# the WTA 1000s aren't competing in it at all. Unconfirmed against a real WTA
# breakdown page as of this writing (the ATP version of this logic went
# through several rounds of real-page validation before landing correctly —
# this WTA port hasn't yet, so it's worth checking the first time a total
# looks off).
BEST_OTHER_POOL_CAP = 7

EXCLUDED_NAME_SUBSTR = ("Billie Jean King Cup", "WIC")
# "WIC" = wildcard playoff (e.g. into the US Open) — not a ranking-points event.
# (United Cup used to be excluded here too — it's a team event that looked
# like it shouldn't carry ranking points, but it does; see is_united_cup.)


def clean_tournament_name(raw):
    name = raw
    if "(" in name:
        name = name.split("(")[0].strip()
    if name.endswith(" WTA"):
        name = name[: -len(" WTA")]
    return name.strip()


def tourney_category(raw_name):
    if any(s in raw_name for s in EXCLUDED_NAME_SUBSTR):
        return None
    if is_tour_finals(raw_name):
        return "F"
    if is_united_cup(raw_name):
        return "UC"
    name = clean_tournament_name(raw_name)
    if name in GRAND_SLAMS:
        return "G"
    if name in WTA_FINALS_NAMES:
        return "F"
    if name in WTA_1000:
        return "1000"
    if name in WTA_500_NAME_OVERRIDES:
        return "500"
    return "250"


def points_for_result(category, round_code, won_final, bye_downgrade=False, true_first_round=None):
    if won_final:
        round_code = "W"
    pts_table = POINTS.get(category, POINTS["250"])
    if bye_downgrade and not won_final:
        # WTA's bye rule (per Wikipedia's WTA rankings page) is broader than
        # ATP's and doesn't have ATP's "zero it out at 500/250-level" special
        # case: ANY category's bye-then-immediate-loss is simply scored as a
        # first-round loss, full stop — never zero. Unconfirmed against a
        # real WTA breakdown page; ATP's equivalent 500/250 rule (score 0)
        # does NOT apply here per the documented WTA rule text.
        return pts_table.get(true_first_round, 0)
    return pts_table.get(round_code, 0)


def load_season_rows():
    files = sorted(glob.glob("*-wta-season.csv"))
    if not files:
        raise SystemExit("No *-wta-season.csv files found in the repo root.")
    rows = []
    for path in files:
        print(f"  Reading {path}")
        with open(path, newline="", encoding="utf-8") as f:
            file_rows = list(csv.DictReader(f))
        print(f"    {len(file_rows)} rows")
        rows.extend(file_rows)
    return rows


def main():
    print("=== WTA Points Tracker — Data Build (tennisdata.app source) ===")

    print("\n[1] Loading season CSV(s)...")
    rows = load_season_rows()

    # 52-week rolling window, matching the WTA ranking points expiry period.
    cutoff_ts = int(datetime.combine(date.today() - timedelta(weeks=52), datetime.min.time()).timestamp())

    tour_rows = [
        r for r in rows
        if r.get("tour_type_human") == "WTA Tour" and int(r.get("date_timestamp", "0")) >= cutoff_ts
    ]
    print(f"  {len(tour_rows)} WTA Tour rows in the 52-week window (of {len(rows)} total)")

    # All WTA Tour rows regardless of date — used ONLY for tournament grouping
    # (section 2). A tournament must be judged in-or-out of the window as one
    # whole unit, not row by row — otherwise a player eliminated a few days
    # before the cutoff falls out of it while the eventual finalists (playing
    # later in the same event) stay in, splitting one tournament's players
    # inconsistently. See process_data.py for the full rationale.
    all_wta_rows = [r for r in rows if r.get("tour_type_human") == "WTA Tour"]

    latest_seen = {}

    def consider(pid, name, ts, rank, points):
        if not pid or not rank:
            return
        try:
            rank_i, ts_i = int(rank), int(ts)
            points_i = int(points) if points not in (None, "") else 0
        except ValueError:
            return
        prev = latest_seen.get(pid)
        if prev is None or ts_i > prev[0]:
            latest_seen[pid] = (ts_i, name, rank_i, points_i)

    for r in tour_rows:
        ts = r.get("date_timestamp", "0")
        consider(r.get("home_id", ""), r.get("home_name", ""), ts, r.get("home_rank"), r.get("home_points"))
        consider(r.get("away_id", ""), r.get("away_name", ""), ts, r.get("away_rank"), r.get("away_points"))

    ranked_players = sorted(
        ((pid, *info) for pid, info in latest_seen.items()),
        key=lambda x: x[3],
    )
    top500 = ranked_players[:500]
    top500_ids = {pid for pid, _, _, _, _ in top500}
    latest_ts = max((int(r.get("date_timestamp", "0")) for r in tour_rows if r.get("status") == "FINISHED"), default=0)
    rankings_date = date.fromtimestamp(latest_ts).strftime("%Y%m%d") if latest_ts else date.today().strftime("%Y%m%d")

    print(f"  Rankings as-of latest finished match: {rankings_date}. Top 500 loaded.")

    weekly_list = build_weekly_rankings(all_wta_rows, cutoff_ts, latest_ts)
    sizes = [len(w["rankings"]) for w in weekly_list]
    if sizes:
        print(
            f"  {len(weekly_list)} weekly snapshots for bump chart "
            f"(size min/med/max={min(sizes)}/{sorted(sizes)[len(sizes)//2]}/{max(sizes)})."
        )
    else:
        print("  Warning: no weekly snapshots built.")

    print("\n[2] Building tournament results...")
    tourneys = {}
    for r in all_wta_rows:
        if r.get("status") != "FINISHED":
            continue
        raw_name = r.get("tournament", "")
        if "Qualification" in raw_name:
            continue
        cat = tourney_category(raw_name)
        if cat is None:
            continue
        rnd_raw = r.get("round", "")
        if rnd_raw in ("Qualifier", "3rd place"):
            continue
        if rnd_raw not in ROUND_MAP:
            continue
        rnd = ROUND_MAP[rnd_raw]

        if is_tour_finals(raw_name):
            group_name, display_name = "WTA Finals", "WTA Finals"
        elif is_united_cup(raw_name):
            group_name, display_name = "United Cup", "United Cup"
        else:
            group_name, display_name = raw_name, clean_tournament_name(raw_name)
        key = (group_name, r.get("season_year", ""))
        ts = int(r.get("date_timestamp", "0"))
        if key not in tourneys:
            tourneys[key] = {
                "name": display_name,
                "date_ts": ts,
                "end_ts": ts,
                "category": cat,
                "surface": title_surface(r.get("surface", "")),
                "winner": "",
                "matches": [],
            }
        else:
            tourneys[key]["date_ts"] = min(tourneys[key]["date_ts"], ts)
            tourneys[key]["end_ts"] = max(tourneys[key]["end_ts"], ts)

        winner_code = r.get("winner_code", "")
        home_id, away_id = r.get("home_id", ""), r.get("away_id", "")
        home_name, away_name = r.get("home_name", ""), r.get("away_name", "")

        def parse_rank(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        home_rank, away_rank = parse_rank(r.get("home_rank")), parse_rank(r.get("away_rank"))
        if winner_code == "1":
            winner_id, winner_name, loser_id, loser_rank = home_id, home_name, away_id, away_rank
        elif winner_code == "2":
            winner_id, winner_name, loser_id, loser_rank = away_id, away_name, home_id, home_rank
        else:
            continue

        if rnd == "F":
            tourneys[key]["winner"] = format_player_name(winner_name)
        is_playoff = is_united_cup_playoffs(raw_name)
        tourneys[key]["matches"].append((rnd, winner_id, loser_id, loser_rank, ts, is_playoff))

    # Apply the 52-week cutoff once per whole tournament (its latest match
    # date), not per row — see all_wta_rows note above.
    sorted_tourneys = sorted(
        ((k, t) for k, t in tourneys.items() if t["end_ts"] >= cutoff_ts),
        key=lambda x: (x[1]["date_ts"], x[1]["name"]),
    )

    tourney_list = []
    player_results = defaultdict(dict)

    for t_idx, (key, t_info) in enumerate(sorted_tourneys):
        tourney_list.append({
            "id": f"{key[1]}-{key[0]}",
            "name": t_info["name"],
            "date": date.fromtimestamp(t_info["date_ts"]).strftime("%Y%m%d"),
            "category": t_info["category"],
            "surface": t_info["surface"],
            "winner": t_info["winner"],
        })

        if t_info["category"] == "F":
            # WTA Finals scoring is additive PER MATCH WON (200/round-robin
            # win, 400 for an SF win, 500 for an F win — see the POINTS
            # comment above), not a flat value for "furthest round reached".
            win_value = {"RR": 200, "SF": 400, "F": 500}
            finals_pts = defaultdict(int)
            for rnd, winner_id, loser_id, _, _, _ in t_info["matches"]:
                if winner_id in top500_ids and rnd in win_value:
                    finals_pts[winner_id] += win_value[rnd]
            for pid, pts in finals_pts.items():
                if pts > 0:
                    player_results[pid][t_idx] = pts
            continue

        if t_info["category"] == "UC":
            # United Cup scoring is additive per match won, worth a flat
            # amount depending on the opponent's ranking and the stage
            # (group vs QF/SF/F) — see united_cup_points/UNITED_CUP_TABLE.
            # Mirrors process_data.py's ATP implementation exactly.
            group_matches = [m for m in t_info["matches"] if not m[5]]
            playoff_matches = sorted((m for m in t_info["matches"] if m[5]), key=lambda m: m[4])
            uc_pts = defaultdict(int)
            for rnd, winner_id, loser_id, loser_rank, ts, is_playoff in group_matches:
                if winner_id in top500_ids and loser_rank is not None:
                    uc_pts[winner_id] += united_cup_points(loser_rank, "group")
            if len(playoff_matches) == 7:
                stages = ["QF"] * 4 + ["SF"] * 2 + ["F"] * 1
            else:
                stages = ["group"] * len(playoff_matches)
            for (rnd, winner_id, loser_id, loser_rank, ts, is_playoff), stage in zip(playoff_matches, stages):
                if winner_id in top500_ids and loser_rank is not None:
                    uc_pts[winner_id] += united_cup_points(loser_rank, stage)
            for pid, pts in uc_pts.items():
                if pts > 0:
                    player_results[pid][t_idx] = pts
            continue

        # The tournament's true "first round" — the shallowest round (earliest
        # in ROUND_ORDER) that appears among any recorded match, excluding
        # the round-robin sentinel. A player whose only recorded match in
        # this event is deeper than this can only have gotten there via a
        # bye — see points_for_result's bye_downgrade handling.
        non_rr_rounds = [rnd for rnd, _, _, _, _, _ in t_info["matches"] if rnd != "RR"]
        true_first_round = min(non_rr_rounds, key=lambda r: ROUND_RANK.get(r, 999)) if non_rr_rounds else None

        player_tourney = {}
        for rnd, winner_id, loser_id, _, _, _ in t_info["matches"]:
            for pid, is_winner in [(winner_id, True), (loser_id, False)]:
                if pid not in top500_ids:
                    continue
                if pid not in player_tourney:
                    player_tourney[pid] = {"best_rank": -1, "won_final": False, "last_round": "", "match_count": 0}
                entry = player_tourney[pid]
                entry["match_count"] += 1
                rrank = ROUND_RANK.get(rnd, -1)
                if rrank > entry["best_rank"]:
                    entry["best_rank"] = rrank
                    entry["last_round"] = rnd
                if is_winner and rnd == "F":
                    entry["won_final"] = True

        for pid, entry in player_tourney.items():
            last_round = entry["last_round"]
            bye_downgrade = (
                entry["match_count"] == 1
                and last_round != "RR"
                and true_first_round is not None
                and ROUND_RANK.get(last_round, -1) > ROUND_RANK.get(true_first_round, -1)
            )
            pts = points_for_result(
                t_info["category"], last_round, entry["won_final"],
                bye_downgrade=bye_downgrade, true_first_round=true_first_round,
            )
            if pts > 0:
                player_results[pid][t_idx] = pts

    # Cap each player's countable results per the real WTA structure: all
    # Grand Slams + the WTA Finals always count, and — unlike ATP — EVERY
    # WTA 1000 result a player earns also always counts (no ATP-style
    # replacement mechanism). Only United Cup/500/250 compete for a separate,
    # smaller pool of BEST_OTHER_POOL_CAP (7) slots. See the BEST_OTHER_POOL_CAP
    # comment above for why this is structured differently from process_data.py.
    for pid, pts_by_idx in player_results.items():
        slam_idx, finals_idx, thousand_idx, pool_idx = [], [], [], []
        for idx in pts_by_idx:
            cat = tourney_list[idx]["category"]
            if cat == "G":
                slam_idx.append(idx)
            elif cat == "F":
                finals_idx.append(idx)
            elif cat == "1000":
                thousand_idx.append(idx)
            else:
                pool_idx.append(idx)
        if len(pool_idx) > BEST_OTHER_POOL_CAP:
            pool_idx.sort(key=lambda idx: -pts_by_idx[idx])
            keep = set(slam_idx) | set(finals_idx) | set(thousand_idx) | set(pool_idx[:BEST_OTHER_POOL_CAP])
            player_results[pid] = {idx: pts for idx, pts in pts_by_idx.items() if idx in keep}

    # Drop any tournament where nobody actually earned points (e.g. a Billie
    # Jean King Cup-adjacent event, or a United Cup edition with no eligible
    # top-500 opponents) — detected structurally rather than by name so it
    # also catches anything else like it.
    scored_indices = {idx for pts_by_idx in player_results.values() for idx in pts_by_idx}
    reindex = {}
    filtered_tourney_list = []
    for old_idx, t in enumerate(tourney_list):
        if old_idx not in scored_indices:
            continue
        reindex[old_idx] = len(filtered_tourney_list)
        filtered_tourney_list.append(t)
    dropped = len(tourney_list) - len(filtered_tourney_list)
    if dropped:
        print(f"  Dropped {dropped} tournament(s) with zero points awarded (e.g. team events).")
    tourney_list = filtered_tourney_list
    player_results = {
        pid: {reindex[idx]: pts for idx, pts in pts_by_idx.items()}
        for pid, pts_by_idx in player_results.items()
    }

    # Rank/sort by counted tourneyPoints sum (same rationale as process_data.py).
    print("\n[3] Assembling output...")
    scored = []
    for pid, _ts, name, _official_rank, _official_pts in top500:
        tourney_pts = player_results.get(pid, {})
        scored.append((pid, name, sum(tourney_pts.values()), tourney_pts))
    scored.sort(key=lambda x: (-x[2], x[1]))

    players_out = []
    for rank, (pid, name, points, tourney_pts) in enumerate(scored, start=1):
        players_out.append({
            "id": pid,
            "name": format_player_name(name),
            "rank": rank,
            "points": points,
            "tourneyPoints": {str(idx): pts for idx, pts in tourney_pts.items()},
        })

    output = {
        "generated": date.today().isoformat(),
        "rankingsDate": rankings_date,
        "tournaments": tourney_list,
        "players": players_out,
        "weeklyRankings": weekly_list,
    }

    out_path = "data/wta/rankings.json"
    with open(out_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    total_size = len(json.dumps(output))
    print(f"\n  Written to {out_path} ({total_size:,} bytes)")
    print(f"  {len(tourney_list)} tournaments, {len(players_out)} players")
    print("=== Done ===")


if __name__ == "__main__":
    main()
