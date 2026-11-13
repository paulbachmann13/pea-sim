"use strict";
// Organigramme illustré des agents : un personnage par agent (robots pour les modules de code), rangés selon leur rôle réel.
// Utilise les fonctions de app.js ($, esc, state, fmtDate) ; chargé avant app.js, appelé après.

const SKIN = ["#f3d1b0", "#e3b48a", "#c68642", "#8d5524"];
const GLASSES = `<g fill="none" stroke="#111" stroke-width="1"><circle cx="28" cy="27" r="3.7"/><circle cx="36" cy="27" r="3.7"/><path d="M31.7 27h.6"/></g>`;
const hairShort = (c) => `<path d="M21 26c0-9 5-13 11-13s11 4 11 13c-3-3-7-5-11-5s-8 2-11 5z" fill="${c}"/>`;
const hairLongBack = (c) => `<path d="M20 31c-1-12 4-18 12-18s13 6 12 18v9h-5V28H25v12h-5z" fill="${c}"/>`;
const hairBun = (c) => `<circle cx="32" cy="12" r="4.6" fill="${c}"/>` + hairShort(c);

// Équipes : couleur de bandeau
const TEAMS = {
  direction: { label: "Direction", sub: "décide les ordres", color: "#f59e0b" },
  scouts: { label: "Éclaireurs", sub: "informent le chef", color: "#3b82f6" },
  guards: { label: "Garde-fous", sub: "contrôlent ses ordres", color: "#ef4444" },
  memory: { label: "Mémoire", sub: "apprennent chaque jour", color: "#8b5cf6" },
  backoffice: { label: "Back-office", sub: "du code, pas d'IA", color: "#64748b" },
};

