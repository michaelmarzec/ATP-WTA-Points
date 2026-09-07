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
from transform_utils import build_weekly_rankings, display_name as format_player_name, resolve_ranking_points, title_surface

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
    # 96-draw WTA 1000 (Indian Wells, Miami, Madrid, Rome, Canada, Cincinnati, Beijing, …)
    "1000": {"W": 1000, "F": 650,  "SF": 390,  "QF": 215, "R16": 120, "R32": 65,  "R64": 35, "R128": 10},
    # 56-draw WTA 1000 (Doha, Dubai, Wuhan, …) — first round is R64 worth 10, not 35
    "1000_small": {"W": 1000, "F": 650, "SF": 390, "QF": 215, "R16": 120, "R32": 65, "R64": 10},
    # 48-draw WTA 500
    "500":  {"W": 500,  "F": 325,  "SF": 195,  "QF": 108, "R16": 60,  "R32": 32, "R64": 1},
    # 28/30-draw WTA 500 (Queen's/London, Ningbo, Berlin, …) — first round R32 worth 1
    "500_small": {"W": 500, "F": 325, "SF": 195, "QF": 108, "R16": 60, "R32": 1},
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

# WTA 500-level tournaments identified by name. tennisdata.app doesn't flag
# tier, so this list is maintained by hand against the WTA calendar.
# Do NOT put WTA 250s here (Hong Kong, Osaka are 250; Seoul was 500 in 2025
# only and dropped back to 250 in 2026).
WTA_500_ALWAYS = {
    "Strasbourg", "Adelaide", "Abu Dhabi", "Berlin", "Charleston", "Stuttgart",
    "Eastbourne", "Washington", "Guadalajara", "Tokyo", "Ningbo", "Hamburg",
    "Brisbane", "Bad Homburg", "London",  # London = Queen's Club WTA 500
}
# Season-specific 500s (tier changes year to year).
WTA_500_BY_SEASON = {
    "2024": {"Seoul"},
    "2025": {"Seoul"},
}

# Combined/virtually-combined WTA 1000s (mandatory "best 6" pool) vs
# WTA-only 1000s (mandatory "best 1" pool). Extra 1000 results compete in
# the best-7 "other" pool — see WTA rankings rules.
WTA_1000_COMBINED = {
    "Indian Wells", "Miami", "Madrid", "Rome", "Montreal", "Toronto",
    "Cincinnati", "Beijing",
}
WTA_1000_NONCOMBINED = {"Doha", "Dubai", "Wuhan"}

WTA_1000 = WTA_1000_COMBINED | WTA_1000_NONCOMBINED

WTA_FINALS_NAMES = {"WTA Finals"}

# tennisdata.app splits the season-ending Finals into two separate "tournament"
# entries: "Finals - <city> WTA (indoor)" (round-robin stage) and
# "Finals - <city> WTA - Play Offs (indoor)" (SF/F knockout stage) — both need to
# be recognized as category "F" and merged into one tourney key.
def is_tour_finals(raw_name):
    return raw_name.startswith("Finals - ") and " WTA" in raw_name


# United Cup: ATP awards points by opponent-rank tier; WTA awards a flat total
# based on how many singles matches the player won (WTA.com / tennis365 2026).
# Do NOT reuse the ATP opponent-rank table here — that was over/under-counting
# WTA players by small United-Cup-shaped amounts (e.g. Mboko 20 vs real 32).
def is_united_cup(raw_name):
    return "United Cup" in raw_name


def is_united_cup_playoffs(raw_name):
    return is_united_cup(raw_name) and "Play Offs" in raw_name


def wta_united_cup_points(total_wins, knockout_wins):
    """WTA United Cup singles points from match-win count (max 500)."""
    if total_wins >= 5:
        return 500
    if total_wins == 4:
        return 325
    if total_wins == 3:
        return 150
    if total_wins == 2:
        return 108 if knockout_wins >= 1 else 90
    if total_wins == 1:
        return 60 if knockout_wins >= 1 else 32
    return 1  # played, zero wins

# Real WTA singles structure (WTA rankings rules): 4 Grand Slams + best 6
# combined WTA 1000s + best 1 non-combined WTA 1000 + best 7 other results
# (extra 1000s, 500, 250, 125, ITF, United Cup) + Finals as a bonus 19th.
BEST_OTHER_POOL_CAP = 7
BEST_COMBINED_1000 = 6
BEST_NONCOMBINED_1000 = 1

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


def is_wta_500(name, season_year=""):
    if name in WTA_500_ALWAYS:
        return True
    return name in WTA_500_BY_SEASON.get(str(season_year), set())


def tourney_category(raw_name, season_year=""):
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
    if is_wta_500(name, season_year):
        return "500"
    return "250"


