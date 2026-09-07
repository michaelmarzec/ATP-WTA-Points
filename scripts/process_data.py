#!/usr/bin/env python3
"""
Transforms tennisdata.app ATP season CSV(s) → data/atp/rankings.json.

Reads local "<year>-atp-season.csv" files in the repo root (CSVs are never
modified). Output matches the contract the table/viz frontend already expects:
full-ish display names, Title-Case surfaces, dense weekly snapshots for bump.
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

# ---------- ATP points schedule (unchanged from the Sackmann-based script) ----------
# "RR" (round-robin, used only by the season-ending Finals) sits at the bottom:
# it must outrank the "-1" not-yet-reached sentinel so an RR-only result (a
# player eliminated in the group stage, never reaching the SF/F knockout
# bracket) actually registers instead of silently scoring 0.
ROUND_ORDER = ["RR", "R128", "R64", "R32", "R16", "QF", "SF", "F", "W"]
ROUND_RANK = {r: i for i, r in enumerate(ROUND_ORDER)}

POINTS = {
    "G":       {"W": 2000, "F": 1300, "SF": 800, "QF": 400, "R16": 200, "R32": 100, "R64": 50, "R128": 10},
    # R64 = 30 is the real base table value (confirmed via Wikipedia's official
    # ATP points-distribution table). The two players whose real breakdown
    # pages showed an R64 loss worth only 10 both turned out to have received
    # a first-round bye and lost their very next match — a documented ATP rule
    # downgrades that specific case to the first-round-loss value (see the
    # bye-downgrade handling in points_for_result/the tournament-scoring loop
    # below), rather than R64 itself being worth 10. Keeping R64 at its real
    # value of 30 and handling the bye case explicitly is correct for players
    # who actually win their first-round match before losing in R64.
    "M":       {"W": 1000, "F": 650,  "SF": 400, "QF": 200, "R16": 100, "R32": 50,  "R64": 30, "R128": 10},
    # Monte Carlo and Paris (Bercy) use a real 56-draw (confirmed via the raw
    # data: these two events never have an "1/64-finals" round at all, only
    # 24 "1/32-finals" matches as the true first round). From the round of 32
    # onward the bracket is the same size as a standard 96-draw Masters, so
    # those rounds share the same point values — only the actual first round
    # (R64 here, vs. R128 for a 96-draw) is worth less. R32=10 was an earlier
    # unverified guess (copied from the same bye-downgrade coincidence that
    # produced the old flat "M" R64=10); a real player's ATP.com breakdown
    # page showing a Monte Carlo/Paris R32 loss worth the full 50 (not 10)
    # confirmed R32 should match the standard table.
    "M_small": {"W": 1000, "F": 650,  "SF": 400, "QF": 200, "R16": 100, "R32": 50,  "R64": 10},
    "500":     {"W": 500,  "F": 330,  "SF": 200, "QF": 100, "R16": 50,  "R32": 25},
    "250":     {"W": 250,  "F": 165,  "SF": 100, "QF": 50,  "R16": 25,  "R32": 13},
    # ATP Finals uses round-robin — approximate with SF/F structure.
    # SF confirmed against a real player's ATP.com rankings-breakdown page (400,
    # not 500 — the previous value was an unverified guess).
    "F":       {"W": 1500, "F": 1000, "SF": 400, "RR": 200},
}

# tennisdata.app round labels -> our internal round codes.
# ("1/Nth-finals" is standard European fraction-of-final naming: 1/64 = round of 128, etc.)
ROUND_MAP = {
    "1/64-finals": "R128",
    "1/32-finals": "R64",
    "1/16-finals": "R32",
    "1/8-finals": "R16",
    "Quarter-finals": "QF",
    "Semi-finals": "SF",
    "Final": "F",
    "": "RR",  # round-robin group stage (Finals event, United Cup, etc.)
}
# Rounds we deliberately do not score: "Qualifier" (not main draw), "3rd place" (playoff, no ATP points)

GRAND_SLAMS = {"Australian Open", "French Open", "Roland Garros", "Wimbledon", "US Open"}

MASTERS_1000 = {
    "Indian Wells", "Miami", "Monte Carlo", "Madrid", "Rome", "Montreal",
    "Toronto", "Cincinnati", "Shanghai", "Paris",
}

# True 56-draw Masters: confirmed by the actual round labels present in the
# CSV data — these two never have an "1/64-finals" (R128) round at all, only
# 24 "1/32-finals" (R64) matches as the real first round. Montreal/Toronto
# were wrongly added here earlier: they DO have a full R128 round (32
# matches) like a standard 96-draw, and the R64=10 result that looked like
# small-draw scoring on a real player's breakdown was actually the
# first-round-bye downgrade rule (see the bye_downgrade handling below),
# not a different draw-size points table.
MASTERS_SMALL_DRAW = {"Monte Carlo", "Paris"}  # 56-draw Masters

TOUR_FINALS_NAMES = {"Nitto ATP Finals", "ATP Finals", "Tour Finals"}

# United Cup is a mixed-team event, but its singles rubbers DO carry real ATP
# ranking points (confirmed via two real players' ATP.com breakdown pages,
# both listed under "Best of Other Countable Tournaments" — not a Slam/Finals
# bonus, just pooled with Masters/500/250 for the top-14 cap). Its scoring
# isn't round-based at all: each win is worth a flat amount that depends only
# on the ranking of the opponent beaten (see UNITED_CUP_TABLE below), per the
# official points-distribution table, and the table itself varies by stage
# (group / QF / SF / F). tennisdata.app splits this into two differently-named
# "tournaments" (like the season-ending Finals): "United Cup ATP" (the
# round-robin group stage) and "United Cup ATP - Play Offs" (the 8-team
# single-elimination knockout bracket) — both need to merge into one event.
def is_united_cup(raw_name):
    return "United Cup" in raw_name


def is_united_cup_playoffs(raw_name):
    return is_united_cup(raw_name) and "Play Offs" in raw_name


# (max_opponent_rank, points_per_win) per stage, most specific tier first.
# Sourced from Wikipedia's ATP rankings points-distribution table
# (2024-present) for the United Cup.
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

# tennisdata.app splits the season-ending Finals into two separate "tournament"
# entries: "Finals - <city> ATP (indoor)" (round-robin group stage) and
# "Finals - <city> ATP - Play Offs (indoor)" (the SF/F knockout stage). Both need
# to be recognized as category "F" AND merged into one tourney key so a player's
# best round spans both stages.
def is_tour_finals(raw_name):
    return raw_name.startswith("Finals - ") and " ATP" in raw_name

# Official ATP 500-level tournaments identified by name (stable across years,
# unlike per-year tournament IDs).
ATP_500_NAMES = {
    "Dallas", "Rotterdam", "Doha", "Rio de Janeiro", "Dubai", "Acapulco",
    "Barcelona", "Munich", "Hamburg", "Queen's Club", "London", "Halle", "Washington",
    "Tokyo", "Beijing", "Vienna", "Basel",
}
# tennisdata.app records Queen's Club under the plain city name "London",
# not "Queen's Club" — confirmed via a real player's ATP.com breakdown page
# showing a 100-point QF result there that our pipeline was scoring as a
# 250-level QF (50) instead, because "London" fell through to the default
# 250 category.

# Real ATP rankings don't sum every tournament played — they cap the total at
# 18 counted results (4 mandatory Grand Slams + 14 "best of the rest" across
# Masters/500/250) plus the ATP Finals counted separately as a bonus 19th.
# Confirmed against a real player's ATP.com rankings-breakdown page: our full
# sum overcounted by exactly the value of their two weakest non-Slam results
# once cut to this cap. (Which Masters are formally "mandatory" doesn't matter
# for the total — a mandatory event a player skips just contributes 0 either
# way, and empirically even a Masters result gets dropped if it's genuinely
# among the player's weakest, so a flat "best 14 of everything non-Slam"
# pool reproduces the real total without hand-coding a mandatory-event list.)
BEST_OTHER_CAP = 14

EXCLUDED_NAME_SUBSTR = ("Davis Cup", "Laver Cup", "Next Gen Finals", "WIC")
# "WIC" = wildcard playoff (e.g. into the US Open) — not a ranking-points event.


def clean_tournament_name(raw):
    """Strip any trailing parenthetical (e.g. '(indoor)') and the ' ATP' tour suffix."""
    name = raw
    if "(" in name:
        name = name.split("(")[0].strip()
    if name.endswith(" ATP"):
        name = name[: -len(" ATP")]
    return name.strip()


def tourney_category(raw_name):
    """Returns (category, is_small_draw) or (None, False) if this tournament shouldn't be scored."""
    if any(s in raw_name for s in EXCLUDED_NAME_SUBSTR):
        return None, False
    if is_tour_finals(raw_name):
        return "F", False
    name = clean_tournament_name(raw_name)
    if name in GRAND_SLAMS:
        return "G", False
    if name in TOUR_FINALS_NAMES:
        return "F", False
    if is_united_cup(raw_name):
        return "UC", False
    if name in MASTERS_1000:
        return "M", name in MASTERS_SMALL_DRAW
    if name in ATP_500_NAMES:
        return "500", False
    return "250", False