const PERSONA = {
  chef: { name: "Victor", title: "Le Chef · gérant", team: "direction", bg: "#fde68a", suit: "#1e293b", skin: SKIN[1],
    front: hairShort("#3b2a20"), acc: `<path d="M25 15l1.5-9 4.5 5 1-7 1 7 4.5-5 1.5 9z" fill="#f59e0b" stroke="#b45309" stroke-width=".8"/>`,
    collar: `<path d="M26 42l6 8 6-8z" fill="#fff"/><path d="M31 44h2l1.5 13h-5z" fill="#dc2626"/>` },
  plan: { name: "Victor", title: "Le Stratège du dimanche", team: "direction", bg: "#fde68a", suit: "#1e293b", skin: SKIN[1],
    front: hairShort("#3b2a20"), acc: `<path d="M25 15l1.5-9 4.5 5 1-7 1 7 4.5-5 1.5 9z" fill="#f59e0b" stroke="#b45309" stroke-width=".8"/><rect x="39" y="44" width="19" height="16" rx="2" fill="#fff" stroke="#b45309"/><path d="M39 49h19" stroke="#b45309"/><path d="M44 46v-3M53 46v-3" stroke="#b45309" stroke-width="2"/><path d="M43 53h3M49 53h3M43 56h3" stroke="#64748b" stroke-width="1.6"/>`,
    collar: `<path d="M26 42l6 8 6-8z" fill="#fff"/><path d="M31 44h2l1.5 13h-5z" fill="#dc2626"/>` },
  news: { name: "Julien", title: "Le Journaliste", team: "scouts", bg: "#bfdbfe", suit: "#1d4ed8", skin: SKIN[0],
    front: hairShort("#5b3a29") + GLASSES,
    acc: `<rect x="39" y="44" width="19" height="15" rx="1.5" fill="#fff" stroke="#1e3a8a"/><path d="M42 48h13M42 51h13M42 54h8" stroke="#64748b" stroke-width="1.2"/>` },
  market: { name: "Inès", title: "L'Analyste des marchés", team: "scouts", bg: "#c7d2fe", suit: "#4338ca", skin: SKIN[2],
    back: hairLongBack("#1f1f2e"), front: hairShort("#1f1f2e"),
    acc: `<rect x="39" y="44" width="19" height="15" rx="2" fill="#fff" stroke="#3730a3"/><path d="M42 56l5-6 4 3 5-7" stroke="#16a34a" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>` },
  research: { name: "Léo", title: "L'Explorateur du web", team: "scouts", bg: "#bbf7d0", suit: "#15803d", skin: SKIN[3],
    front: `<ellipse cx="32" cy="19" rx="17" ry="3.2" fill="#b45309"/><path d="M23 19c0-8 4-11 9-11s9 3 9 11z" fill="#d97706"/>`,
    acc: `<circle cx="47" cy="48" r="6" fill="#e0f2fe" fill-opacity=".85" stroke="#334155" stroke-width="2"/><path d="M51.5 52.5L58 59" stroke="#334155" stroke-width="2.8" stroke-linecap="round"/>` },
  dossier: { name: "Camille", title: "L'Archiviste", team: "scouts", bg: "#a7f3d0", suit: "#0f766e", skin: SKIN[0],
    front: hairBun("#6b3f2a") + GLASSES,
    acc: `<path d="M38 47h8l2 3h10v11H38z" fill="#fbbf24" stroke="#b45309"/><path d="M41 55h13" stroke="#b45309" stroke-width="1.2"/>` },
  risk: { name: "Rachel", title: "La Gardienne du risque", team: "guards", bg: "#fecaca", suit: "#7f1d1d", skin: SKIN[1],
    back: hairLongBack("#5b3a29"), front: hairShort("#5b3a29"),
    acc: `<path d="M42 41h15v9c0 6-7.5 9.5-7.5 9.5S42 56 42 50z" fill="#fff" stroke="#991b1b" stroke-width="1.6"/><path d="M49.5 44v12M45 48h9" stroke="#991b1b" stroke-width="1.6"/>` },
  review: { name: "Sofia", title: "La Coach du soir", team: "memory", bg: "#ddd6fe", suit: "#6d28d9", skin: SKIN[2],
    front: hairShort("#92400e") + `<path d="M44 22q9 2 6 13" stroke="#92400e" stroke-width="3.4" fill="none" stroke-linecap="round"/>`,
    acc: `<rect x="40" y="45" width="16" height="15" rx="2" fill="#fff" stroke="#5b21b6"/><rect x="45" y="43" width="6" height="4" rx="1" fill="#a78bfa"/><path d="M43 52h10M43 56h7" stroke="#64748b"/>` },
  postmortem: { name: "Dr Marc", title: "Le Médecin légiste", team: "memory", bg: "#e9d5ff", suit: "#f1f5f9", skin: SKIN[3],
    front: hairShort("#9ca3af") + `<ellipse cx="32" cy="16.5" rx="5" ry="3" fill="#cbd5e1" stroke="#64748b"/>`,
    acc: `<path d="M23 43q-3 11 8 11" stroke="#475569" stroke-width="2" fill="none"/><circle cx="33" cy="55" r="2.3" fill="#475569"/><path d="M47 47v9M42.5 51.5h9" stroke="#dc2626" stroke-width="2.4"/>` },
  weekly: { name: "Hélène", title: "La Sage du week-end", team: "memory", bg: "#fbcfe8", suit: "#9d174d", skin: SKIN[0],
    front: hairBun("#cbd5e1") + GLASSES,
    acc: `<rect x="39" y="46" width="19" height="14" rx="1.5" fill="#fde68a" stroke="#92400e"/><path d="M48.5 46v14M42 51h4M51 51h4" stroke="#92400e" stroke-width="1.2"/>` },
  report: { name: "Nina", title: "La Rapporteuse du mois", team: "memory", bg: "#fed7aa", suit: "#c2410c", skin: SKIN[1],
    front: hairShort("#b91c1c"),
    acc: `<rect x="40" y="43" width="14" height="17" rx="1.5" fill="#fff" stroke="#9a3412"/><path d="M43 48h8M43 52h8M43 56h5" stroke="#64748b"/><path d="M52 60l7-9" stroke="#1e293b" stroke-width="2.4" stroke-linecap="round"/>` },
  screener: { name: "Radar", title: "Le Screener · robot", team: "backoffice", bg: "#e2e8f0", suit: "#0f766e", led: "#22d3ee", glyph: "◎" },
  ledger: { name: "Compta-3000", title: "La Comptabilité · robot", team: "backoffice", bg: "#e2e8f0", suit: "#475569", led: "#4ade80", glyph: "€" },
  validator: { name: "Le Douanier", title: "Validation des ordres · robot", team: "guards", bg: "#fee2e2", suit: "#b91c1c", led: "#fbbf24", glyph: "✓" },
};

