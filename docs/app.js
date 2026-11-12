"use strict";

const APP_VERSION = "1.3.0";        // à garder identique à VERSION dans sw.js
const SUPABASE = {
  url: "https://gwsomltfixhoikwkpqmd.supabase.co",
  key: "sb_publishable_ZtC6h8l38XYDa8WXo1-Zmw_tIrTIhmh",   // clé publique, lecture seule
};
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

// Données : table pea_docs de Supabase (une ligne par fichier JSON exporté par le run quotidien).
// Copie locale en secours hors connexion.
class NotFound extends Error {}

const cacheGet = (k) => { try { return JSON.parse(localStorage.getItem("pea:" + k)); } catch { return null; } };
const cacheSet = (k, v) => { try { localStorage.setItem("pea:" + k, JSON.stringify(v)); } catch {} };

// Mode démo, en local uniquement (http://localhost:…/?demo) : lit les fichiers JSON de docs/data/
const DEMO = location.hostname === "localhost" && new URLSearchParams(location.search).has("demo");

async function getJSON(path) {
  if (DEMO) {
    const r = await fetch(`data/${path}?t=${Date.now()}`);
    if (!r.ok) throw new NotFound(path);
    noteSync(new Date().toISOString(), false);
    return r.json();
  }
  try {
    const r = await fetch(`${SUPABASE.url}/rest/v1/pea_docs?select=data,updated_at&key=eq.${encodeURIComponent(path)}`,
      { headers: { apikey: SUPABASE.key }, cache: "no-store" });
    if (!r.ok) throw new Error(`Supabase ${r.status}`);
    const rows = await r.json();
    if (!rows.length) throw new NotFound(path);
    cacheSet(path, rows[0]);
    noteSync(rows[0].updated_at, false);
    return rows[0].data;
  } catch (e) {
    if (e instanceof NotFound) throw e;
    const cached = cacheGet(path);
    if (!cached) throw e;
    noteSync(cached.updated_at, true);
    return cached.data;
  }
}

function noteSync(updatedAt, offline) {
  if (!state.syncedAt || updatedAt > state.syncedAt) state.syncedAt = updatedAt;
  state.offline = state.offline || offline;
}

// --- Chargement --------------------------------------------------------------
async function load() {
  const btn = $("#refresh");
  btn.classList.add("spin");
  state.syncedAt = null; state.offline = false;
  try {
    const [summary, orders, index, pms] = await Promise.all([
      getJSON("summary.json"), getJSON("orders.json"), getJSON("journal/index.json"), getJSON("postmortems.json"),
    ]);
    Object.assign(state, { summary, orders, index, pms, loadedAt: new Date() });
    state.report = await getJSON("report.json").catch(() => null);
    state.knowledge = await getJSON("knowledge.json").catch(() => null);
    render();
  } catch (e) {
    $("#subtitle").textContent = "Données indisponibles (" + e.message + ")";
  } finally {
    btn.classList.remove("spin");
    renderFooter();
  }
}

function renderFooter() {
  const t = (d) => new Date(d).toLocaleString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const parts = [`PEA sim v${APP_VERSION}`];
  if (state.syncedAt) parts.push(`données du ${t(state.syncedAt)}`);
  if (state.loadedAt) parts.push(`vérifié à ${state.loadedAt.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`);
  $("#footer").innerHTML = esc(parts.join(" · ")) + (state.offline ? ` · <span class="neg">hors ligne (copie locale)</span>` : "");
}

function render() {
  const s = state.summary, h = s.horizon;
  $("#subtitle").textContent = h
    ? `Séance ${h.session_no}/${s.total_sessions} · au ${fmtDate(s.as_of)} · fin le ${fmtDate(h.end)}`
    : "La simulation n'a pas encore démarré";
  renderHero(); renderKpis(); renderChart(); renderDaily(); renderPositions(); renderOrders(); renderJournal(); renderLearning(); renderLessons(); renderReport();
}

