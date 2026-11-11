"use strict";

const ORDER = ["agents", "benchmark", "random", "agents_lowcost"];
const SHORT = { agents: "Équipe", agents_lowcost: "Équipe low-cost", benchmark: "ETF Monde", random: "Aléatoire" };
const state = {};

// --- Utilitaires -------------------------------------------------------------
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = (x, d = 2) => x == null ? "–" : Number(x).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }) + " €";
const pct = (x, sign = true) => x == null ? "–" : (sign && x > 0 ? "+" : "") + Number(x).toLocaleString("fr-FR", { maximumFractionDigits: 2, minimumFractionDigits: 2 }) + " %";
const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const fmtDate = (d) => new Date(d + "T12:00:00").toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const ret = (b) => { const s = b.series; return s.length ? (s[s.length - 1].total / b.deposits - 1) * 100 : 0; };
const last = (b) => (b.series.length ? b.series[b.series.length - 1].total : b.deposits);

async function getJSON(path) {
  const r = await fetch(`data/${path}?t=${Date.now()}`);
  if (!r.ok) throw new Error(`${path} : ${r.status}`);
  return r.json();
}

// --- Chargement --------------------------------------------------------------
async function load() {
  $("#subtitle").textContent = "Chargement…";
  try {
    const [summary, orders, index, pms] = await Promise.all([
      getJSON("summary.json"), getJSON("orders.json"), getJSON("journal/index.json"), getJSON("postmortems.json"),
    ]);
    Object.assign(state, { summary, orders, index, pms });
    state.report = await getJSON("report.json").catch(() => null);
    render();
  } catch (e) {
    $("#subtitle").textContent = "Pas encore de données (" + e.message + ")";
  }
}

function render() {
  const s = state.summary, h = s.horizon;
  $("#subtitle").textContent = h
    ? `Séance ${h.session_no}/${s.total_sessions} · au ${fmtDate(s.as_of)} · fin le ${fmtDate(h.end)}`
    : "La simulation n'a pas encore démarré";
  renderKpis(); renderChart(); renderPositions(); renderOrders(); renderJournal(); renderLessons(); renderReport();
}

function renderKpis() {
  const b = state.summary.books, a = b.agents;
  const gap = (k) => ret(a) - ret(b[k]);
  const cards = [
    ["Équipe d'agents", eur(last(a)), `<span class="${cls(ret(a))}">${pct(ret(a))}</span>`],
    ["vs ETF Monde", `<span class="${cls(gap("benchmark"))}">${pct(gap("benchmark"))}</span>`, `ETF ${pct(ret(b.benchmark))}`],
    ["vs Aléatoire", `<span class="${cls(gap("random"))}">${pct(gap("random"))}</span>`, `Aléatoire ${pct(ret(b.random))}`],
    ["Frais payés", eur(a.fees), `low-cost : ${eur(b.agents_lowcost.fees)}`],
  ];
  $("#kpis").innerHTML = cards.map(([l, v, s]) =>
    `<div class="kpi"><div class="label">${l}</div><div class="value">${v}</div><div class="sub muted">${s}</div></div>`).join("");
}