function avatar(key) {
  const p = PERSONA[key];
  if (p.led) {                                              // robot : tête carrée, antenne, pas d'IA
    return `<svg viewBox="0 0 64 64" class="av" aria-hidden="true"><defs><clipPath id="c-${key}"><circle cx="32" cy="32" r="31"/></clipPath></defs>
      <g clip-path="url(#c-${key})"><circle cx="32" cy="32" r="31" fill="${p.bg}"/>
      <rect x="15" y="40" width="34" height="24" rx="6" fill="${p.suit}"/>
      <text x="32" y="56" text-anchor="middle" font-size="13" font-weight="700" fill="#fff" font-family="system-ui,sans-serif">${p.glyph}</text>
      <path d="M32 15v-6" stroke="#475569" stroke-width="2"/><circle cx="32" cy="8" r="2.6" fill="${p.led}"/>
      <rect x="18" y="14" width="28" height="23" rx="6" fill="#cbd5e1" stroke="#475569" stroke-width="1.4"/>
      <circle cx="26.5" cy="24" r="3.4" fill="${p.led}"/><circle cx="37.5" cy="24" r="3.4" fill="${p.led}"/>
      <path d="M25 31h14" stroke="#475569" stroke-width="2" stroke-linecap="round" stroke-dasharray="2 2"/></g></svg>`;
  }
  return `<svg viewBox="0 0 64 64" class="av" aria-hidden="true"><defs><clipPath id="c-${key}"><circle cx="32" cy="32" r="31"/></clipPath></defs>
    <g clip-path="url(#c-${key})"><circle cx="32" cy="32" r="31" fill="${p.bg}"/>${p.back || ""}
    <path d="M9 64c0-14 10-22 23-22s23 8 23 22z" fill="${p.suit}" stroke="${p.suit === "#f1f5f9" ? "#cbd5e1" : "none"}"/>${p.collar || ""}
    <circle cx="32" cy="27" r="11" fill="${p.skin}"/>${p.front || ""}
    <circle cx="28" cy="27.5" r="1.3" fill="#222"/><circle cx="36" cy="27.5" r="1.3" fill="#222"/>
    <path d="M28.5 32.2q3.5 2.8 7 0" stroke="#222" stroke-width="1.3" fill="none" stroke-linecap="round"/>${p.acc || ""}</g></svg>`;
}

const ST_TXT = { ok: ["✓ a tourné", "pos"], error: ["✗ erreur", "neg"], budget: ["⏸ économisé", "warn"], skipped: ["– au repos", "muted"], unknown: ["…", "muted"] };

function orgCard(key) {
  const p = PERSONA[key], a = state.agents?.agents.find((x) => x.key === key);
  const st = a?.status || "unknown", [txt, cls_] = ST_TXT[st];
  return `<button class="ag st-${st}" data-agent="${key}" style="--tc:${TEAMS[p.team].color}">
    <span class="dot"></span>${avatar(key)}<b class="nm">${esc(p.name)}</b><span class="ti">${esc(p.title)}</span>
    ${a?.model ? `<span class="chip">${esc(a.model)}</span>` : `<span class="chip">code</span>`}<span class="stt ${cls_}">${txt}</span></button>`;
}

function tier(team, keys, extra = "") {
  const t = TEAMS[team];
  return `<div class="tier" style="--tc:${t.color}"><div class="tier-label"><b>${t.label}</b> · ${t.sub}</div>
    <div class="tier-cards ${extra}">${keys.map(orgCard).join("")}</div></div>`;
}