// Valeur actuelle du portefeuille de l'équipe, en grand en haut de page
function renderHero() {
  const s = state.summary, a = s.books.agents, capital = a.deposits || s.capital || 1000;
  const pts = a.series;
  if (!pts.length) {
    $("#hero").innerHTML = `<div class="hero-label">Mon argent aujourd'hui</div>
      <div class="hero-value">${eur(capital)}</div>
      <div class="hero-sub muted">Capital de départ · la simulation démarre au premier run de 18h</div>`;
    return;
  }
  const now = pts.at(-1).total, prev = pts.length > 1 ? pts.at(-2).total : capital;
  const gain = now - capital, day = now - prev;
  const invested = now - a.cash;
  const signed = (x) => (x > 0 ? "+" : x < 0 ? "−" : "") + eur(Math.abs(x));
  $("#hero").innerHTML = `<div class="hero-label">Mon argent au ${fmtDate(pts.at(-1).date)}</div>
    <div class="hero-value">${eur(now)}</div>
    <div class="hero-gain ${cls(gain)}">${signed(gain)} (${pct(gain / capital * 100)}) depuis le départ</div>
    <div class="hero-row">
      <span>Aujourd'hui <b class="${cls(day)}">${signed(day)} (${pct(day / prev * 100)})</b></span>
      <span>Cash <b>${eur(a.cash)}</b></span>
      <span>Investi <b>${eur(invested)}</b></span>
    </div>`;
}

// Valeur à chaque clôture, la plus récente en premier
function renderDaily() {
  const a = state.summary.books.agents, capital = a.deposits || state.summary.capital || 1000;
  const pts = a.series;
  if (!pts.length) { $("#daily").innerHTML = `<h2>Jour par jour</h2><p class="empty">Première valeur ce soir après le run.</p>`; return; }
  const rows = pts.map((p, i) => {
    const prev = i ? pts[i - 1].total : capital, d = p.total - prev;
    return `<tr><td class="nowrap">${fmtDate(p.date)}</td><td class="num"><b>${eur(p.total)}</b></td>
      <td class="num ${cls(d)}">${d > 0 ? "+" : ""}${eur(d)}</td><td class="num ${cls(d)}">${pct(d / prev * 100)}</td>
      <td class="num ${cls(p.total - capital)}">${pct((p.total / capital - 1) * 100)}</td></tr>`;
  }).reverse();
  const head = `<tr><th>Clôture</th><th class="num">Valeur</th><th class="num">Jour</th><th class="num">Jour %</th><th class="num">Total %</th></tr>`;
  const table = (r) => `<div class="table-wrap"><table>${head}${r.join("")}</table></div>`;
  $("#daily").innerHTML = `<div class="card-head"><h2>Jour par jour</h2><span class="muted small">départ ${eur(capital)}</span></div>
    ${table(rows.slice(0, 7))}${rows.length > 7 ? `<details><summary>Voir les ${rows.length - 7} jours précédents</summary>${table(rows.slice(7))}</details>` : ""}`;
}

