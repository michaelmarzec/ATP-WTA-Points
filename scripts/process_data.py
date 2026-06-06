#!/usr/bin/env python3
"""
Fetches Sackmann tennis_atp CSVs and outputs data/rankings.json.
Covers a rolling ~14-month window of ATP tournaments.
"""

import csv
import json
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from io import StringIO

BASE_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"

# ---------- ATP points schedule ----------
# Points go to the player based on their furthest round result.
# Keys: (tourney_category, round_reached)
# round_reached for a loser = the round they lost in; for the winner = "W"

ROUND_ORDER = ["R128", "R64", "R32", "R16", "QF", "SF", "F", "W"]
ROUND_RANK = {r: i for i, r in enumerate(ROUND_ORDER)}

POINTS = {
    "G":   {"W": 2000, "F": 1300, "SF": 800, "QF": 400, "R16": 200, "R32": 100, "R64": 50, "R128": 10},
    "M":   {"W": 1000, "F": 650,  "SF": 400, "QF": 200, "R16": 100, "R32": 50,  "R64": 30, "R128": 10},
    "500": {"W": 500,  "F": 330,  "SF": 200, "QF": 100, "R16": 50,  "R32": 25},
    "250": {"W": 250,  "F": 165,  "SF": 100, "QF": 50,  "R16": 25,  "R32": 13},
    # ATP Finals uses round-robin — approximate with SF/F structure
    "F":   {"W": 1500, "F": 1000, "SF": 500, "RR": 200},
}

# Official ATP 500-level tournaments identified by name (stable across years,
# unlike Sackmann tourney IDs which can vary).
ATP_500_NAMES = {
    # Source: https://en.wikipedia.org/wiki/ATP_500_tournaments (16 events as of 2025)
    "Dallas",         # Dallas Open
    "Rotterdam",      # ABN AMRO Open
    "Doha",           # Qatar Open (Sackmann uses "Doha" as tourney_name)
    "Rio de Janeiro", # Rio Open
    "Dubai",          # Dubai Duty Free Tennis Championships
    "Acapulco",       # Abierto Mexicano Telcel (Mexican Open)
    "Barcelona",      # Barcelona Open Banc Sabadell
    "Munich",         # Bavarian International Tennis Championships
    "Hamburg",        # Hamburg Open
    "Queen's Club",   # Cinch Championships
    "Halle",          # Terra Wortmann Open
    "Washington",     # Citi Open (Washington Open)
    "Tokyo",          # Rakuten Japan Open (Japan Open)
    "Beijing",        # China Open
    "Vienna",         # Erste Bank Open (Vienna Open)
    "Basel",          # Swiss Indoors
}


def fetch_csv(url):
    print(f"  Fetching {url}")
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def tourney_category(tourney_level, tourney_id, tourney_name=""):
    if tourney_level == "D":
        return None  # Davis Cup — excluded
    if tourney_level == "G":
        return "G"
    if tourney_level == "M":
        return "M"
    if tourney_level == "F":
        return "F"
    if tourney_level == "A":
        return "500" if tourney_name in ATP_500_NAMES else "250"
    return "250"


def points_for_result(category, round_str, won_final):
    """Return ranking points for a player's result in a tournament."""
    if won_final:
        round_str = "W"
    pts_table = POINTS.get(category, POINTS["250"])
    return pts_table.get(round_str, 0)


def cutoff_date():
    today = date.today()
    # 52-week rolling window (matches ATP ranking period)
    cutoff = today - timedelta(weeks=52)
    return int(cutoff.strftime("%Y%m%d"))