function renderOrg() {
  return `<div class="card org"><div class="card-head"><h2>Organigramme</h2><span class="muted small">touche un personnage</span></div>
    ${tier("direction", ["chef", "plan"], "boss")}
    <div class="link"><span>▲ les éclaireurs l'informent</span><span>▼ ses ordres sont contrôlés</span></div>
    <div class="two-cols">${tier("scouts", ["news", "market", "research", "dossier"])}${tier("guards", ["validator", "risk"])}</div>
    <div class="link"><span>↻ tout le monde relit le playbook et s'améliore</span></div>
    <div class="two-cols">${tier("memory", ["review", "postmortem", "weekly", "report"])}${tier("backoffice", ["screener", "ledger"])}</div>
    <h3>Un soir de séance, dans l'ordre</h3>
    <ol class="flow small">
      <li><b>Sofia</b> relit la veille et propose des règles.</li>
      <li><b>Radar</b> calcule les indicateurs ; <b>Camille</b> constitue les dossiers manquants.</li>
      <li><b>Inès</b> (les chiffres) et <b>Julien</b> (la presse) font leurs analyses.</li>
      <li><b>Victor</b> décide les ordres, cite ses sources et chiffre ses prédictions.</li>
      <li><b>Le Douanier</b> vérifie les règles (PEA, cash, 40 %, liquidité) ; <b>Rachel</b> peut bloquer un ordre.</li>
      <li><b>Compta-3000</b> exécute à l'ouverture suivante et tient les comptes ; <b>Dr Marc</b> analyse chaque position fermée.</li>
    </ol>
    <p class="small muted"><b>Le week-end</b> : <b>Léo</b> cherche sur le web (samedi), <b>Camille</b> archive, <b>Hélène</b> fait le bilan de la semaine,
    <b>Victor</b> prépare le plan du dimanche. <b>Nina</b> rédige le rapport final le dernier jour.</p>
    <p class="small muted">Qui commande : <b>Victor</b> est le seul à décider. Les éclaireurs n'ont aucun pouvoir sur les ordres ; les garde-fous peuvent dire non ;
    la mémoire fait évoluer les règles que tout le monde relit. Le badge <span class="chip">opus</span> ou <span class="chip">sonnet</span> est le modèle utilisé (Opus = le plus fort, réservé aux rôles qui comptent le plus : Victor, Hélène et Nina).</p></div>`;
}

function openAgent(key) {
  const p = PERSONA[key], a = state.agents?.agents.find((x) => x.key === key);
  const hist = (state.agents?.history || []).map((h) => `<i title="${esc(fmtDate(h.date))}" style="background:${AG_DOT[h.states[key]] || AG_DOT.skipped}"></i>`).join("");
  const [txt, cls_] = ST_TXT[a?.status || "unknown"];
  const kt2 = (n) => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));
  $("#day-title").textContent = `${p.name} — ${p.title}`;
  $("#day-body").innerHTML = `<div class="ag-detail"><div class="ag-big">${avatar(key)}</div>
    <p><b>${esc(TEAMS[p.team].label)}</b> · ${esc(TEAMS[p.team].sub)}</p>
    <p class="small">${esc(a?.desc || "")}</p>
    <p class="small">Dernier run : <b class="${cls_}">${txt}</b>${a?.model ? ` · modèle <span class="chip">${esc(a.model)}</span>` : ""}
    ${a?.seconds ? ` · ${a.seconds} s` : ""}${a?.tokens_in ? ` · ${kt2(a.tokens_in)} lus → ${kt2(a.tokens_out)} écrits` : ""}</p>
    ${a?.note ? `<p class="small muted">⏸ ${esc(a.note)}</p>` : ""}${a?.error ? `<p class="small neg">${esc(a.error)}</p>` : ""}
    <p class="small muted">${state.agents?.history?.length || 0} derniers runs</p><div class="dots-line">${hist}</div></div>`;
  $("#day-dialog").showModal();
}

document.addEventListener("click", (ev) => {
  const c = ev.target.closest?.("[data-agent]");
  if (c) { ev.preventDefault(); openAgent(c.dataset.agent); }
});