function renderKpis() {
  const s = state.summary, b = s.books, a = b.agents, h = s.horizon;
  const gap = (k) => ret(a) - ret(b[k]);
  const cards = [
    ["Séance", h ? `${h.session_no} / ${s.total_sessions}` : "–", h ? `fin le ${fmtDate(h.end)}` : "départ ce soir"],
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
      <span class="small muted">${e.mode === "trading" ? `${e.n_exec} exéc. · ${e.n_orders} ordres` : ""}${e.n_rule_changes ? ` · ${e.n_rule_changes} règle(s)` : ""}${e.n_errors ? ` · <span class="neg">${e.n_errors} erreur(s)</span>` : ""}</span></div>
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
  const L = e.learning;
  if (L) {
    const lp = [];
    if (e.daily_review?.review) lp.push(`<p class="small"><b>Revue de la veille</b> : ${esc(e.daily_review.review)}</p>`);
    if (e.weekly_review?.summary) lp.push(`<p class="small"><b>Revue hebdomadaire</b> : ${esc(e.weekly_review.summary)}</p>
      ${e.weekly_review.biases?.length ? `<p class="small muted">Biais : ${esc(e.weekly_review.biases.join(" ; "))}</p>` : ""}`);
    if (L.playbook_changes?.length) lp.push(`<p class="small"><b>Règles</b></p>${L.playbook_changes.map((c) => `<p class="small">• <b>${esc(c.rule || "–")}</b> ${esc(c.op)}${c.confidence ? " " + confChip(c.confidence) : ""} : ${esc(c.text)} <span class="muted">(${esc(c.by)})</span></p>`).join("")}`);
    if (L.predictions_resolved?.length) lp.push(`<p class="small"><b>Prédictions évaluées</b></p>${L.predictions_resolved.map((p) => `<p class="small">• ${esc(p.id)} ${esc(p.ticker)} : <b class="${p.hit ? "pos" : "neg"}">${p.hit ? "réussie" : "ratée"}</b> (${pct(p.return_pct)})</p>`).join("")}`);
    if (L.predictions_added?.length) lp.push(`<p class="small"><b>Nouvelles prédictions</b> : ${L.predictions_added.map((p) => `${esc(p.ticker)} ${dirTxt(p)}`).join(" · ")}</p>`);
    const misc = [L.dossiers_built?.length && `dossiers 6 mois : ${L.dossiers_built.join(", ")}`, L.notes_updated?.length && `notes mises à jour : ${L.notes_updated.join(", ")}`,
      L.insights_added && `${L.insights_added} connaissance(s) ajoutée(s)`, L.agenda_added && `${L.agenda_added} événement(s) à l'agenda`].filter(Boolean);
    if (misc.length) lp.push(`<p class="small muted">${esc(misc.join(" · "))}</p>`);
    if (lp.length) parts.push(`<h3>Apprentissage du jour</h3>${lp.join("")}`);
  }
  if (e.final_orders?.length) parts.push(`<h3>Ordres retenus</h3>${e.final_orders.map((o) => `<div class="item">${sideChip(o.side)} <b>${o.qty} ${esc(o.ticker)}</b>
    <p class="small">${esc(o.thesis)}</p>
    ${(o.catalysts || []).map((c) => `<p class="small">📰 <b>${esc(c.title)}</b> <span class="muted">(${esc(c.date)}, ${esc(c.source_id)})</span> → ${esc(c.link)}</p>`).join("")}
    ${o.prediction ? `<p class="small">Prédiction : ${dirTxt(o.prediction)}${o.rules_applied?.length ? ` · règles ${esc(o.rules_applied.join(", "))}` : ""}</p>` : ""}
    <p class="small muted">Sortie : ${esc(o.exit_plan)}</p></div>`).join("")}`);
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

// --- Apprentissage : playbook, calibration, agenda, connaissances ---------------
const CONF_CLS = { "confirmée": "pos", "probable": "", "hypothèse": "muted", "contestée": "neg" };
const confChip = (c) => `<span class="chip ${CONF_CLS[c] || ""}">${esc(c)}</span>`;
const dirTxt = (p) => `${p.direction === "up" ? "hausse" : "baisse"} ≥ ${p.move_pct} % en ${p.horizon_sessions} séance(s), proba ${Math.round(p.probability * 100)} %`;

function renderLearning() {
  const k = state.knowledge;
  if (!k) { $("#tab-learning").innerHTML = `<div class="card"><p class="empty">La base de connaissances apparaîtra après le premier run.</p></div>`; return; }
  const cards = [];

  const c = k.calibration;
  cards.push(`<div class="card"><h2>Calibration des prédictions</h2>${c.n ? `
    <p>${c.n} prédictions évaluées · probabilité annoncée <b>${c.announced} %</b> · réussite réelle
    <b class="${c.observed >= c.announced - 10 ? "pos" : "neg"}">${c.observed} %</b> · Brier ${c.brier} <span class="muted small">(0 = parfait, 0,25 = hasard)</span></p>
    <div class="table-wrap"><table><tr><th>Confiance</th><th class="num">Nb</th><th class="num">Annoncé</th><th class="num">Observé</th></tr>
    ${c.buckets.map((b) => `<tr><td>${esc(b.range)}</td><td class="num">${b.n}</td><td class="num">${b.announced} %</td><td class="num">${b.observed} %</td></tr>`).join("")}</table></div>`
    : `<p class="empty">Aucune prédiction arrivée à échéance pour l'instant.</p>`}</div>`);

  const active = k.playbook.filter((r) => r.status === "active");
  const order = { "confirmée": 0, "probable": 1, "hypothèse": 2, "contestée": 3 };
  const ruleHtml = (r) => {
    const s = r.evidence.filter((e) => e.kind === "support").length, x = r.evidence.length - s;
    const eff = r.effect ? ` · appliquée ${r.effect.n}× : ${r.effect.hits}/${r.effect.n} réussies, ${pct(r.effect.avg_return)} en moyenne` : "";
    return `<div class="item"><div class="item-head"><div><b>${esc(r.id)}</b> ${confChip(r.confidence)}</div>
      <span class="muted small">${s} pour / ${x} contre · depuis ${fmtDate(r.created)}</span></div>
      <p class="small">${esc(r.text)}</p><div class="muted small">portée ${esc(r.scope)}${eff}</div>
      <details><summary>Historique et cas</summary>${r.history.map((h) => `<p class="small">${fmtDate(h.date)} · ${esc(h.by)} : ${esc(h.change)}${h.reason ? ` — <span class="muted">${esc(h.reason)}</span>` : ""}</p>`).join("")}
      ${r.evidence.map((e) => `<p class="small muted">${e.kind === "support" ? "✓" : "✗"} ${fmtDate(e.date)} ${esc(e.origin)} ${esc(e.note)}</p>`).join("")}</details></div>`;
  };
  const retired = k.playbook.filter((r) => r.status !== "active");
  cards.push(`<div class="card"><div class="card-head"><h2>Playbook (règles apprises)</h2><span class="muted small">${active.length} active(s)</span></div>
    ${active.length ? active.sort((a, b) => order[a.confidence] - order[b.confidence]).map(ruleHtml).join("") : `<p class="empty">Aucune règle pour l'instant.</p>`}
    ${retired.length ? `<details><summary>${retired.length} règle(s) retirée(s)</summary>${retired.map(ruleHtml).join("")}</details>` : ""}</div>`);

  const log = k.log.slice().reverse().slice(0, 40);
  cards.push(`<div class="card"><h2>Évolution des règles</h2>${log.length ? log.map((l) => `<div class="item small">
    <div class="item-head"><span><b>${esc(l.rule || "–")}</b> ${esc(l.op)}${l.confidence ? " " + confChip(l.confidence) : ""}</span><span class="muted">${fmtDate(l.date)} · ${esc(l.by)}</span></div>
    <div>${esc(l.text)}</div>${l.reason ? `<div class="muted">${esc(l.reason)}</div>` : ""}</div>`).join("") : `<p class="empty">Aucun changement pour l'instant.</p>`}</div>`);

  const preds = k.predictions.slice().reverse();
  const open = preds.filter((p) => p.status === "open"), done = preds.filter((p) => p.status === "resolved").slice(0, 20);
  const predHtml = (p) => `<div class="item small"><div class="item-head"><span><b>${esc(p.id)} ${esc(p.ticker)}</b> <span class="muted">${esc(p.kind)}</span></span>
    <span>${p.status === "resolved" ? `<b class="${p.hit ? "pos" : "neg"}">${p.hit ? "✓ réussie" : "✗ ratée"}</b> ${pct(p.return_pct)}` : `échéance ${fmtDate(p.target_date)}`}</span></div>
    <div>${dirTxt(p)}${p.rules?.length ? ` · règles ${esc(p.rules.join(", "))}` : ""}</div><div class="muted">${esc(p.thesis).slice(0, 220)}</div></div>`;
  cards.push(`<div class="card"><h2>Registre des prédictions</h2>
    <h3>En cours (${open.length})</h3>${open.map(predHtml).join("") || `<p class="empty">Aucune.</p>`}
    <h3>Évaluées</h3>${done.map(predHtml).join("") || `<p class="empty">Aucune.</p>`}</div>`);

  cards.push(`<div class="card"><h2>Agenda et scénarios</h2>${k.agenda.length ? k.agenda.slice(0, 30).map((e) => `<div class="item small">
    <div class="item-head"><b>${e.date ? fmtDate(e.date) : "date à préciser"}</b><span class="muted">${esc((e.tickers || []).join(", "))}</span></div>
    <div>${esc(e.event)}</div>${e.impact ? `<div class="muted">${esc(e.impact)}</div>` : ""}
    ${(e.scenarios || []).map((s) => `<div>→ si ${esc(s.if)} : ${esc(s.then)}</div>`).join("")}</div>`).join("") : `<p class="empty">Agenda vide.</p>`}</div>`);

  const ins = k.insights.filter((i) => i.status === "active").reverse();
  cards.push(`<div class="card"><h2>Connaissances accumulées</h2>${ins.length ? ins.map((i) => `<p class="small"><span class="chip">${esc(i.kind)}</span><b>${esc(i.subject)}</b> : ${esc(i.text)} <span class="muted">(${fmtDate(i.date)}, ${esc(i.by)})</span></p>`).join("") : `<p class="empty">Aucune pour l'instant.</p>`}</div>`);

  const notes = Object.entries(k.notes).sort((a, b) => (b[1].updated || "").localeCompare(a[1].updated || ""));
  cards.push(`<div class="card"><h2>Dossiers et notes</h2>${notes.length ? notes.map(([key, n]) => `<details><summary>${esc(key)}${n.sector ? ` · ${esc(n.sector)}` : ""} <span class="muted">(${n.updated ? fmtDate(n.updated) : "–"})</span></summary>
    ${n.history_6m ? `<p class="small"><b>Historique 6 mois</b><br>${esc(n.history_6m).replace(/\n/g, "<br>")}</p>` : ""}
    ${n.outlook ? `<p class="small"><b>Perspectives</b> : ${esc(n.outlook)}</p>` : ""}
    ${n.text ? `<p class="small"><b>Note courante</b> : ${esc(n.text)}</p>` : ""}</details>`).join("") : `<p class="empty">Aucun dossier pour l'instant.</p>`}</div>`);

  $("#tab-learning").innerHTML = cards.join("");
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
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.summary && renderChart());
let resizeTimer;
addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => state.summary && renderChart(), 150); });
// Données rafraîchies quand l'app revient au premier plan (après 2 min)
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && (!state.loadedAt || Date.now() - state.loadedAt > 120000)) load();
});

// --- Mises à jour de l'app (service worker) -----------------------------------
let swReg;
$("#refresh").addEventListener("click", () => { swReg?.update().catch(() => {}); load(); });
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").then((reg) => {
    swReg = reg;
    if (reg.waiting && navigator.serviceWorker.controller) offerUpdate(reg.waiting);
    reg.addEventListener("updatefound", () => {
      const w = reg.installing;
      w?.addEventListener("statechange", () => {
        if (w.state === "installed" && navigator.serviceWorker.controller) offerUpdate(w);
      });
    });
  }).catch(() => {});
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (!reloading) { reloading = true; location.reload(); } });
}

function offerUpdate(worker) {
  const bar = $("#update");
  bar.hidden = false;
  bar.querySelector("button").onclick = () => worker.postMessage("skipWaiting");
}

load();
