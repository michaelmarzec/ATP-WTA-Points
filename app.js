(async () => {
  const $ = id => document.getElementById(id);

  // ── Load data ──────────────────────────────────────────────────────────────
  let data;
  try {
    const resp = await fetch("data/rankings.json");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (e) {
    $("loading").hidden = true;
    $("error").hidden = false;
    $("error").textContent = `Failed to load data: ${e.message}`;
    return;
  }

  $("loading").hidden = true;
  $("table-wrap").hidden = false;

  $("meta").textContent =
    `Rankings as of ${fmtDate(data.rankingsDate)} · Built ${data.generated} · ${data.players.length} players · ${data.tournaments.length} tournaments`;

  // ── State ──────────────────────────────────────────────────────────────────
  const ALL_SURFACES = ["Hard", "Clay", "Grass"];
  const ALL_LEVELS   = ["G", "M", "500", "250", "F"];

  const state = {
    selectedPlayers: new Set(),   // player ids
    favoritePlayers: loadFavs(),  // persisted
    surfaces: new Set(ALL_SURFACES),
    levels:   new Set(ALL_LEVELS),
    dateFrom: null,   // YYYYMMDD or null
    dateTo:   null,
    hideEmpty: false,
    favsOnly:  false,
    sortCol: "rank",
    sortAsc: true,
  };

  // ── Shared DOM refs (must be declared before render()) ────────────────────
  const headerRow = $("header-row");
  const tbody     = $("table-body");
  const rowCount  = $("row-count");
  let sortThMap   = {};

  // ── Build filter UI ────────────────────────────────────────────────────────
  buildDateSelects();
  bindSurfaceChips();
  bindLevelChips();
  bindPlayerSearch();
  bindToggles();

  // ══════════════════════════════════════════════════════════════════════════
  // RENDER
  // ══════════════════════════════════════════════════════════════════════════
  function render() {
    const { visibleTourneys, visiblePlayers } = computeFiltered();
    buildHeader(visibleTourneys);
    buildRows(visiblePlayers, visibleTourneys);
    updateResetBtn();
  }

  function computeFiltered() {
    // 1. Filter tournaments
    let tourneys = data.tournaments.map((t, i) => ({ ...t, idx: i }));

    tourneys = tourneys.filter(t => {
      if (!state.surfaces.has(t.surface)) return false;
      if (!state.levels.has(t.category)) return false;
      if (state.dateFrom && t.date < state.dateFrom) return false;
      if (state.dateTo   && t.date > state.dateTo)   return false;
      return true;
    });

    // 2. Filter players
    let players = data.players;

    if (state.favsOnly) {
      players = players.filter(p => state.favoritePlayers.has(p.id));
    }
    if (state.selectedPlayers.size > 0) {
      players = players.filter(p => state.selectedPlayers.has(p.id));
    }

    // 3. Recalculate filtered points for each player
    players = players.map(p => {
      let filteredPts = 0;
      const tPts = {};
      for (const t of tourneys) {
        const pts = p.tourneyPoints[String(t.idx)] ?? 0;
        tPts[t.idx] = pts;
        filteredPts += pts;
      }
      return { ...p, filteredPts, tPts };
    });

    // 4. Hide empty columns (no visible player has points in that tourney)
    if (state.hideEmpty) {
      tourneys = tourneys.filter(t =>
        players.some(p => (p.tPts[t.idx] ?? 0) > 0)
      );
    }

    // 5. Sort
    const { sortCol, sortAsc } = state;
    players = [...players].sort((a, b) => {
      let va, vb;
      if      (sortCol === "rank")   { va = a.rank; vb = b.rank; }
      else if (sortCol === "name")   { va = a.name; vb = b.name; }
      else if (sortCol === "points") { va = a.filteredPts; vb = b.filteredPts; }
      else {
        va = a.tPts[sortCol] ?? 0;
        vb = b.tPts[sortCol] ?? 0;
      }
      if (va < vb) return sortAsc ? -1 : 1;
      if (va > vb) return sortAsc ?  1 : -1;
      return 0;
    });

    return { visibleTourneys: tourneys, visiblePlayers: players };
  }

  // ── Header ─────────────────────────────────────────────────────────────────

  function buildHeader(tourneys) {
    headerRow.innerHTML = "";
    sortThMap = {};

    addTh("",        "col-rank",  "rank");
    addTh("Player",  "col-name",  "name");
    addTh("Points",  "col-pts",   "points");

    for (const t of tourneys) {
      const th = document.createElement("th");
      th.className = "col-tourney";
      th.dataset.sort = t.idx;
      th.title = `${t.name} · ${fmtDate(t.date)} · ${t.category} · ${t.surface}`;
      th.innerHTML = `<span class="th-label">${shortName(t.name)}</span><span class="th-date">${fmtDateShort(t.date)}</span><span class="sort-arrow">▼</span>`;
      th.addEventListener("click", () => onSort(t.idx, th));
      if (state.sortCol === t.idx) {
        th.classList.add("sorted");
        th.querySelector(".sort-arrow").textContent = state.sortAsc ? "▲" : "▼";
      }
      sortThMap[t.idx] = th;
      headerRow.appendChild(th);
    }
  }

  function addTh(label, cls, sortKey) {
    const th = document.createElement("th");
    th.className = cls;
    th.dataset.sort = sortKey;
    th.innerHTML = `<span class="th-label">${label}</span><span class="sort-arrow">▼</span>`;
    th.addEventListener("click", () => onSort(sortKey, th));
    if (state.sortCol === sortKey) {
      th.classList.add("sorted");
      th.querySelector(".sort-arrow").textContent = state.sortAsc ? "▲" : "▼";
    }
    sortThMap[sortKey] = th;
    headerRow.appendChild(th);
  }

  function onSort(key, th) {
    if (state.sortCol === key) {
      state.sortAsc = !state.sortAsc;
    } else {
      state.sortCol = key;
      state.sortAsc = key === "name";
    }
    render();
  }

  // ── Rows ───────────────────────────────────────────────────────────────────
  const isFiltered = () =>
    state.selectedPlayers.size > 0 || state.favsOnly ||
    state.surfaces.size < ALL_SURFACES.length ||
    state.levels.size < ALL_LEVELS.length ||
    state.dateFrom || state.dateTo;

  function buildRows(players, tourneys) {
    tbody.innerHTML = "";

    for (const p of players) {
      const tr = document.createElement("tr");

      // Rank
      const tdRank = document.createElement("td");
      tdRank.className = "col-rank";
      tdRank.textContent = p.rank;
      tr.appendChild(tdRank);

      // Name + star
      const tdName = document.createElement("td");
      tdName.className = "col-name";
      const star = document.createElement("button");
      star.className = "star-btn" + (state.favoritePlayers.has(p.id) ? " active" : "");
      star.textContent = "⭐";
      star.title = "Favorite";
      star.addEventListener("click", e => { e.stopPropagation(); toggleFav(p.id, star); });
      tdName.appendChild(star);
      tdName.appendChild(document.createTextNode(p.name));
      tr.appendChild(tdName);

      // Points (filtered sum)
      const tdPts = document.createElement("td");
      tdPts.className = "pts-total" + (isFiltered() ? " filtered" : "");
      tdPts.textContent = fmt(p.filteredPts);
      tr.appendChild(tdPts);

      // Tournament columns
      for (const t of tourneys) {
        const pts = p.tPts[t.idx] ?? 0;
        const td = document.createElement("td");
        td.className = "pts-tourney";
        if (pts > 0) {
          td.textContent = fmt(pts);
          td.classList.add("has-pts", tierClass(t.category));
        } else {
          td.textContent = "·";
          td.classList.add("empty");
        }
        tr.appendChild(td);
      }

      tbody.appendChild(tr);
    }

    const noun = players.length === 1 ? "player" : "players";
    const tcols = tourneys.length;
    rowCount.textContent = `${players.length} ${noun} · ${tcols} tournaments`;
  }

  // ── Player search + tags ───────────────────────────────────────────────────
  function bindPlayerSearch() {
    const input    = $("player-search");
    const dropdown = $("player-dropdown");
    const tags     = $("player-tags");

    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      renderDropdown(q, dropdown);
    });

    input.addEventListener("focus", () => {
      dropdown.hidden = false;
      renderDropdown(input.value.trim().toLowerCase(), dropdown);
    });

    document.addEventListener("click", e => {
      if (!$("fg-players").contains(e.target)) dropdown.hidden = true;
    });

    renderTags(tags);
  }

  function renderDropdown(q, dropdown) {
    dropdown.innerHTML = "";

    const favs = data.players.filter(p => state.favoritePlayers.has(p.id));
    const results = q
      ? data.players.filter(p => p.name.toLowerCase().includes(q)).slice(0, 30)
      : [];

    if (favs.length > 0 && !q) {
      const sec = dropdownSection("⭐ Favorites", favs);
      dropdown.appendChild(sec);
    }
    if (results.length > 0) {
      const sec = dropdownSection("Players", results);
      dropdown.appendChild(sec);
    }
    if (favs.length === 0 && results.length === 0) {
      const empty = document.createElement("div");
      empty.className = "dropdown-item";
      empty.style.color = "#475569";
      empty.textContent = q ? "No matches" : "Type to search players";
      dropdown.appendChild(empty);
    }

    dropdown.hidden = false;
  }

  function dropdownSection(label, players) {
    const sec = document.createElement("div");
    sec.className = "dropdown-section";
    if (label) {
      const lbl = document.createElement("div");
      lbl.className = "dropdown-section-label";
      lbl.textContent = label;
      sec.appendChild(lbl);
    }
    for (const p of players) {
      const item = document.createElement("div");
      item.className = "dropdown-item" + (state.selectedPlayers.has(p.id) ? " selected" : "");

      const name = document.createElement("span");
      name.className = "di-name";
      name.textContent = p.name;

      const rank = document.createElement("span");
      rank.className = "di-rank";
      rank.textContent = `#${p.rank}`;

      const star = document.createElement("span");
      star.className = "di-star" + (state.favoritePlayers.has(p.id) ? " active" : "");
      star.textContent = "⭐";
      star.title = "Favorite";
      star.addEventListener("click", e => {
        e.stopPropagation();
        toggleFav(p.id, null);
        renderDropdown($("player-search").value.trim().toLowerCase(), $("player-dropdown"));
        renderTags($("player-tags"));
      });

      item.appendChild(name);
      item.appendChild(rank);
      item.appendChild(star);

      item.addEventListener("click", () => {
        if (state.selectedPlayers.has(p.id)) {
          state.selectedPlayers.delete(p.id);
        } else {
          state.selectedPlayers.add(p.id);
        }
        $("player-search").value = "";
        renderDropdown("", $("player-dropdown"));
        renderTags($("player-tags"));
        render();
      });

      sec.appendChild(item);
    }
    return sec;
  }

  function renderTags(tags) {
    tags.innerHTML = "";
    for (const id of state.selectedPlayers) {
      const p = data.players.find(x => x.id === id);
      if (!p) continue;
      const tag = document.createElement("div");
      tag.className = "player-tag" + (state.favoritePlayers.has(id) ? " is-fav" : "");
      tag.textContent = p.name;
      const rm = document.createElement("button");
      rm.textContent = "×";
      rm.addEventListener("click", () => {
        state.selectedPlayers.delete(id);
        renderTags(tags);
        render();
      });
      tag.appendChild(rm);
      tags.appendChild(tag);
    }
  }

  // ── Surface chips ──────────────────────────────────────────────────────────
  function bindSurfaceChips() {
    document.querySelectorAll("[data-surface]").forEach(btn => {
      btn.addEventListener("click", () => {
        const s = btn.dataset.surface;
        if (state.surfaces.has(s)) {
          if (state.surfaces.size === 1) return; // keep at least one
          state.surfaces.delete(s);
          btn.classList.remove("active");
        } else {
          state.surfaces.add(s);
          btn.classList.add("active");
        }
        render();
      });
    });
  }

  // ── Level chips ────────────────────────────────────────────────────────────
  function bindLevelChips() {
    document.querySelectorAll("[data-level]").forEach(btn => {
      btn.addEventListener("click", () => {
        const l = btn.dataset.level;
        if (state.levels.has(l)) {
          if (state.levels.size === 1) return;
          state.levels.delete(l);
          btn.classList.remove("active");
        } else {
          state.levels.add(l);
          btn.classList.add("active");
        }
        render();
      });
    });
  }

  // ── Date selects ───────────────────────────────────────────────────────────
  function buildDateSelects() {
    const dates = data.tournaments.map(t => t.date).sort();
    const months = uniqueMonths(dates);

    const fromSel = $("date-from");
    const toSel   = $("date-to");

    const allOpt = () => {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = "All";
      return o;
    };

    fromSel.appendChild(allOpt());
    toSel.appendChild(allOpt());

    for (const { value, label } of months) {
      const o1 = document.createElement("option");
      o1.value = value;
      o1.textContent = label;
      fromSel.appendChild(o1);

      const o2 = document.createElement("option");
      o2.value = value;
      o2.textContent = label;
      toSel.appendChild(o2);
    }

    fromSel.addEventListener("change", () => {
      state.dateFrom = fromSel.value ? fromSel.value + "01" : null;
      render();
    });
    toSel.addEventListener("change", () => {
      // end of selected month: value is YYYYMM, use 99 as day to be >= any day
      state.dateTo = toSel.value ? toSel.value + "99" : null;
      render();
    });
  }

  function uniqueMonths(dates) {
    const seen = new Set();
    const result = [];
    const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    for (const d of dates) {
      const ym = d.slice(0, 6);
      if (!seen.has(ym)) {
        seen.add(ym);
        const mo = parseInt(d.slice(4, 6), 10) - 1;
        const yr = d.slice(0, 4);
        result.push({ value: ym, label: `${MONTHS[mo]} ${yr}` });
      }
    }
    return result;
  }

  // ── Toggles ────────────────────────────────────────────────────────────────
  function bindToggles() {
    const emptyBtn = $("toggle-empty");
    const favsBtn  = $("toggle-favs");

    emptyBtn.addEventListener("click", () => {
      state.hideEmpty = !state.hideEmpty;
      emptyBtn.dataset.on = state.hideEmpty;
      render();
    });

    favsBtn.addEventListener("click", () => {
      state.favsOnly = !state.favsOnly;
      favsBtn.dataset.on = state.favsOnly;
      render();
    });
  }

  // ── Favorites ──────────────────────────────────────────────────────────────
  function loadFavs() {
    try {
      return new Set(JSON.parse(localStorage.getItem("atp-favs") || "[]"));
    } catch { return new Set(); }
  }

  function saveFavs() {
    localStorage.setItem("atp-favs", JSON.stringify([...state.favoritePlayers]));
  }

  function toggleFav(id, starEl) {
    if (state.favoritePlayers.has(id)) {
      state.favoritePlayers.delete(id);
    } else {
      state.favoritePlayers.add(id);
    }
    saveFavs();
    // Refresh all star buttons in the table
    document.querySelectorAll(".star-btn").forEach(btn => {
      const row = btn.closest("tr");
      if (!row) return;
      const nameCell = row.querySelector(".col-name");
      // find player by matching rendered name — use data attribute instead
    });
    render();
  }

  // ── Reset ──────────────────────────────────────────────────────────────────
  function updateResetBtn() {
    const dirty =
      state.selectedPlayers.size > 0 ||
      state.favsOnly ||
      state.surfaces.size < ALL_SURFACES.length ||
      state.levels.size < ALL_LEVELS.length ||
      state.dateFrom || state.dateTo ||
      state.hideEmpty;
    $("reset-filters").hidden = !dirty;
  }

  $("reset-filters").addEventListener("click", () => {
    state.selectedPlayers.clear();
    state.favsOnly  = false;
    state.hideEmpty = false;
    state.surfaces  = new Set(ALL_SURFACES);
    state.levels    = new Set(ALL_LEVELS);
    state.dateFrom  = null;
    state.dateTo    = null;

    // Reset UI
    document.querySelectorAll("[data-surface]").forEach(b => b.classList.add("active"));
    document.querySelectorAll("[data-level]").forEach(b => b.classList.add("active"));
    $("date-from").value = "";
    $("date-to").value   = "";
    $("toggle-empty").dataset.on = "false";
    $("toggle-favs").dataset.on  = "false";
    renderTags($("player-tags"));
    render();
  });

  // ── Helpers ────────────────────────────────────────────────────────────────
  function fmt(n) { return Number(n).toLocaleString(); }

  function fmtDate(d) {
    if (!d || d.length < 8) return d;
    return `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}`;
  }

  function fmtDateShort(d) {
    if (!d || d.length < 8) return "";
    const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    return `${MONTHS[parseInt(d.slice(4,6),10)-1]} ${parseInt(d.slice(6,8),10)}`;
  }

  function shortName(name) {
    const s = name
      .replace("ATP","").replace("Masters","M.").replace("Open","")
      .replace("Championships","").replace("International","").trim();
    return s.length > 12 ? s.slice(0, 11) + "…" : s;
  }

  function tierClass(category) {
    if (category === "G")   return "tier-gs";
    if (category === "M")   return "tier-m";
    if (category === "500") return "tier-500";
    if (category === "F")   return "tier-f";
    return "tier-250";
  }

  // ── Initial render (after all declarations) ────────────────────────────────
  render();
})();