def points_for_result(category, round_code, won_final, bye_downgrade=False, true_first_round=None, small_draw=False):
    if won_final:
        round_code = "W"
    if category == "1000" and small_draw:
        category = "1000_small"
    elif category == "500" and small_draw:
        category = "500_small"
    pts_table = POINTS.get(category, POINTS["250"])
    if bye_downgrade and not won_final:
        # WTA bye rule: bye-then-immediate-loss scores as a first-round loss.
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

    # 52-week rolling window. Add 7 days of slack so tournaments whose points
    # entered rankings on the Monday *after* they ended (WTA rule) are not
    # dropped a few days early — e.g. US Open 2025 (ended Sep 6, counted from
    # Mon Sep 8) must still count on Mon Sep 7 2026 for Mboko's 2531 total.
    cutoff_ts = int(datetime.combine(date.today() - timedelta(weeks=52, days=7), datetime.min.time()).timestamp())

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
        cat = tourney_category(raw_name, r.get("season_year", ""))
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
                "rounds_seen": set(),
            }
        else:
            tourneys[key]["date_ts"] = min(tourneys[key]["date_ts"], ts)
            tourneys[key]["end_ts"] = max(tourneys[key]["end_ts"], ts)
        tourneys[key]["rounds_seen"].add(rnd)

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
        # Detect draw-size variant from which round labels appear in the CSV.
        # 1000 without R128 → 56-draw table; 500 without R64 → 28/30-draw table.
        rounds_seen = t_info.get("rounds_seen") or {m[0] for m in t_info["matches"]}
        if t_info["category"] == "1000":
            small_draw = "R128" not in rounds_seen
        elif t_info["category"] == "500":
            small_draw = "R64" not in rounds_seen
        else:
            small_draw = False

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
            # WTA: flat total from number of singles wins (and whether any
            # were in the knockout rounds) — see wta_united_cup_points.
            wins = defaultdict(lambda: {"total": 0, "knockout": 0, "played": False})
            for rnd, winner_id, loser_id, _, _, is_playoff in t_info["matches"]:
                for pid in (winner_id, loser_id):
                    if pid in top500_ids:
                        wins[pid]["played"] = True
                if winner_id in top500_ids:
                    wins[winner_id]["total"] += 1
                    if is_playoff:
                        wins[winner_id]["knockout"] += 1
            for pid, w in wins.items():
                if not w["played"]:
                    continue
                pts = wta_united_cup_points(w["total"], w["knockout"])
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
                small_draw=small_draw,
            )
            if pts > 0:
                player_results[pid][t_idx] = pts

    # Cap per WTA rules: all Slams + Finals always count; best 6 combined
    # WTA 1000s + best 1 non-combined WTA 1000; everything else (including
    # surplus 1000s) competes for BEST_OTHER_POOL_CAP slots.
    for pid, pts_by_idx in player_results.items():
        slam_idx, finals_idx, combined_idx, noncombined_idx, pool_idx = [], [], [], [], []
        for idx in pts_by_idx:
            cat = tourney_list[idx]["category"]
            name = tourney_list[idx]["name"]
            if cat == "G":
                slam_idx.append(idx)
            elif cat == "F":
                finals_idx.append(idx)
            elif cat == "1000" and name in WTA_1000_COMBINED:
                combined_idx.append(idx)
            elif cat == "1000" and name in WTA_1000_NONCOMBINED:
                noncombined_idx.append(idx)
            else:
                pool_idx.append(idx)

        combined_idx.sort(key=lambda i: -pts_by_idx[i])
        noncombined_idx.sort(key=lambda i: -pts_by_idx[i])
        kept_combined = combined_idx[:BEST_COMBINED_1000]
        kept_noncombined = noncombined_idx[:BEST_NONCOMBINED_1000]
        # Surplus 1000 results fall into the best-of-rest pool.
        pool_idx.extend(combined_idx[BEST_COMBINED_1000:])
        pool_idx.extend(noncombined_idx[BEST_NONCOMBINED_1000:])
        pool_idx.sort(key=lambda i: -pts_by_idx[i])
        kept_pool = pool_idx[:BEST_OTHER_POOL_CAP]

        keep = set(slam_idx) | set(finals_idx) | set(kept_combined) | set(kept_noncombined) | set(kept_pool)
        if len(keep) < len(pts_by_idx):
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

    # Prefer tennisdata.app's embedded official points when the player has
    # played recently (matches WTA.com for active players). If their last match
    # is older than EMBED_STALE_DAYS, the embed is frozen while real rankings
    # decay — fall back to our in-window tourneyPoints sum (Mboko case).
    print("\n[3] Assembling output...")
    scored = []
    stale_n = 0
    for pid, last_ts, name, _official_rank, official_pts in top500:
        tourney_pts = player_results.get(pid, {})
        computed = sum(tourney_pts.values())
        points = resolve_ranking_points(last_ts, latest_ts, official_pts, computed)
        if points != official_pts:
            stale_n += 1
        scored.append((pid, name, points, tourney_pts))
    scored.sort(key=lambda x: (-x[2], x[1]))
    if stale_n:
        print(f"  Used replay totals for {stale_n} player(s) with stale ranking embeds.")

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
