#!/usr/bin/env bash
#
# refresh_data.sh — one-command data refresh for ATP-WTA-Points.
#
# What it does:
#   1. Looks in ~/Downloads for freshly-downloaded tennisdata.app CSVs
#      (*-atp-season.csv / *-wta-season.csv).
#   2. Archives the CSVs currently in the repo, then moves the new ones
#      in from Downloads.
#   3. Regenerates data/atp/rankings.json and data/wta/rankings.json.
#   4. Commits and pushes, if anything actually changed.
#
# Usage: run this after downloading fresh CSV(s) from tennisdata.app.
#   ./scripts/refresh_data.sh
#
# This does NOT fetch anything from tennisdata.app itself — that site is
# behind Cloudflare bot protection, so the CSV download has to stay a
# manual step in your browser. This script automates everything after
# that.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOWNLOADS="$HOME/Downloads"
ARCHIVE_DIR="$REPO_ROOT/scripts/csv_archive/$(date +%Y%m%d_%H%M%S)"

cd "$REPO_ROOT"

echo "== ATP-WTA-Points data refresh =="
echo "Repo: $REPO_ROOT"

# --- 0. Sanity: make sure we're not about to fight a dirty/behind branch ---
if [ -n "$(git status --porcelain)" ]; then
  echo
  echo "Your working tree already has uncommitted changes:"
  git status --short
  echo
  read -r -p "Continue anyway? [y/N] " ans
  if [[ ! "$ans" =~ ^[Yy]$ ]]; then
    echo "Aborted. Commit, stash, or discard those changes first."
    exit 1
  fi
fi

echo "Fetching latest from origin..."
git fetch origin

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if ! git merge-base --is-ancestor HEAD "origin/$CURRENT_BRANCH" 2>/dev/null; then
  if ! git merge-base --is-ancestor "origin/$CURRENT_BRANCH" HEAD 2>/dev/null; then
    echo
    echo "Local '$CURRENT_BRANCH' and 'origin/$CURRENT_BRANCH' have diverged."
    echo "Resolve that manually (pull/rebase/merge) before running this script."
    exit 1
  fi
fi
git pull --ff-only origin "$CURRENT_BRANCH"

# --- 1. Find new CSVs in Downloads ---
shopt -s nullglob
new_atp=("$DOWNLOADS"/*-atp-season.csv)
new_wta=("$DOWNLOADS"/*-wta-season.csv)
shopt -u nullglob

if [ ${#new_atp[@]} -eq 0 ] && [ ${#new_wta[@]} -eq 0 ]; then
  echo
  echo "No *-atp-season.csv or *-wta-season.csv files found in $DOWNLOADS."
  echo "Download the latest CSV(s) from tennisdata.app first, then re-run this script."
  exit 1
fi

echo
echo "Found in Downloads:"
for f in "${new_atp[@]}" "${new_wta[@]}"; do
  echo "  - $(basename "$f")"
done

# --- 2. Archive the CSVs currently in the repo (so nothing is ever lost) ---
mkdir -p "$ARCHIVE_DIR"
shopt -s nullglob
existing_csvs=("$REPO_ROOT"/*-atp-season.csv "$REPO_ROOT"/*-wta-season.csv)
shopt -u nullglob
if [ ${#existing_csvs[@]} -gt 0 ]; then
  echo
  echo "Archiving current repo CSVs to scripts/csv_archive/$(basename "$ARCHIVE_DIR")/"
  cp "${existing_csvs[@]}" "$ARCHIVE_DIR"/
fi

# --- 3. Move the new CSVs into place ---
echo
echo "Moving new CSV(s) into the repo..."
for f in "${new_atp[@]}" "${new_wta[@]}"; do
  mv "$f" "$REPO_ROOT/$(basename "$f")"
  echo "  -> $(basename "$f")"
done

# --- 4. Regenerate rankings.json for both tours ---
echo
echo "Running ATP pipeline..."
python3 scripts/process_data.py

echo
echo "Running WTA pipeline..."
python3 scripts/process_wta_data.py

# --- 5. Commit + push, only if something actually changed ---
echo
git add \
  "$REPO_ROOT"/*-atp-season.csv \
  "$REPO_ROOT"/*-wta-season.csv \
  data/atp/rankings.json \
  data/wta/rankings.json

if git diff --staged --quiet; then
  echo "No changes detected after regenerating — nothing to commit."
  exit 0
fi

echo "Changes to commit:"
git diff --staged --stat

commit_msg="chore: refresh rankings data $(date -u +%Y-%m-%d)"
git commit -m "$commit_msg"
git push origin "$CURRENT_BRANCH"

echo
echo "Done. Pushed: $commit_msg"
echo "The live site should pick this up within a minute or two."