def main():
    print("=== ATP Points Tracker — Data Build ===")

    # 1. Fetch current rankings to get top 300 player IDs
    print("\n[1] Fetching current rankings...")
    rankings_csv = fetch_csv(f"{BASE_URL}/atp_rankings_current.csv")
    reader = csv.DictReader(StringIO(rankings_csv))
    rows = list(reader)

    # File has: ranking_date, rank, player_id, points
    # Take the most recent date's top 300
    latest_date = max(r["ranking_date"] for r in rows)
    current_rankings = [r for r in rows if r["ranking_date"] == latest_date]
    current_rankings.sort(key=lambda r: int(r["rank"]))
    top500 = current_rankings[:500]
    top500_ids = {r["player"] for r in top500}
    player_rank_points = {r["player"]: (int(r["rank"]), int(r["points"])) for r in top500}

    print(f"  Rankings date: {latest_date}, top 500 loaded.")

    # 1b. Build weekly ranking snapshots for top 50 (for bump chart)
    # Pull from decade file for historical weeks + current file already loaded
    cutoff_wk = cutoff_date()
    weekly = {}

    def ingest_ranking_rows(ranking_rows):
        for r in ranking_rows:
            d = r["ranking_date"]
            if int(d) < cutoff_wk:
                continue
            try:
                rk = int(r["rank"])
            except ValueError:
                continue
            if rk > 50:
                continue
            if d not in weekly:
                weekly[d] = []
            weekly[d].append({"id": r["player"], "rank": rk, "points": int(r["points"])})

    # Historical (20s decade file covers through end of 2025)
    try:
        hist_csv = fetch_csv(f"{BASE_URL}/atp_rankings_20s.csv")
        ingest_ranking_rows(csv.DictReader(StringIO(hist_csv)))
    except Exception as e:
        print(f"  Warning: could not fetch decade rankings: {e}")

    # Current year (already fetched as `rows`)
    ingest_ranking_rows(rows)

    weekly_list = [{"date": d, "rankings": weekly[d]} for d in sorted(weekly)]
    print(f"  {len(weekly_list)} weekly snapshots for bump chart.")

    # 2. Fetch player names
    print("\n[2] Fetching player names...")
    players_csv = fetch_csv(f"{BASE_URL}/atp_players.csv")
    reader = csv.DictReader(StringIO(players_csv))
    # columns: player_id, name_first, name_last, hand, dob, ioc
    player_names = {}
    for row in reader:
        pid = row.get("player_id", "")
        if pid:
            first = row.get("name_first", "")
            last = row.get("name_last", "")
            player_names[pid] = f"{first} {last}".strip()

    # 3. Fetch match files for current and previous year
    cutoff = cutoff_date()
    today_year = date.today().year
    years = [today_year - 1, today_year]

    # Tournaments excluded from ATP ranking points
    # Davis Cup (level "D") and Laver Cup (level "A" but team event, no ATP points)
    EXCLUDED_LEVELS = {"D"}
    EXCLUDED_NAMES  = {"Laver Cup", "Next Gen Finals"}  # team events / non-ranking events

    print(f"\n[3] Fetching match data (cutoff {cutoff})...")
    all_matches = []
    for yr in years:
        url = f"{BASE_URL}/atp_matches_{yr}.csv"
        try:
            raw = fetch_csv(url)
            reader = csv.DictReader(StringIO(raw))
            for row in reader:
                try:
                    td = int(row.get("tourney_date", "0"))
                except ValueError:
                    continue
                if td < cutoff:
                    continue
                if row.get("tourney_level") in EXCLUDED_LEVELS:
                    continue
                if row.get("tourney_name") in EXCLUDED_NAMES:
                    continue
                all_matches.append(row)
        except Exception as e:
            print(f"  Warning: could not fetch {yr}: {e}")

    print(f"  {len(all_matches)} matches in window.")

    # 4. Build per-tournament results for each player
    # tourney key: (tourney_id, tourney_name, tourney_date, category)
    # player result: furthest round reached

    # Group matches by tournament
    tourneys = {}  # tourney_key → list of match rows
    for m in all_matches:
        tid = m.get("tourney_id", "")
        tdate = m.get("tourney_date", "")
        tname = m.get("tourney_name", "")
        tlevel = m.get("tourney_level", "A")
        cat = tourney_category(tlevel, tid, tname)
        if cat is None:
            continue
        key = (tid, tdate)
        if key not in tourneys:
            tourneys[key] = {
                "name": tname,
                "date": tdate,
                "level": tlevel,
                "category": cat,
                "surface": m.get("surface", ""),
                "winner": "",
                "matches": [],
            }
        if m.get("round") == "F":
            tourneys[key]["winner"] = m.get("winner_name", "")
        tourneys[key]["matches"].append(m)

    # Sort tournaments chronologically
    sorted_tourneys = sorted(tourneys.items(), key=lambda x: (x[1]["date"], x[1]["name"]))

    # For each tournament, compute each top-300 player's result
    # result = points earned
    tourney_list = []  # ordered list of {id, name, date, category}
    # player_results[player_id][tourney_index] = points
    player_results = defaultdict(dict)

    for t_idx, (key, t_info) in enumerate(sorted_tourneys):
        tourney_list.append({
            "id": key[0],
            "name": t_info["name"],
            "date": t_info["date"],
            "category": t_info["category"],
            "surface": t_info["surface"],
            "winner": t_info["winner"],
        })

        matches = t_info["matches"]
        cat = t_info["category"]

        # Find each player's best round
        # Track: player_id → {"best_round_rank": int, "won_final": bool, "last_round": str}
        player_tourney = {}

        for m in matches:
            rnd = m.get("round", "")
            winner_id = m.get("winner_id", "")
            loser_id = m.get("loser_id", "")

            for pid, is_winner in [(winner_id, True), (loser_id, False)]:
                if pid not in top500_ids:
                    continue
                if pid not in player_tourney:
                    player_tourney[pid] = {"best_rank": -1, "won_final": False, "last_round": ""}

                rrank = ROUND_RANK.get(rnd, -1)
                entry = player_tourney[pid]

                if is_winner:
                    if rrank > entry["best_rank"]:
                        entry["best_rank"] = rrank
                        entry["last_round"] = rnd
                    if rnd == "F":
                        entry["won_final"] = True
                else:
                    # Loser's round = the round they lost in
                    # Only update if this is a deeper loss (shouldn't happen, but guard)
                    if rrank > entry["best_rank"]:
                        entry["best_rank"] = rrank
                        entry["last_round"] = rnd

        for pid, entry in player_tourney.items():
            pts = points_for_result(cat, entry["last_round"], entry["won_final"])
            if pts > 0:
                player_results[pid][t_idx] = pts

    # 5. Assemble output JSON
    print("\n[4] Assembling output...")
    players_out = []
    for r in top500:
        pid = r["player"]
        rank, total_pts = player_rank_points[pid]
        name = player_names.get(pid, f"Player {pid}")
        tourney_pts = player_results.get(pid, {})
        players_out.append({
            "id": pid,
            "name": name,
            "rank": rank,
            "points": total_pts,
            "tourneyPoints": tourney_pts,  # {tourney_index: points}
        })

    output = {
        "generated": date.today().isoformat(),
        "rankingsDate": latest_date,
        "tournaments": tourney_list,
        "players": players_out,
        "weeklyRankings": weekly_list,
    }

    out_path = "data/atp/rankings.json"
    with open(out_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    total_size = len(json.dumps(output))
    print(f"\n  Written to {out_path} ({total_size:,} bytes)")
    print(f"  {len(tourney_list)} tournaments, {len(players_out)} players")
    print("=== Done ===")


if __name__ == "__main__":
    main()
