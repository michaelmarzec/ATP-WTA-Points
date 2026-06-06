#!/usr/bin/env python3
"""
Fetches Sackmann tennis_wta CSVs and outputs data/wta/rankings.json.
Covers a rolling ~14-month window of WTA tournaments.
"""

import csv
import json
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from io import StringIO

BASE_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master"

ROUND_ORDER = ["R128", "R64", "R32", "R16", "QF", "SF", "F", "W"]
ROUND_RANK = {r: i for i, r in enumerate(ROUND_ORDER)}

# WTA points schedule
# PM = WTA 1000, P = WTA 500, I = WTA 250, G = Grand Slam, F = WTA Finals
POINTS = {
    "G":    {"W": 2000, "F": 1300, "SF": 780, "QF": 430, "R16": 240, "R32": 130, "R64": 70, "R128": 10},
    "1000": {"W": 1000, "F": 650,  "SF": 390, "QF": 215, "R16": 120, "R32": 65,  "R64": 1},
    "500":  {"W": 470,  "F": 285,  "SF": 170, "QF": 95,  "R16": 57,  "R32": 28},
    "250":  {"W": 280,  "F": 180,  "SF": 110, "QF": 60,  "R16": 35,  "R32": 6},
    "F":    {"W": 1500, "F": 1000, "SF": 500, "RR": 125},
}

EXCLUDED_LEVELS = {"D", "35+H", "50+H"}
EXCLUDED_NAMES  = {"United Cup", "Billie Jean King Cup"}

# Sackmann miscategorizes Strasbourg as "I" (250) but it is a WTA 500 event.
WTA_500_NAME_OVERRIDES = {"Strasbourg"}


def fetch_csv(url):
    print(f"  Fetching {url}")
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def tourney_category(tourney_level, tourney_name=""):
    if tourney_level in EXCLUDED_LEVELS:
        return None
    if tourney_level == "G":
        return "G"
    if tourney_level == "PM":
        return "1000"
    if tourney_level == "P":
        return "500"
    if tourney_level == "I":
        return "500" if tourney_name in WTA_500_NAME_OVERRIDES else "250"
    if tourney_level == "F":
        return "F"
    return None


def points_for_result(category, round_str, won_final):
    if won_final:
        round_str = "W"
    pts_table = POINTS.get(category, POINTS["250"])
    return pts_table.get(round_str, 0)


def cutoff_date():
    today = date.today()
    cutoff = today - timedelta(days=14 * 30)
    return int(cutoff.strftime("%Y%m%d"))


def main():
    print("=== WTA Points Tracker — Data Build ===")

    # 1. Fetch current rankings (top 500)
    print("\n[1] Fetching current rankings...")
    rankings_csv = fetch_csv(f"{BASE_URL}/wta_rankings_current.csv")
    reader = csv.DictReader(StringIO(rankings_csv))
    rows = list(reader)

    latest_date = max(r["ranking_date"] for r in rows)
    current_rankings = [r for r in rows if r["ranking_date"] == latest_date]
    current_rankings.sort(key=lambda r: int(r["rank"]))
    top500 = current_rankings[:500]
    top500_ids = {r["player"] for r in top500}
    player_rank_points = {r["player"]: (int(r["rank"]), int(r["points"])) for r in top500}

    print(f"  Rankings date: {latest_date}, top 500 loaded.")

    # 1b. Weekly ranking snapshots for bump chart (top 50)
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

    try:
        hist_csv = fetch_csv(f"{BASE_URL}/wta_rankings_20s.csv")
        ingest_ranking_rows(csv.DictReader(StringIO(hist_csv)))
    except Exception as e:
        print(f"  Warning: could not fetch decade rankings: {e}")

    ingest_ranking_rows(rows)

    weekly_list = [{"date": d, "rankings": weekly[d]} for d in sorted(weekly)]
    print(f"  {len(weekly_list)} weekly snapshots for bump chart.")

    # 2. Fetch player names
    print("\n[2] Fetching player names...")
    players_csv = fetch_csv(f"{BASE_URL}/wta_players.csv")
    reader = csv.DictReader(StringIO(players_csv))
    player_names = {}
    for row in reader:
        pid = row.get("player_id", "")
        if pid:
            first = row.get("name_first", "")
            last = row.get("name_last", "")
            player_names[pid] = f"{first} {last}".strip()

    # 3. Fetch match files
    cutoff = cutoff_date()
    today_year = date.today().year
    years = [today_year - 1, today_year]

    print(f"\n[3] Fetching match data (cutoff {cutoff})...")
    all_matches = []
    for yr in years:
        url = f"{BASE_URL}/wta_matches_{yr}.csv"
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
                level = row.get("tourney_level", "")
                if level in EXCLUDED_LEVELS:
                    continue
                if row.get("tourney_name") in EXCLUDED_NAMES:
                    continue
                all_matches.append(row)
        except Exception as e:
            print(f"  Warning: could not fetch {yr}: {e}")

    print(f"  {len(all_matches)} matches in window.")

    # 4. Build per-tournament results
    tourneys = {}
    for m in all_matches:
        tid = m.get("tourney_id", "")
        tdate = m.get("tourney_date", "")
        tname = m.get("tourney_name", "")
        tlevel = m.get("tourney_level", "")
        cat = tourney_category(tlevel, tname)
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

    sorted_tourneys = sorted(tourneys.items(), key=lambda x: (x[1]["date"], x[1]["name"]))

    tourney_list = []
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
                    if rrank > entry["best_rank"]:
                        entry["best_rank"] = rrank
                        entry["last_round"] = rnd

        for pid, entry in player_tourney.items():
            pts = points_for_result(cat, entry["last_round"], entry["won_final"])
            if pts > 0:
                player_results[pid][t_idx] = pts

    # 5. Assemble output
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
            "tourneyPoints": tourney_pts,
        })

    output = {
        "generated": date.today().isoformat(),
        "rankingsDate": latest_date,
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
