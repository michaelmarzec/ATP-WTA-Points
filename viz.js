(async () => {
  const $ = id => document.getElementById(id);

  // ── Site switch pill + title ───────────────────────────────────────────────
  const isWta = location.hostname === "wtapoints.com" || location.hostname === "www.wtapoints.com";
  const switchBar = document.querySelector(".site-switch-bar");
  if (switchBar) {
    const activeLabel = isWta ? "WTA" : "ATP";
    const sisterLabel = isWta ? "ATP" : "WTA";
    const sisterHref  = isWta ? "https://atppoints.com" : "https://wtapoints.com";
    switchBar.innerHTML = `<span class="tour-label">${activeLabel}</span><a href="${sisterHref}">${sisterLabel}</a>`;
  }
  if (isWta) {
    document.querySelector("h1").textContent = "WTA Points — Visualize";
    document.title = "WTA Points — Visualize";
    document.getElementById("favicon").href = "favicon-wta-32.png";
    // Hide ATP-only chip, show WTA-only chip; update 500/250 labels
    document.getElementById("viz-chip-M")?.remove();
    document.querySelectorAll("[data-val='500']").forEach(b => b.textContent = "WTA 500");
    document.querySelectorAll("[data-val='250']").forEach(b => b.textContent = "WTA 250");
  } else {
    // Hide WTA-only chip on ATP site; update 500/250 labels
    document.getElementById("viz-chip-1000")?.remove();
    document.querySelectorAll("[data-val='500']").forEach(b => b.textContent = "ATP 500");
    document.querySelectorAll("[data-val='250']").forEach(b => b.textContent = "ATP 250");
  }

  // ── Load data ──────────────────────────────────────────────────────────────
  let data;
  try {
    const tour = isWta ? "wta" : "atp";
    const resp = await fetch(`data/${tour}/rankings.json`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (e) {
    $("loading").hidden = true;
    $("error").hidden = false;
    $("error").textContent = `Failed to load data: ${e.message}`;
    return;
  }
  $("loading").hidden = true;
  $("viz-shell").hidden = false;
  $("meta").textContent =
    `Rankings as of ${fmtDate(data.rankingsDate)} · ${data.players.length} players · ${data.tournaments.length} tournaments`;

  // ── Constants ──────────────────────────────────────────────────────────────
  const CAT_COLORS  = { G:"#fbbf24", M:"#34d399", "1000":"#34d399", "500":"#60a5fa", "250":"#a78bfa", F:"#f472b6" };
  const CAT_LABELS  = isWta
    ? { G:"Grand Slam", "1000":"WTA 1000", "500":"WTA 500", "250":"WTA 250", F:"Finals" }
    : { G:"Grand Slam", M:"Masters",       "500":"ATP 500", "250":"ATP 250", F:"Finals" };
  const SURF_COLORS = { Hard:"#60a5fa", Clay:"#f97316", Grass:"#34d399" };
  const PLAYER_PAL  = ["#60a5fa","#f472b6","#34d399","#fbbf24","#a78bfa","#fb923c","#2dd4bf","#f87171","#a3e635","#e879f9"];

  // ── Shared tooltip ─────────────────────────────────────────────────────────
  const tooltip = $("viz-tooltip");
  function showTip(html, clientX, clientY) {
    tooltip.innerHTML = html;
    tooltip.hidden = false;
    positionTip(clientX, clientY);
  }
  function positionTip(cx, cy) {
    const pad = 14, tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
    const wx = window.innerWidth, wy = window.innerHeight;
    tooltip.style.left = (cx + pad + tw > wx ? cx - tw - pad : cx + pad) + "px";
    tooltip.style.top  = (cy + pad + th > wy ? cy - th - pad : cy + pad) + "px";
  }
  function hideTip() { tooltip.hidden = true; }
  document.addEventListener("mousemove", e => { if (!tooltip.hidden) positionTip(e.clientX, e.clientY); });

  // ── Tabs ───────────────────────────────────────────────────────────────────
  let activeTab = "stacked";
  const tabPanels = {
    stacked: $("tab-stacked"), sankey: $("tab-sankey"),
    bump:    $("tab-bump"),    heatmap: $("tab-heatmap"),
  };
  document.querySelectorAll(".tab").forEach(btn => {
    btn.addEventListener("click", () => {
      hideTip();
      document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      Object.values(tabPanels).forEach(p => p.hidden = true);
      activeTab = btn.dataset.tab;
      tabPanels[activeTab].hidden = false;
      drawActive();
    });
  });
  function drawActive() {
    if (activeTab === "stacked") drawStacked();
    if (activeTab === "sankey")  drawSankey();
    if (activeTab === "bump")    drawBump();
    if (activeTab === "heatmap") drawHeatmap();
  }

  // ── Tournament lookup: find all tourneys in the week ending on a ranking date ──
  // Ranking dates are Mondays; tournaments in the preceding 7 days show as "last week"
  function tourneysLastWeek(rankingDate) {
    const toDate = d => new Date(`${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}`);
    const end   = toDate(rankingDate);
    const start = new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000);
    return data.tournaments.filter(t => {
      const td = toDate(t.date);
      return td >= start && td <= end;
    });
  }

  // Keep for backwards-compat reference (unused now)
  function lastTourneyBefore(rankingDate) {
    let best = null;
    for (const t of data.tournaments) {
      if (t.date <= rankingDate && (best === null || t.date > best.date)) {
        best = t;
      }
    }
    return best;
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 1. STACKED BAR — per-tournament segments, By Level | By Surface toggle
  // ══════════════════════════════════════════════════════════════════════════
  let stackedChart = null;
  let stackedMode = "level"; // "level" | "surface"
  const stackedSurfaces = new Set(["Hard","Clay","Grass"]);
  const stackedLevels   = new Set(isWta ? ["G","1000","500","250","F"] : ["G","M","500","250","F"]);

  // Mode toggle (mutually exclusive)
  document.querySelectorAll("#stacked-mode [data-val]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#stacked-mode [data-val]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      stackedMode = btn.dataset.val;
      drawStacked();
    });
  });
  bindChips("stacked-surface", stackedSurfaces, drawStacked);
  bindChips("stacked-level",   stackedLevels,   drawStacked);
  $("stacked-n").addEventListener("change", drawStacked);

  function drawStacked() {
    const n = parseInt($("stacked-n").value);
    const isLevel = stackedMode === "level";

    // Tournaments filtered by current chips
    const visT = data.tournaments
      .map((t,i) => ({...t, i}))
      .filter(t => stackedSurfaces.has(t.surface) && stackedLevels.has(t.category));

    // Group keys in display order
    const groupOrder = isLevel
      ? (isWta ? ["G","1000","500","250","F"] : ["G","M","500","250","F"]).filter(g => stackedLevels.has(g))
      : ["Hard","Clay","Grass"].filter(g => stackedSurfaces.has(g));

    // Per-player totals for sorting
    const playerTotals = data.players.map(p => {
      let total = 0;
      for (const t of visT) total += p.tourneyPoints[String(t.i)] ?? 0;
      return { ...p, total };
    }).sort((a,b) => b.total - a.total).slice(0, n);

    const labels = playerTotals.map(p => p.name.split(" ").pop());

    // One dataset per tournament, ordered by group then date
    const datasets = [];
    for (const grp of groupOrder) {
      const grpTourneys = visT.filter(t => (isLevel ? t.category : t.surface) === grp)
        .sort((a,b) => a.date.localeCompare(b.date));

      const baseColor = isLevel ? CAT_COLORS[grp] : SURF_COLORS[grp];

      grpTourneys.forEach((t, idx) => {
        datasets.push({
          label: t.name,
          _group: grp,
          _grpFirst: idx === 0,
          data: playerTotals.map(p => p.tourneyPoints[String(t.i)] ?? 0),
          backgroundColor: baseColor,
          borderWidth: 0,
        });
      });
    }

    // Custom legend (groups only)
    const legendEl = $("stacked-legend");
    legendEl.innerHTML = "";
    groupOrder.forEach(grp => {
      const col = isLevel ? CAT_COLORS[grp] : SURF_COLORS[grp];
      const lbl = isLevel ? CAT_LABELS[grp] : grp;
      const item = document.createElement("div");
      item.className = "stacked-legend-item";
      item.innerHTML = `<div class="stacked-legend-swatch" style="background:${col}"></div>${lbl}`;
      legendEl.appendChild(item);
    });

    const ctx = $("stacked-chart").getContext("2d");
    // Plugin: draw grey dashed lines between within-group tournament segments
    const dashedDividers = {
      id: "dashedDividers",
      afterDraw(chart) {
        const { ctx, data: { datasets } } = chart;
        ctx.save();
        ctx.setLineDash([2, 3]);
        ctx.lineWidth = 1;
        datasets.forEach((ds, di) => {
          if (ds._grpFirst) return; // skip group boundaries (already have stronger border)
          const meta = chart.getDatasetMeta(di);
          if (meta.hidden) return;
          meta.data.forEach((bar) => {
            if (!bar || bar.base === bar.y) return; // zero segment
            const hw = bar.width / 2;
            ctx.strokeStyle = "rgba(140,140,180,0.22)";
            ctx.beginPath();
            ctx.moveTo(bar.x - hw + 1, bar.y);
            ctx.lineTo(bar.x + hw - 1, bar.y);
            ctx.stroke();
          });
        });
        ctx.restore();
      }
    };

    if (stackedChart) stackedChart.destroy();
    stackedChart = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets },
      plugins: [dashedDividers],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: items => playerTotals[items[0].dataIndex].name,
              label: item => item.raw > 0 ? ` ${item.dataset.label}: ${item.raw.toLocaleString()} pts` : null,
              filter: item => item.raw > 0,
            }
          }
        },
        animation: false,
        scales: {
          x: { stacked:true, ticks:{ color:"#64748b", font:{size:10} }, grid:{ color:"#1a2030" } },
          y: { stacked:true, min:0, ticks:{ color:"#64748b" }, grid:{ color:"#1a2030" } },
        }
      }
    });
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 2. SANKEY — 3-layer: Surface → Tournament → Level
  // ══════════════════════════════════════════════════════════════════════════
  let sankeyPlayerId = data.players[0].id;
  $("sankey-player-name").textContent = data.players[0].name;

  buildPickerList("sankey-list", "sankey-search", false, (p) => {
    sankeyPlayerId = p.id;
    $("sankey-player-name").textContent = p.name;
    // highlight selected in list
    document.querySelectorAll("#sankey-list .picker-item").forEach(el => {
      el.classList.toggle("selected", el.dataset.pid === p.id);
      el.querySelector(".pi-dot").style.setProperty("--c", el.classList.contains("selected") ? "#60a5fa" : "");
    });
    drawSankey();
  });

  function drawSankey() {
    const wrap = $("sankey-wrap");
    wrap.innerHTML = "";

    const player = data.players.find(p => p.id === sankeyPlayerId);
    if (!player) return;

    const entries = data.tournaments
      .map((t,i) => ({...t, i, pts: player.tourneyPoints[String(i)] ?? 0}))
      .filter(e => e.pts > 0)
      .sort((a,b) => b.pts - a.pts);

    if (!entries.length) {
      wrap.innerHTML = `<p style="color:#475569;padding:24px">${player.name} has no points in this window.</p>`;
      return;
    }

    const surfaces = [...new Set(entries.map(e => e.surface))];
    const levels   = [...new Set(entries.map(e => e.category))];

    // Nodes: surfaces | tournaments | levels
    const surfNodes  = surfaces.map(s => ({ name: s, _type: "surface" }));
    // Use tournament index as unique key to avoid name collisions (e.g. Rome 2025 + Rome 2026)
    const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const tourNodes  = entries.map(e => {
      const d = e.date;
      const shortDate = d && d.length >= 8 ? `${MONTHS[parseInt(d.slice(4,6),10)-1]} ${parseInt(d.slice(6,8),10)}` : "";
      return { name: `${e.name} (${shortDate})`, _type: "tourney", _entry: e, _key: `t_${e.i}` };
    });
    const levelNodes = levels.map(l => ({ name: l, _type: "level" }));

    const allNodes = [...surfNodes, ...tourNodes, ...levelNodes];
    // Index by unique key
    const nodeIdx = {};
    surfNodes.forEach((n,i)  => { nodeIdx[n.name + "_surface"] = i; });
    tourNodes.forEach((n,i)  => { nodeIdx[n._key] = surfNodes.length + i; });
    levelNodes.forEach((n,i) => { nodeIdx[n.name + "_level"] = surfNodes.length + tourNodes.length + i; });

    // Links: surface → tournament, tournament → level
    const links = [];
    entries.forEach(e => {
      const tKey = `t_${e.i}`;
      links.push({
        source: nodeIdx[e.surface + "_surface"],
        target: nodeIdx[tKey],
        value: e.pts, _tourIdx: e.i,
      });
      links.push({
        source: nodeIdx[tKey],
        target: nodeIdx[e.category + "_level"],
        value: e.pts, _tourIdx: e.i,
      });
    });

    const W = Math.min(780, wrap.clientWidth || 720);
    const H = Math.max(340, entries.length * 22 + 80);
    const ml = 90, mr = 130;

    const svg = d3.select(wrap).append("svg").attr("width", W).attr("height", H);

    const LEVEL_ORDER = ["G", "M", "1000", "F", "500", "250"];
    const sk = d3.sankey()
      .nodeWidth(14).nodePadding(5)
      .nodeSort((a, b) => {
        // Right column (levels): fixed canonical order
        if (a._type === "level" && b._type === "level") {
          return LEVEL_ORDER.indexOf(a.name) - LEVEL_ORDER.indexOf(b.name);
        }
        // Left column (surfaces) + middle (tournaments): most points at top
        return (b.value ?? 0) - (a.value ?? 0);
      })
      .extent([[ml, 16], [W - mr, H - 16]]);

    const { nodes: skNodes, links: skLinks } = sk({ nodes: allNodes, links });

    // Post-process: enforce min height on right-column (level) nodes,
    // then restack them top-to-bottom so none overlap, and update link attachment points.
    (() => {
      const MIN_H = 8, PAD = 5;
      const lvlNds = skNodes.filter(n => n._type === "level")
        .sort((a, b) => LEVEL_ORDER.indexOf(a.name) - LEVEL_ORDER.indexOf(b.name));
      if (!lvlNds.length) return;

      const totalPts = lvlNds.reduce((s, n) => s + (n.value ?? 0), 0);
      const availH   = H - 32 - (lvlNds.length - 1) * PAD;
      const heights  = lvlNds.map(n =>
        Math.max(MIN_H, totalPts > 0 ? availH * (n.value ?? 0) / totalPts : MIN_H));

      // Scale down if heights exceed available space (can happen when many nodes have min height)
      const sum   = heights.reduce((a, b) => a + b, 0);
      const scale = sum > availH ? availH / sum : 1;

      let curY = 16;
      lvlNds.forEach((n, i) => {
        const h = heights[i] * scale;
        n.y0 = curY;
        n.y1 = curY + h;
        curY += h + PAD;

        // Restack incoming links within the repositioned node, preserving relative order
        let linkY = n.y0;
        [...n.targetLinks]
          .sort((a, b) => a.y1 - b.y1)
          .forEach(link => {
            link.y1 = linkY + link.width / 2;
            linkY += link.width;
          });
      });
    })();

    // Color helper
    function nodeColor(n) {
      if (n._type === "surface") return SURF_COLORS[n.name] ?? "#475569";
      if (n._type === "level")   return CAT_COLORS[n.name]  ?? "#475569";
      return "#64748b";
    }

    // Links
    const linkSel = svg.selectAll(".sk-link")
      .data(skLinks).join("path")
      .attr("class", "sk-link")
      .attr("d", d3.sankeyLinkHorizontal())
      .attr("stroke-width", d => Math.max(1, d.width))
      .attr("stroke", d => nodeColor(skNodes[d.source.index]))
      .attr("fill", "none").attr("opacity", 0.35)
      .style("cursor", "pointer");

    function highlight(tourIdx) {
      linkSel.attr("opacity", d =>
        tourIdx == null ? 0.35 : (d._tourIdx === tourIdx ? 0.8 : 0.08));
      nodeSel.attr("opacity", d =>
        tourIdx == null ? 0.9 :
        (d._type !== "tourney" || d._entry?.i === tourIdx ? 0.95 : 0.2));
    }

    linkSel
      .on("mouseenter", (event, d) => {
        highlight(d._tourIdx);
        const e = entries.find(x => x.i === d._tourIdx);
        if (e) showTip(tourTip(e), event.clientX, event.clientY);
      })
      .on("mouseleave", () => { highlight(null); hideTip(); });

    // Nodes
    const nodeSel = svg.selectAll(".sk-node")
      .data(skNodes).join("rect")
      .attr("class", "sk-node")
      .attr("x", d => d.x0).attr("y", d => d.y0)
      .attr("width", d => d.x1 - d.x0)
      .attr("height", d => Math.max(1, d.y1 - d.y0))
      .attr("fill", d => nodeColor(d)).attr("opacity", 0.9)
      .style("cursor", d => d._type === "tourney" ? "pointer" : "default")
      .on("mouseenter", (event, d) => {
        if (d._type === "tourney") {
          highlight(d._entry?.i);
          showTip(tourTip(d._entry), event.clientX, event.clientY);
        }
      })
      .on("mouseleave", () => { highlight(null); hideTip(); });

    // Labels
    svg.selectAll(".sk-label")
      .data(skNodes).join("text")
      .attr("x", d => d.x0 < W / 2 ? d.x1 + 6 : d.x0 - 6)
      .attr("y", d => (d.y0 + d.y1) / 2)
      .attr("dy", "0.35em")
      .attr("text-anchor", d => d.x0 < W / 2 ? "start" : "end")
      .attr("fill", d => d._type === "tourney" ? "#94a3b8" : "#e2e8f0")
      .attr("font-size", d => d._type === "tourney" ? 10 : 11)
      .attr("font-weight", d => d._type === "tourney" ? 400 : 600)
      .text(d => {
        if (d._type === "surface") return `${d.name} (${(d.value??0).toLocaleString()})`;
        if (d._type === "level")   return `${CAT_LABELS[d.name]??d.name} (${(d.value??0).toLocaleString()})`;
        const nm = d.name.replace(" Open","").replace(" Masters","").replace("ATP ","").trim();
        return nm.length > 15 ? nm.slice(0,14)+"…" : nm;
      });

    function tourTip(e) {
      if (!e) return "";
      return `<div class="tt-title">${e.name}</div>
        <div class="tt-row"><span class="tt-label">Points</span><span class="tt-val">${e.pts.toLocaleString()}</span></div>
        <div class="tt-row"><span class="tt-label">Surface</span><span class="tt-val">${e.surface}</span></div>
        <div class="tt-row"><span class="tt-label">Level</span><span class="tt-val">${CAT_LABELS[e.category]??e.category}</span></div>
        <div class="tt-row"><span class="tt-label">Date</span><span class="tt-val">${fmtDate(e.date)}</span></div>`;
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 3. BUMP CHART — multi-select player list, crosshair + tourney tooltip
  // ══════════════════════════════════════════════════════════════════════════
  let bumpMode = "top";
  const bumpPlayers = [];

  function computeBumpDefault(mode) {
    const weeks = data.weeklyRankings;
    if (!weeks?.length) return data.players.slice(0, 15);
    if (mode === "top") return data.players.slice(0, 15);

    // Find earliest week ~52 weeks back from last week
    const lastWeek = weeks[weeks.length - 1];
    const lastDate = lastWeek.date;
    const oneYearAgo = String(parseInt(lastDate.slice(0,4)) - 1) + lastDate.slice(4);
    const earlyWeek = weeks.find(w => w.date >= oneYearAgo) ?? weeks[0];

    const earlyMap = new Map(earlyWeek.rankings.map(r => [r.id, r.rank]));
    const lastMap  = new Map(lastWeek.rankings.map(r => [r.id, r.rank]));

    const deltas = [];
    for (const [id, currentRank] of lastMap) {
      const oldRank = earlyMap.get(id);
      if (oldRank == null) continue;
      deltas.push({ id, delta: oldRank - currentRank }); // positive = moved up
    }

    const minDelta = 2;
    const sorted = mode === "risers"
      ? deltas.filter(d => d.delta >= minDelta).sort((a,b) => b.delta - a.delta)
      : deltas.filter(d => d.delta <= -minDelta).sort((a,b) => a.delta - b.delta);

    return sorted.slice(0, 15).map(d => data.players.find(p => p.id === d.id)).filter(Boolean);
  }

  function applyBumpMode(mode) {
    bumpMode = mode;
    bumpPlayers.length = 0;
    computeBumpDefault(mode).forEach((p, i) => {
      bumpPlayers.push({ id: p.id, name: p.name, color: PLAYER_PAL[i % PLAYER_PAL.length] });
    });
    renderBumpTags();
    refreshPickerSelection("bump-list");
    drawBump();
  }

  document.querySelectorAll("#bump-mode [data-val]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#bump-mode [data-val]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      applyBumpMode(btn.dataset.val);
    });
  });

  applyBumpMode("top");

  buildPickerList("bump-list", "bump-search", true, (p, selected) => {
    if (selected) {
      if (bumpPlayers.length >= 20) return;
      bumpPlayers.push({ id:p.id, name:p.name, color:PLAYER_PAL[bumpPlayers.length % PLAYER_PAL.length] });
    } else {
      const idx = bumpPlayers.findIndex(x => x.id === p.id);
      if (idx >= 0) bumpPlayers.splice(idx, 1);
    }
    renderBumpTags();
    drawBump();
  }, () => new Set(bumpPlayers.map(p => p.id)));

  $("bump-clear").addEventListener("click", () => {
    applyBumpMode(bumpMode);
  });

  function renderBumpTags() {
    const el = $("bump-player-tags");
    el.innerHTML = "";
    bumpPlayers.forEach((p,i) => {
      const tag = document.createElement("div");
      tag.className = "bump-tag";
      tag.style.cssText = `border-color:${p.color};color:${p.color};background:${p.color}22`;
      tag.textContent = p.name.split(" ").pop();
      const rm = document.createElement("button");
      rm.textContent = "×";
      rm.addEventListener("click", () => {
        bumpPlayers.splice(i,1);
        renderBumpTags();
        refreshPickerSelection("bump-list");
        drawBump();
      });
      tag.appendChild(rm);
      el.appendChild(tag);
    });
  }

  function drawBump() {
    const wrap = $("bump-wrap");
    wrap.innerHTML = "";
    if (!data.weeklyRankings?.length || !bumpPlayers.length) {
      wrap.innerHTML = `<p style="color:#475569;padding:0">Select players to compare ranking trajectories.</p>`;
      return;
    }

    const weeks = data.weeklyRankings;
    const series = bumpPlayers.map(p => ({
      ...p,
      points: weeks.map(w => {
        const entry = w.rankings.find(r => r.id === p.id);
        return entry ? { date: w.date, rank: entry.rank } : null;
      }).filter(Boolean)
    }));

    const allDates = weeks.map(w => w.date);
    const mL=48, mR=150, mT=24, mB=40;
    const W = Math.max(700, allDates.length * 18 + mL + mR);
    const H = 440;

    // Dynamic y domain based on actual ranks in view
    const allRanks = series.flatMap(s => s.points.map(p => p.rank));
    const maxRank = Math.max(50, ...allRanks);
    const yTicks = [1,5,10,20,30,40,50,75,100,150,200,300,400,500].filter(t => t <= maxRank + 10);

    const svg = d3.select(wrap).append("svg").attr("width", W).attr("height", H);
    const x = d3.scalePoint().domain(allDates).range([mL, W - mR]).padding(0.2);
    const y = d3.scaleLinear().domain([1, maxRank]).range([mT, H - mB]);

    // Gridlines
    svg.selectAll(".hg").data(yTicks).join("line")
      .attr("x1",mL).attr("x2",W-mR).attr("y1",d=>y(d)).attr("y2",d=>y(d))
      .attr("stroke","#1a2030").attr("stroke-width",1);

    // Y axis
    svg.selectAll(".yl").data(yTicks).join("text")
      .attr("x",mL-6).attr("y",d=>y(d)+4).attr("text-anchor","end")
      .attr("fill","#475569").attr("font-size",10).text(d=>`#${d}`);

    // X axis month labels
    const seen = new Set();
    svg.selectAll(".xl").data(allDates).join("text")
      .attr("x",d=>x(d)).attr("y",H-mB+16).attr("text-anchor","middle")
      .attr("fill","#475569").attr("font-size",9)
      .text(d=>{ const ym=d.slice(0,6); if(seen.has(ym)) return ""; seen.add(ym); return fmtMonthShort(d); });

    const line = d3.line().x(d=>x(d.date)).y(d=>y(d.rank)).curve(d3.curveCatmullRom.alpha(0.5));

    // Lines + end labels
    series.forEach(s => {
      svg.append("path").datum(s.points)
        .attr("fill","none").attr("stroke",s.color).attr("stroke-width",2.5)
        .attr("opacity",.85).attr("d",line);
      const last = s.points.at(-1);
      if (last) {
        svg.append("text")
          .attr("x",x(last.date)+10).attr("y",y(last.rank)+4)
          .attr("fill",s.color).attr("font-size",11).attr("font-weight",600)
          .text(s.name.split(" ").pop());
      }
    });

    // Dots (interactive)
    series.forEach(s => {
      svg.selectAll(null).data(s.points).join("circle")
        .attr("cx",d=>x(d.date)).attr("cy",d=>y(d.rank))
        .attr("r",4).attr("fill",s.color).attr("stroke","#0f1117").attr("stroke-width",1.5)
        .style("cursor","pointer")
        .on("mouseenter",(event,d)=>{
          showTip(`<div class="tt-title">${s.name}</div>
            <div class="tt-row"><span class="tt-label">Rank</span><span class="tt-val" style="color:${s.color}">#${d.rank}</span></div>
            <div class="tt-row"><span class="tt-label">Week</span><span class="tt-val">${fmtDate(d.date)}</span></div>`,
            event.clientX, event.clientY);
        })
        .on("mouseleave", hideTip);
    });

    // Crosshair overlay
    const crossLine = svg.append("line")
      .attr("y1",mT).attr("y2",H-mB).attr("stroke","#334155")
      .attr("stroke-width",1).attr("stroke-dasharray","4,3")
      .attr("opacity",0).attr("pointer-events","none");

    svg.append("rect")
      .attr("x",mL).attr("y",mT).attr("width",W-mL-mR).attr("height",H-mT-mB)
      .attr("fill","transparent")
      .on("mousemove", (event) => {
        const [mx] = d3.pointer(event);
        let best=null, bestDist=Infinity;
        allDates.forEach(d=>{ const dist=Math.abs(x(d)-mx); if(dist<bestDist){bestDist=dist;best=d;} });
        if (!best) return;
        crossLine.attr("x1",x(best)).attr("x2",x(best)).attr("opacity",1);

        const weekTourneys = tourneysLastWeek(best);
        const rankRows = series
          .map(s => {
            const pt = s.points.find(p=>p.date===best);
            if (!pt) return null;
            const ptsThisWeek = weekTourneys.reduce((sum, t) => {
              const ti = data.tournaments.indexOf(t);
              return sum + (data.players.find(p=>p.id===s.id)?.tourneyPoints[String(ti)] ?? 0);
            }, 0);
            return { s, pt, ptsThisWeek };
          })
          .filter(Boolean)
          .sort((a, b) => a.pt.rank - b.pt.rank)
          .map(({ s, pt, ptsThisWeek }) => {
            const ptsStr = ptsThisWeek > 0
              ? ` <span style="color:#64748b;font-size:10px">(${ptsThisWeek.toLocaleString()} pts)</span>`
              : "";
            return `<div class="tt-row"><span class="tt-label" style="color:${s.color}">${s.name.split(" ").pop()}</span><span class="tt-val">#${pt.rank}${ptsStr}</span></div>`;
          }).join("");

        const tourneySection = weekTourneys.length
          ? `<hr class="tt-divider"><div class="tt-row" style="font-size:10px;color:#475569;margin-bottom:2px">Last week's tourneys</div>`
            + weekTourneys.map(t => `<div class="tt-row"><span class="tt-label">${t.name}</span><span class="tt-val" style="font-size:10px">${t.surface}</span></div>`).join("")
          : "";

        showTip(`<div class="tt-title">${fmtDate(best)}</div>${rankRows}${tourneySection}`, event.clientX+16, event.clientY);
      })
      .on("mouseleave", () => { crossLine.attr("opacity",0); hideTip(); });
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 4. HEATMAP — hover on column headers + cells
  // ══════════════════════════════════════════════════════════════════════════
  $("heatmap-n").addEventListener("change", drawHeatmap);

  // Build legend (static)
  const hmLegend = $("heatmap-legend");
  (isWta ? ["G","1000","500","250","F"] : ["G","M","500","250","F"]).forEach(cat => {
    const item = document.createElement("div");
    item.className = "heatmap-legend-item";
    item.innerHTML = `<div class="heatmap-legend-swatch" style="background:${CAT_COLORS[cat]}"></div>${CAT_LABELS[cat]}`;
    hmLegend.appendChild(item);
  });

  function drawHeatmap() {
    const wrap = $("heatmap-wrap");
    wrap.innerHTML = "";
    const n = parseInt($("heatmap-n").value);
    const players  = data.players.slice(0, n);
    const tourneys = data.tournaments;

    const cellW=14, cellH=20;
    const mL=140, mT=110, mR=20, mB=20;
    const W = mL + tourneys.length * cellW + mR;
    const H = mT + players.length * cellH + mB;

    const svg = d3.select(wrap).append("svg")
      .attr("width",W).attr("height",H).style("font-size","11px");

    const maxPts = d3.max(players, p =>
      d3.max(tourneys, (t,i) => p.tourneyPoints[String(i)] ?? 0));

    function cellColor(pts, cat) {
      if (!pts) return "#0d1018";
      return d3.interpolate("#1a2030", CAT_COLORS[cat] ?? "#60a5fa")(Math.sqrt(pts / maxPts));
    }

    const g = svg.append("g").attr("transform", `translate(${mL},${mT})`);

    // Column headers — interactive
    g.selectAll(".th")
      .data(tourneys)
      .join("text")
      .attr("class","th")
      .attr("x",(d,i) => i*cellW + cellW/2)
      .attr("y",0)
      .attr("text-anchor","start")
      .attr("transform",(d,i) => `rotate(-55, ${i*cellW+cellW/2}, 0)`)
      .attr("fill","#475569").attr("font-size",9)
      .style("cursor","pointer")
      .text(d => shortName(d.name))
      .on("mouseenter",(event,d) => {
        showTip(`<div class="tt-title">${d.name}</div>
          <div class="tt-row"><span class="tt-label">Date</span><span class="tt-val">${fmtDate(d.date)}</span></div>
          <div class="tt-row"><span class="tt-label">Surface</span><span class="tt-val">${d.surface}</span></div>
          <div class="tt-row"><span class="tt-label">Level</span><span class="tt-val">${CAT_LABELS[d.category]??d.category}</span></div>
          ${d.winner ? `<div class="tt-row"><span class="tt-label">Winner</span><span class="tt-val">${d.winner}</span></div>` : ""}`,
          event.clientX, event.clientY);
      })
      .on("mouseleave", hideTip);

    // Row labels
    g.selectAll(".pl")
      .data(players)
      .join("text")
      .attr("class","pl")
      .attr("x",-6).attr("y",(d,i)=>i*cellH+cellH*.72)
      .attr("text-anchor","end").attr("fill","#94a3b8").attr("font-size",11)
      .text(d=>d.name);

    // Cells
    players.forEach((p,pi) => {
      tourneys.forEach((t,ti) => {
        const pts = p.tourneyPoints[String(ti)] ?? 0;
        g.append("rect")
          .attr("x",ti*cellW+1).attr("y",pi*cellH+1)
          .attr("width",cellW-2).attr("height",cellH-2).attr("rx",2)
          .attr("fill",cellColor(pts,t.category))
          .style("cursor","pointer")
          .on("mouseenter",(event)=>{
            showTip(`<div class="tt-title">${p.name}</div>
              <div class="tt-row"><span class="tt-label">Tournament</span><span class="tt-val">${t.name}</span></div>
              <div class="tt-row"><span class="tt-label">Points</span><span class="tt-val">${pts>0?pts.toLocaleString():"—"}</span></div>
              <div class="tt-row"><span class="tt-label">Surface</span><span class="tt-val">${t.surface}</span></div>
              <div class="tt-row"><span class="tt-label">Date</span><span class="tt-val">${fmtDate(t.date)}</span></div>
              ${t.winner?`<div class="tt-row"><span class="tt-label">Winner</span><span class="tt-val">${t.winner}</span></div>`:""}`,
              event.clientX, event.clientY);
          })
          .on("mouseleave", hideTip);
      });
    });
  }

  // ══════════════════════════════════════════════════════════════════════════
  // PLAYER PICKER — reusable scrollable list (single or multi select)
  // ══════════════════════════════════════════════════════════════════════════
  function buildPickerList(listId, searchId, isMulti, onToggle, getSelected) {
    const listEl   = $(listId);
    const searchEl = $(searchId);

    // Make the list a hidden dropdown
    listEl.hidden = true;

    function renderList(q) {
      const filtered = q
        ? data.players.filter(p => p.name.toLowerCase().includes(q.toLowerCase()))
        : data.players;
      const selected = getSelected ? getSelected() : new Set();

      listEl.innerHTML = "";
      filtered.slice(0, 150).forEach(p => {
        const item = document.createElement("div");
        item.className = "picker-item" + (selected.has(p.id) ? " selected" : "");
        item.dataset.pid = p.id;

        const dot = document.createElement("div");
        dot.className = "pi-dot";

        const name = document.createElement("span");
        name.className = "pi-name";
        name.textContent = p.name;

        const rank = document.createElement("span");
        rank.className = "pi-rank";
        rank.textContent = `#${p.rank}`;

        item.appendChild(dot);
        item.appendChild(name);
        item.appendChild(rank);

        item.addEventListener("mousedown", (e) => {
          // mousedown fires before blur, prevent input from losing focus handling
          e.preventDefault();
        });
        item.addEventListener("click", () => {
          if (isMulti) {
            const nowSelected = !item.classList.contains("selected");
            onToggle(p, nowSelected);
            renderList(searchEl.value.trim());
          } else {
            listEl.hidden = true;
            searchEl.value = "";
            searchEl.blur();
            onToggle(p, true);
          }
        });

        listEl.appendChild(item);
      });
    }

    if (searchEl) {
      searchEl.addEventListener("focus", () => {
        renderList(searchEl.value.trim());
        listEl.hidden = false;
      });
      searchEl.addEventListener("input", () => renderList(searchEl.value.trim()));
      searchEl.addEventListener("blur", () => {
        // Small delay so click on item fires first
        setTimeout(() => { listEl.hidden = true; }, 150);
      });
    }
  }

  function refreshPickerSelection(listId) {
    // Re-render list to update selected state after external change (e.g. clear)
    const listEl = $(listId);
    if (!listEl) return;
    const searchEl = { "bump-list": $("bump-search") }[listId];
    const q = searchEl ? searchEl.value.trim() : "";
    const sel = new Set(bumpPlayers.map(p => p.id));
    listEl.querySelectorAll(".picker-item").forEach(item => {
      item.classList.toggle("selected", sel.has(item.dataset.pid));
    });
  }

  // ══════════════════════════════════════════════════════════════════════════
  // UTILITIES
  // ══════════════════════════════════════════════════════════════════════════
  function bindChips(containerId, stateSet, redraw) {
    document.querySelectorAll(`#${containerId} [data-val]`).forEach(btn => {
      btn.addEventListener("click", () => {
        const v = btn.dataset.val;
        if (stateSet.has(v)) { if (stateSet.size===1) return; stateSet.delete(v); btn.classList.remove("active"); }
        else { stateSet.add(v); btn.classList.add("active"); }
        redraw();
      });
    });
  }

  function fmtDate(d) {
    if (!d||d.length<8) return d||"";
    return `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}`;
  }
  function fmtMonthShort(d) {
    const M=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    return `${M[parseInt(d.slice(4,6),10)-1]} '${d.slice(2,4)}`;
  }
  function shortName(name) {
    const s = name.replace("ATP","").replace("Masters","M.").replace("Open","")
      .replace("Championships","").replace("International","").trim();
    return s.length>10 ? s.slice(0,9)+"…" : s;
  }

  // ── Initial render ─────────────────────────────────────────────────────────
  renderBumpTags();
  drawActive();

})();