def points_for_result(category, round_code, won_final, small_draw=False, bye_downgrade=False, true_first_round=None):
    if won_final:
        round_code = "W"
    if category == "M" and small_draw:
        category = "M_small"
    pts_table = POINTS.get(category, POINTS["250"])
    if bye_downgrade and not won_final:
        # ATP rule: a player who draws a first-round bye and loses their very
        # next match is scored as if they'd lost in the first round instead
        # of whatever round that next match nominally was — for Masters that's
        # a real (nonzero) first-round value; for 500/250-level events the
        # rule specifically zeroes it out. Confirmed via Wikipedia's ATP
        # rankings points-distribution page (footnote on bye handling).
        if category in ("500", "250"):
            return 0
        return pts_table.get(true_first_round, 0)
    return pts_table.get(round_code, 0)


def load_season_rows():
    """Reads every '<year>-atp-season.csv' file present in the repo root."""
    files = sorted(glob.glob("*-atp-season.csv"))
    if not files:
        raise SystemExit("No *-atp-season.csv files found in the repo root.")
    rows = []
    for path in files:
        print(f"  Reading {path}")
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            file_rows = list(reader)
        print(f"    {len(file_rows)} rows")
        rows.extend(file_rows)
    return rows


def main():
    print("=== ATP Points Tracker — Data Build (tennisdata.app source) ===")

    print("\n[1] Loading season CSV(s)...")
    rows = load_season_rows()

    # 52-week rolling window, matching the ATP ranking points expiry period.
    # NOTE: this filters at the individual MATCH-row level (each row has its own
    # real match date from tennisdata.app), not a per-tournament start date — so
    # no extra buffer is needed for multi-week events like Grand Slams: a
    # player's final/deepest-round match always carries its own later date and
    # survives the cutoff on its own merits. (An earlier version used a 54-week
    # window as a buffer, ported over from the old Sackmann-based script that
    # only had a single tourney START date to filter on — that buffer is wrong
    # here and was over-crediting players with points from tournaments that had
    # already expired, e.g. keeping a Masters 1000 runner-up finish alive for an
    # extra ~1-2 weeks past its true 52-week expiry.)
    cutoff_ts = int(datetime.combine(date.today() - timedelta(weeks=52), datetime.min.time()).timestamp())

    # Only main ATP Tour level (excludes "ATP Chall" and anything else), within the window
    tour_rows = [
        r for r in rows
        if r.get("tour_type_human") == "ATP Tour" and int(r.get("date_timestamp", "0")) >= cutoff_ts
    ]
    print(f"  {len(tour_rows)} ATP Tour rows in the 52-week window (of {len(rows)} total)")

    # All ATP Tour rows regardless of date — used ONLY for tournament grouping
    # (section 2 below). Tournament scoring needs a whole event judged as one
    # unit: filtering individual match rows by date before grouping let some
    # players in a tournament fall inside the window while others (e.g.
    # someone eliminated a few days before the cutoff, while the eventual
    # finalists played on past it) fell outside it, even though it's the same
    # event — a real bug found when a player's early-round exit vanished from
    # their total while the rest of that tournament's results survived.
    all_atp_rows = [r for r in rows if r.get("tour_type_human") == "ATP Tour"]

    # ---------- Current rank/points per player: most recent row each player appears in ----------
    # home_rank/home_points (or away_ equivalent) reflect the official ranking as of that match,
    # so the latest row a player appears in gives their current rank + points directly.
    latest_seen = {}  # player_id -> (timestamp, name, rank, points)

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
        key=lambda x: x[3],  # rank
    )
    top500 = ranked_players[:500]
    top500_ids = {pid for pid, _, _, _, _ in top500}
    latest_ts = max((int(r.get("date_timestamp", "0")) for r in tour_rows if r.get("status") == "FINISHED"), default=0)
    rankings_date = date.fromtimestamp(latest_ts).strftime("%Y%m%d") if latest_ts else date.today().strftime("%Y%m%d")

    print(f"  Rankings as-of latest finished match: {rankings_date}. Top 500 loaded.")

    # ---------- Weekly ranking snapshots for bump chart ----------
    # Seed timelines from ALL ATP Tour rows so early weeks in the 52-week window
    # are already dense (match embeds only update when a player plays).
    weekly_list = build_weekly_rankings(all_atp_rows, cutoff_ts, latest_ts)
    sizes = [len(w["rankings"]) for w in weekly_list]
    if sizes:
        print(
            f"  {len(weekly_list)} weekly snapshots for bump chart "
            f"(size min/med/max={min(sizes)}/{sorted(sizes)[len(sizes)//2]}/{max(sizes)})."
        )
    else:
        print("  Warning: no weekly snapshots built.")

    # ---------- Tournament / points-by-tournament ----------
    print("\n[2] Building tournament results...")
    tourneys = {}
    for r in all_atp_rows:
        if r.get("status") != "FINISHED":
            continue
        raw_name = r.get("tournament", "")
        if "Qualification" in raw_name:
            continue  # separate qualifying-round "tournament" entries — not main draw
        cat, small_draw = tourney_category(raw_name)
        if cat is None:
            continue
        rnd_raw = r.get("round", "")
        if rnd_raw == "Qualifier" or rnd_raw == "3rd place":
            continue
        if rnd_raw not in ROUND_MAP:
            continue  # unrecognized round label — skip rather than guess
        rnd = ROUND_MAP[rnd_raw]

        # tennisdata.app doesn't give a stable per-tournament id; use (name, season) as the key
        # so all rounds of one tournament group together, keyed to its earliest match date.
        # The season-ending Finals is a special case: its round-robin stage and knockout
        # stage arrive as two differently-named "tournaments" that need to merge into one.
        if is_tour_finals(raw_name):
            group_name, display_name = "ATP Finals", "ATP Finals"
        elif is_united_cup(raw_name):
            group_name, display_name = "United Cup", "United Cup"
        else:
            group_name, display_name = raw_name, clean_tournament_name(raw_name)
        key = (group_name, r.get("season_year", ""))
        ts = int(r.get("date_timestamp", "0"))
        if key not in tourneys:
            tourneys[key] = {
                "name": display_name,
                "date_ts": ts,       # earliest match date — used for display/sort order
                "end_ts": ts,        # latest match date — used for the cutoff decision below
                "category": cat,
                "small_draw": small_draw,
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
            continue  # no clear result (e.g. retired before completion with no winner_code)

        if rnd == "F":
            tourneys[key]["winner"] = format_player_name(winner_name)
        is_playoff = is_united_cup_playoffs(raw_name)
        tourneys[key]["matches"].append((rnd, winner_id, loser_id, loser_rank, ts, is_playoff))

    # Apply the 52-week cutoff once per whole tournament (using its latest
    # match date, i.e. closest to the final) rather than per match row, so a
    # single event is either fully in-window or fully out for every player —
    # see the note above `all_atp_rows` for why that matters.
    sorted_tourneys = sorted(
        ((k, t) for k, t in tourneys.items() if t["end_ts"] >= cutoff_ts),
        key=lambda x: (x[1]["date_ts"], x[1]["name"]),
    )

    tourney_list = []
    tourney_end_ts = []  # parallel to tourney_list, not part of the JSON output — used for the Masters-replacement rule below
    player_results = defaultdict(dict)

    for t_idx, (key, t_info) in enumerate(sorted_tourneys):
        tourney_end_ts.append(t_info["end_ts"])
        tourney_list.append({
            "id": f"{key[1]}-{key[0]}",
            "name": t_info["name"],
            "date": date.fromtimestamp(t_info["date_ts"]).strftime("%Y%m%d"),
            "category": t_info["category"],
            "surface": t_info["surface"],
            "winner": t_info["winner"],
        })

        if t_info["category"] == "F":
            # ATP Finals scoring is additive PER MATCH WON, not a flat value
            # for "furthest round reached": each round-robin win is worth 200,
            # a semifinal win 400, a final win 500 (summing to the "W":1500
            # total for an undefeated champion). Confirmed by two real
            # players' ATP.com breakdown pages that both list a "SF" result
            # but with different point totals (400 vs 200) — the difference
            # was their round-robin win count, not the SF stage itself.
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
            # amount that depends on the opponent's ranking AND the stage
            # (group vs QF/SF/F) — see united_cup_points/UNITED_CUP_TABLE.
            # Group-stage matches are unambiguous (all from the "United Cup
            # ATP" event). Playoff matches ("United Cup ATP - Play Offs") are
            # a fixed 8-team single-elimination bracket (4 QF + 2 SF + 1 F =
            # 7 ties) with no round label of their own, so we infer the stage
            # from chronological order within just that bracket.
            group_matches = [m for m in t_info["matches"] if not m[5]]
            playoff_matches = sorted((m for m in t_info["matches"] if m[5]), key=lambda m: m[4])
            uc_pts = defaultdict(int)
            for rnd, winner_id, loser_id, loser_rank, ts, is_playoff in group_matches:
                if winner_id in top500_ids and loser_rank is not None:
                    uc_pts[winner_id] += united_cup_points(loser_rank, "group")
            if len(playoff_matches) == 7:
                stages = ["QF"] * 4 + ["SF"] * 2 + ["F"] * 1
            else:
                # Bracket doesn't match the expected 8-team shape (e.g. a
                # partial/short season) — fall back to the group-stage table
                # rather than guess at a QF/SF/F split we can't verify.
                stages = ["group"] * len(playoff_matches)
            for (rnd, winner_id, loser_id, loser_rank, ts, is_playoff), stage in zip(playoff_matches, stages):
                if winner_id in top500_ids and loser_rank is not None:
                    uc_pts[winner_id] += united_cup_points(loser_rank, stage)
            for pid, pts in uc_pts.items():
                if pts > 0:
                    player_results[pid][t_idx] = pts
            continue

        # The tournament's true "first round" — the shallowest round (earliest
        # in ROUND_ORDER) that appears among any recorded match, excluding the
        # round-robin sentinel. Used below to detect a first-round-bye player:
        # someone whose only recorded match in this event is deeper than this,
        # which real ATP draws can only produce via a bye (nobody plays a
        # round-of-64 match without either winning or losing a prior round).
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
            if t_info["category"] in ("500", "250"):
                # tennisdata.app appears to be missing the actual first-round
                # matches entirely for smaller (48-draw) 500/250 events — the
                # shallowest round it records for these is already round of
                # 32/16 for the WHOLE field, seeded byes and true first-round
                # winners alike, so the "true first round" comparison used
                # for Masters/Slams below can't distinguish a real first-round
                # loss from a first-round-bye-then-loss here. Empirically,
                # every 500/250 result confirmed against a real player's
                # ATP.com breakdown page as worth 0 was exactly the case of a
                # player with only ONE recorded match in that event (win or
                # loss) — i.e. no evidence of them ever winning an earlier
                # match — while every result with 2+ recorded matches (real
                # progression) scored normally. So for this category tier we
                # use match_count==1 alone as the bye signal.
                bye_downgrade = entry["match_count"] == 1 and last_round != "RR"
            else:
                # First-round-bye detection: exactly one recorded match, and
                # it's deeper than the tournament's true first round — the
                # only way that's possible is a bye straight into that round.
                bye_downgrade = (
                    entry["match_count"] == 1
                    and last_round != "RR"
                    and true_first_round is not None
                    and ROUND_RANK.get(last_round, -1) > ROUND_RANK.get(true_first_round, -1)
                )
            pts = points_for_result(
                t_info["category"], last_round, entry["won_final"], t_info["small_draw"],
                bye_downgrade=bye_downgrade, true_first_round=true_first_round,
            )
            if pts > 0:
                player_results[pid][t_idx] = pts

    # Cap each player's countable results, per the ATP's actual rulebook
    # (Section 9.03): all 4 Grand Slams + the ATP Finals always count.
    # Masters 1000 results normally ALL count too — but the rulebook lets a
    # player replace up to 3 "weak" Masters results (worth only the
    # rock-bottom value for that draw: a first-round loss, bye-affected or
    # not) with a LATER, better-scoring ATP 500 or 250 result, in which case
    # the weak Masters result is dropped entirely rather than occupying a
    # slot. Whatever Masters results remain (unreplaced) each occupy their
    # own guaranteed slot; United Cup + 500 + 250 then compete for whatever's
    # left of the BEST_OTHER_CAP total.
    #
    # This matters because United Cup can NEVER bump a Masters result out of
    # a player's total, no matter its point value — only a later 500/250
    # result can, via the replacement rule. Confirmed against three real
    # players' full breakdown pages: one whose 40-point United Cup result was
    # dropped in favor of a 10-point Masters result that had no later
    # 500/250 result to replace it (his very last result of the window), and
    # two others whose weak Masters results (also worth 10, but WITH later
    # 500/250 results available) were the ones dropped instead, letting
    # their United Cup and other results all count.
    MASTERS_REPLACEMENT_LIMIT = 3
    MASTERS_MIN_VALUE = 10  # the rock-bottom Masters points value (first-round loss, bye-downgraded or not)

    for pid, pts_by_idx in player_results.items():
        slam_idx, finals_idx, masters_idx, pool_idx = [], [], [], []
        for idx in pts_by_idx:
            cat = tourney_list[idx]["category"]
            if cat == "G":
                slam_idx.append(idx)
            elif cat == "F":
                finals_idx.append(idx)
            elif cat == "M":
                masters_idx.append(idx)
            else:
                pool_idx.append(idx)

        # Replacement rule: a weak (rock-bottom-value) Masters result can be
        # dropped entirely if a LATER ATP 500/250 result (for this player)
        # scored higher than it — up to 3 such swaps, oldest weak result
        # first (arbitrary among ties, since a swap's value doesn't affect
        # the total either way — it just frees the Masters slot).
        weak_masters = [idx for idx in masters_idx if pts_by_idx[idx] == MASTERS_MIN_VALUE]
        replaced = []
        for m_idx in sorted(weak_masters, key=lambda idx: tourney_end_ts[idx]):
            if len(replaced) >= MASTERS_REPLACEMENT_LIMIT:
                break
            has_later_better = any(
                tourney_list[p_idx]["category"] in ("500", "250")
                and tourney_end_ts[p_idx] > tourney_end_ts[m_idx]
                and pts_by_idx[p_idx] > MASTERS_MIN_VALUE
                for p_idx in pool_idx
            )
            if has_later_better:
                replaced.append(m_idx)
        if replaced:
            masters_idx = [idx for idx in masters_idx if idx not in replaced]
            for idx in replaced:
                del pts_by_idx[idx]

        other_cap = max(0, BEST_OTHER_CAP - len(masters_idx))
        if len(pool_idx) > other_cap:
            # Stable sort: ties keep the chronologically-earlier result. Which
            # exact tied tournament is kept doesn't change the total (tied
            # values are interchangeable), only which one shows in the table.
            pool_idx.sort(key=lambda idx: -pts_by_idx[idx])
            keep = set(slam_idx) | set(finals_idx) | set(masters_idx) | set(pool_idx[:other_cap])
            player_results[pid] = {idx: pts for idx, pts in pts_by_idx.items() if idx in keep}

    # Drop any tournament where nobody actually earned points (e.g. United Cup —
    # a team event that doesn't carry ATP ranking points despite looking like a
    # normal tournament in the feed). Detected structurally rather than by name,
    # so it also catches anything else like it we haven't thought of.
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

    # Prefer embedded official points when fresh; if last match is stale, the
    # embed no longer tracks ranking decay — fall back to tourneyPoints sum.
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

    out_path = "data/atp/rankings.json"
    with open(out_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    total_size = len(json.dumps(output))
    print(f"\n  Written to {out_path} ({total_size:,} bytes)")
    print(f"  {len(tourney_list)} tournaments, {len(players_out)} players")
    print("=== Done ===")


if __name__ == "__main__":
    main()