// --- Graphique (SVG maison, sans dépendance) ---------------------------------
function renderChart() {
  const books = state.summary.books;
  const series = ORDER.filter((k) => books[k]?.series.length).map((k) => ({
    key: k, pts: books[k].series.map((p) => ({ d: p.date, v: (p.total / books[k].deposits - 1) * 100 })),
  }));
  const dates = [...new Set(series.flatMap((s) => s.pts.map((p) => p.d)))].sort();
  if (dates.length < 2) { $("#chart").innerHTML = `<p class="empty">Le graphique apparaîtra après la 2ᵉ séance.</p>`; $("#legend").innerHTML = ""; return; }

  const W = Math.max(280, $("#chart").clientWidth), H = 220, L = 38, R = 8, T = 10, B = 22;
  const vals = series.flatMap((s) => s.pts.map((p) => p.v)).concat(0);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.12, 0.5); lo -= pad; hi += pad;
  const x = (d) => L + (dates.indexOf(d) / (dates.length - 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const step = niceStep((hi - lo) / 4);
  let grid = "";
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    grid += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="${css("--line")}" ${Math.abs(v) < 1e-9 ? 'stroke-width="1.5"' : ""}/>`;
    grid += `<text x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${v.toFixed(step < 1 ? 1 : 0)}%</text>`;
  }
  const ticks = [dates[0], dates[Math.floor(dates.length / 2)], dates[dates.length - 1]];
  grid += ticks.map((d, i) => `<text x="${x(d)}" y="${H - 6}" text-anchor="${["start", "middle", "end"][i]}">${fmtDate(d)}</text>`).join("");
  const lines = series.slice().reverse().map((s) => {
    const path = s.pts.map((p, i) => `${i ? "L" : "M"}${x(p.d).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    return `<path d="${path}" fill="none" stroke="${css("--s-" + s.key)}" stroke-width="${s.key === "agents" ? 2.6 : 1.6}" stroke-linejoin="round" stroke-linecap="round"/>`;
  }).join("");
  $("#chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Performance des portefeuilles">${grid}${lines}</svg>`;
  $("#legend").innerHTML = series.map((s) =>
    `<span style="--c:${css("--s-" + s.key)}">${SHORT[s.key]} <b class="${cls(s.pts.at(-1).v)}">${pct(s.pts.at(-1).v)}</b></span>`).join("");
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / p;
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * p;
}

// --- Onglets -----------------------------------------------------------------
function renderPositions() {
  const s = state.summary, a = s.books.agents;
  let html = `<div class="card"><div class="card-head"><h2>Positions de l'équipe</h2><span class="muted small">cash ${eur(a.cash)}</span></div>`;
  html += s.positions.length ? `<div class="table-wrap"><table>
    <tr><th>Titre</th><th class="num">Qté</th><th class="num">PRU</th><th class="num">Cours</th><th class="num">Poids</th><th class="num">Latent</th></tr>
    ${s.positions.map((p) => `<tr><td><b>${esc(p.ticker)}</b><br><span class="muted small">${esc(p.name)}</span></td>
      <td class="num">${p.qty}</td><td class="num">${eur(p.pru)}</td><td class="num">${eur(p.price)}</td>
      <td class="num">${pct(p.weight, false)}</td><td class="num ${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</td></tr>`).join("")}
  </table></div>` : `<p class="empty">Aucune position, 100 % cash.</p>`;
  html += `<p class="muted small">P&L réalisé ${eur(a.realized)} · dividendes ${eur(a.dividends)} · frais ${eur(a.fees)}</p></div>`;

  const p = s.pending;
  html += `<div class="card"><div class="card-head"><h2>Ordres pour la prochaine ouverture</h2>${p ? `<span class="muted small">${fmtDate(p.exec_date)}</span>` : ""}</div>`;
  html += p?.orders?.length ? p.orders.map((o) => `<div class="item"><div class="item-head"><div>${sideChip(o.side)} <b>${o.qty} ${esc(o.ticker)}</b></div>
      <span class="muted small">≈ ${eur(o.est_price, 4)}</span></div>
      <details><summary>Thèse et plan de sortie</summary><p>${esc(o.thesis)}</p><p class="muted">Sortie : ${esc(o.exit_plan)}</p>
      ${o.risk_note ? `<p class="muted">Risque : ${esc(o.risk_note)}</p>` : ""}</details></div>`).join("")
    : `<p class="empty">Aucun ordre en attente.</p>`;
  html += `</div>`;

  html += `<div class="card"><h2>Les autres portefeuilles</h2><div class="table-wrap"><table>
    <tr><th>Livre</th><th class="num">Valeur</th><th class="num">Perf.</th><th class="num">Frais</th><th>Titres</th></tr>
    ${ORDER.map((k) => { const b = s.books[k]; return `<tr><td>${esc(b.label)}</td><td class="num">${eur(last(b))}</td>
      <td class="num ${cls(ret(b))}">${pct(ret(b))}</td><td class="num">${eur(b.fees)}</td><td class="small">${esc(b.positions.join(", ") || "–")}</td></tr>`; }).join("")}
  </table></div></div>`;
  $("#tab-positions").innerHTML = html;
}

const sideChip = (side) => `<span class="chip ${side === "BUY" ? "buy" : "sell"}">${side === "BUY" ? "Achat" : "Vente"}</span>`;

function renderOrders() {
  const { fills, rejections } = state.orders;
  let html = `<div class="card"><h2>Exécutions de l'équipe</h2>`;
  html += fills.length ? fills.slice().reverse().map((f) => `<div class="item"><div class="item-head">
      <div>${sideChip(f.side)} <b>${f.qty} ${esc(f.ticker)}</b> à ${eur(f.price, 4)}</div><span class="muted small">${fmtDate(f.date)}</span></div>
      <div class="muted small">montant ${eur(f.amount)} · frais ${eur(f.fees)} · ouverture ${eur(f.ref_price, 4)}</div>
      ${f.thesis ? `<details><summary>Thèse</summary><p>${esc(f.thesis)}</p><p class="muted">Sortie : ${esc(f.exit_plan)}</p></details>` : ""}
    </div>`).join("") : `<p class="empty">Aucune exécution pour l'instant.</p>`;
  html += `</div><div class="card"><h2>Ordres refusés ou bloqués</h2>`;
  const stage = { decision: "contrôle (décision)", execution: "contrôle (exécution)", risk: "gestionnaire de risque" };
  html += rejections.length ? rejections.slice().reverse().map((r) => `<div class="item"><div class="item-head">
      <div>${sideChip(r.side)} <b>${r.qty ?? ""} ${esc(r.ticker)}</b></div><span class="muted small">${fmtDate(r.date)}</span></div>
      <div class="small"><span class="chip warn">${stage[r.stage] || r.stage}</span>${esc(r.reason)}</div></div>`).join("")
    : `<p class="empty">Aucun refus.</p>`;
  $("#tab-orders").innerHTML = html + `</div>`;
}

function renderJournal() {
  const items = state.index;
  $("#tab-journal").innerHTML = `<div class="card"><h2>Journal</h2>${items.length ? items.map((e) => `
    <div class="item"><button class="link" data-file="${esc(e.file)}">
      <div class="item-head"><b>${fmtDate(e.date)}${e.mode === "veille" ? " · veille" : ""}${e.final ? " · fin" : ""}</b>
      <span class="small muted">${e.mode === "trading" ? `${e.n_exec} exéc. · ${e.n_orders} ordres` : ""}${e.n_errors ? ` · <span class="neg">${e.n_errors} erreur(s)</span>` : ""}</span></div>
      <div class="muted small">${esc(e.view)}…</div></button></div>`).join("") : `<p class="empty">Journal vide.</p>`}</div>`;
  document.querySelectorAll("#tab-journal [data-file]").forEach((b) => b.addEventListener("click", () => openDay(b.dataset.file)));
}

async function openDay(file) {
  const e = await getJSON("journal/" + file);
  $("#day-title").textContent = `${fmtDate(e.date)}${e.mode === "veille" ? " · veille" : ""}`;
  const parts = [];
  if (e.portfolio?.total) parts.push(`<p>Portefeuille à la clôture : <b>${eur(e.portfolio.total)}</b> <span class="${cls(Number(e.portfolio.return_pct))}">(${pct(Number(e.portfolio.return_pct))})</span>${e.final ? " — dernier jour de la simulation" : ""}</p>`);
  if (e.errors?.length) parts.push(`<h3 class="neg">Erreurs</h3>${e.errors.map((x) => `<p class="small">${esc(x)}</p>`).join("")}`);
  if (e.executions?.length) parts.push(`<h3>Exécutions à l'ouverture</h3>${e.executions.map((x) =>
    `<p class="small">${sideChip(x.side)} ${esc(x.ticker)} : ${x.filled ? `${x.qty} à ${eur(x.price, 4)} (frais ${eur(x.fees)})` : `<span class="neg">non exécuté : ${esc(x.reason)}</span>`}${x.filled && x.reason ? ` <span class="muted">(${esc(x.reason)})</span>` : ""}</p>`).join("")}`);
  if (e.chef) parts.push(`<h3>Vue du chef</h3><p>${esc(e.chef.market_view)}</p>${e.chef.no_trade_reason ? `<p class="muted">Pas d'ordre : ${esc(e.chef.no_trade_reason)}</p>` : ""}`);
  if (e.final_orders?.length) parts.push(`<h3>Ordres retenus</h3>${e.final_orders.map((o) => `<div class="item">${sideChip(o.side)} <b>${o.qty} ${esc(o.ticker)}</b>
    <p class="small">${esc(o.thesis)}</p><p class="small muted">Sortie : ${esc(o.exit_plan)}${o.sources?.length ? ` · sources ${esc(o.sources.join(", "))}` : ""}</p></div>`).join("")}`);
  if (e.refused?.length) parts.push(`<h3>Refusés</h3>${e.refused.map((o) => `<p class="small">${sideChip(o.side)} ${esc(o.ticker)} — <span class="chip warn">${esc(o.by)}</span>${esc(o.reason)}</p>`).join("")}`);
  if (e.risk?.comment) parts.push(`<h3>Gestionnaire de risque</h3><p class="small">${esc(e.risk.comment)}</p>`);
  const m = e.market_analysis;
  if (m) parts.push(`<h3>Analyste marché</h3><p class="small">${esc(m.summary)}</p>${m.opportunities.map((o) => `<p class="small">• <b>${esc(o.ticker)}</b> : ${esc(o.thesis)} <span class="muted">(risques : ${esc(o.risks)})</span></p>`).join("")}`);
  const n = e.news_analysis;
  if (n) parts.push(`<h3>Analyste actualité</h3><p class="small">${esc(n.summary)}</p>${n.by_company.map((c) => `<p class="small">• <b>${esc(c.ticker || c.company)}</b> <span class="chip">${esc(c.impact)}</span>${esc(c.why)}</p>`).join("")}`);
  if (e.news?.length) parts.push(`<details><summary>${e.news.length} actualités lues</summary>${e.news.map((i) => `<p class="small">[${esc(i.id)}] <a href="${esc(i.link)}" target="_blank" rel="noopener">${esc(i.title)}</a> <span class="muted">${esc(i.source)}</span></p>`).join("")}</details>`);
  if (e.llm_calls?.length) parts.push(`<p class="muted small">Appels LLM : ${e.llm_calls.map((c) => `${esc(c.role)} (${esc(c.model)}, ${c.duration_s}s)`).join(", ")}</p>`);
  $("#day-body").innerHTML = parts.join("") || `<p class="empty">Rien à afficher.</p>`;
  $("#day-dialog").showModal();
}

function renderLessons() {
  const pms = state.pms.slice().reverse();
  $("#tab-lessons").innerHTML = `<div class="card"><h2>Post-mortems</h2>${pms.length ? pms.map((p) => `<div class="item">
    <div class="item-head"><b>${esc(p.ticker)}</b><span class="${cls(Number(p.pnl))}">${eur(p.pnl)} (${pct(Number(p.pnl_pct))})</span></div>
    <div class="muted small">${fmtDate(p.open_date)} → ${fmtDate(p.close_date)} · ETF sur la période ${p.benchmark_pct == null ? "–" : pct(Number(p.benchmark_pct))}</div>
    <p class="small"><b>Leçon :</b> ${esc(p.lesson)}</p>
    <details><summary>Prévu / constaté</summary><p class="small">${esc(p.expected)}</p><p class="small">${esc(p.happened)}</p></details></div>`).join("")
    : `<p class="empty">Aucune position clôturée pour l'instant.</p>`}</div>`;
}

function renderReport() {
  const r = state.report;
  if (!r) { $("#tab-report").innerHTML = `<div class="card"><p class="empty">Le rapport final sera publié à la fin de la simulation.</p></div>`; return; }
  const s = r.stats, a = r.analysis;
  const list = (xs) => xs.map((x) => `<li>${esc(x)}</li>`).join("");
  const trades = (ts) => ts.map((t) => `<li>${esc(t.ticker)} (${esc(t.open_date)} → ${esc(t.close_date)}) : <span class="${cls(Number(t.pnl))}">${eur(t.pnl)} (${pct(Number(t.pnl_pct))})</span></li>`).join("");
  $("#tab-report").innerHTML = `<div class="card"><h2>Rapport final</h2>
    <div class="table-wrap"><table><tr><th>Livre</th><th class="num">Valeur</th><th class="num">Perf.</th><th class="num">Frais</th></tr>
    ${Object.values(s.books).map((b) => `<tr><td>${esc(b.label)}</td><td class="num">${eur(b.total)}</td><td class="num ${cls(Number(b.return_pct))}">${pct(Number(b.return_pct))}</td><td class="num">${eur(b.fees)}</td></tr>`).join("")}</table></div>
    <h3>Meilleurs trades</h3><ul class="small">${trades(s.best_trades) || "<li>–</li>"}</ul>
    <h3>Pires trades</h3><ul class="small">${trades(s.worst_trades) || "<li>–</li>"}</ul>
    ${a ? `<h3>Verdict</h3><p>${esc(a.verdict)}</p><h3>Ce qui a fonctionné</h3><ul class="small">${list(a.what_worked)}</ul>
    <h3>Ce qui n'a pas fonctionné</h3><ul class="small">${list(a.what_failed)}</ul><h3>Frais</h3><p class="small">${esc(a.fees_impact)}</p>
    <h3>Fonctionnement de l'équipe</h3><p class="small">${esc(a.process)}</p><h3>Leçons</h3><ul class="small">${list(a.lessons)}</ul>`
    : `<p class="neg small">Analyse rédigée indisponible : ${esc(r.analysis_error)}</p>`}</div>`;
}

// --- Navigation --------------------------------------------------------------
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs button, .tab").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  $("#tab-" + b.dataset.tab).classList.add("active");
}));
$("#refresh").addEventListener("click", load);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.summary && renderChart());
let resizeTimer;
addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => state.summary && renderChart(), 150); });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
load();
