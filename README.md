# ATP-WTA-Points

Live ATP rankings points tracker — see where every player's points come from, tournament by tournament.

## What it does

- **Table view** (`index.html`) — filterable, sortable grid of the top 500 ATP players × all tournaments in the trailing ~14-month window. Filter by surface, level (Grand Slam / Masters / 500 / 250 / Finals), and date range. Search and pin players, hide empty columns, mark favorites.
- **Visualize view** (`viz.html`) — four chart tabs:
  - **Stacked Bar** — top N players' points broken down by level or surface
  - **Sankey** — flow diagram showing how a single player's points split across surfaces → tournaments → levels
  - **Bump Chart** — weekly ranking trajectory for selected players
  - **Heatmap** — top N players × tournaments intensity grid

## Data pipeline

`scripts/process_data.py` pulls from [Jeff Sackmann's tennis_atp](https://github.com/JeffSackmann/tennis_atp) CSV repo and writes `data/rankings.json`. It:

1. Fetches current ATP rankings (top 500) and weekly snapshots for the bump chart
2. Fetches match results for the current and previous year
3. Derives each player's result per tournament from match outcomes
4. Applies the official ATP points schedule to compute points earned

A GitHub Actions workflow (`.github/workflows/update_data.yml`) runs the pipeline daily at 06:00 UTC and auto-commits any updated `data/rankings.json`.

## Running locally

```bash
python3 -m http.server 3400
# open http://localhost:3400
```

To rebuild the data manually:

```bash
python3 scripts/process_data.py
```

## Structure

```
index.html          Table view
app.js              Table view logic
style.css           Shared styles
viz.html            Visualize view
viz.js              Chart logic (Chart.js + D3 + d3-sankey)
viz.css             Visualize styles
data/
  rankings.json     Generated data (committed by CI)
scripts/
  process_data.py   Data pipeline
.github/workflows/
  update_data.yml   Daily CI job
```
