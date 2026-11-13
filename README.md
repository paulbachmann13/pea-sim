# PEA sim — une équipe d'agents LLM sur un PEA fictif

1 000 € fictifs, 30 jours, actions et ETF éligibles PEA. Une équipe d'agents Claude décide chaque soir
des ordres, un simulateur déterministe les exécute à l'ouverture du lendemain. Aucun argent réel, aucun courtier.

## Fonctionnement

Chaque jour à 18h00 (planificateur Windows), `run_daily.py` :

1. met à jour les cours (yfinance, avec l'historique Euronext en secours et en contrôle croisé) ;
2. crédite les dividendes, puis exécute les ordres de la veille au **cours d'ouverture** (spread + frais) ;
3. valorise les 4 portefeuilles à la clôture ;
4. écrit les post-mortems des positions soldées ;
5. évalue les prédictions arrivées à échéance, puis fait la **revue de la veille** (décisions vs réalité → règles) ;
6. constitue le **dossier 6 mois** des titres suivis qui n'en ont pas (Google News, avant la date de décision) ;
7. fait tourner l'équipe : **analyste marché** → **analyste actualité** (notes, agenda, « déjà dans le prix ») →
   **chef** (sources obligatoires, prédiction chiffrée, scénarios) → contrôles automatiques
   → **gestionnaire de risque** (peut bloquer) → ordres pour demain ;
6. exporte les données du dashboard et les synchronise vers **Supabase** (table `pea_docs`) ;
   la PWA, hébergée sur GitHub Pages, les y lit directement. Un mail n'est envoyé qu'en cas de problème.

Le week-end et les jours fériés : veille, sans ordre.
- **Samedi** : dossiers 6 mois de tous les titres suivis (30 au lieu de 12), puis **recherche web** (un agent avec
  WebSearch/WebFetch, sources obligatoires), puis la revue hebdomadaire.
- **Dimanche** : le chef (Opus) rédige le **plan de la semaine** (événements, scénarios « si X alors Y », titres à
  surveiller). Chaque run de séance le relit.
- Jour férié en semaine : veille légère (actualités, notes, agenda).

Le dernier jour : rapport final.

## Budget et consommation de l'abonnement

Chaque appel Claude est journalisé (`data/usage.jsonl` : tokens, équivalent $ API, durée, quota). L'onglet **Agents**
du dashboard montre la consommation du jour, de la semaine, des 14 derniers jours et par agent, **en euros** (taux BCE
du jour, avec un taux de secours hors ligne). Le mode du budget et la pause se choisissent dans cet onglet, avec un
code de réglage (`settings_code` dans `local.yaml`) ; le run applique le choix au passage suivant. Les chiffres de
`config.yaml` restent en équivalent $ API.

Le quota réel de l'abonnement n'est pas lisible depuis le CLI et il est partagé avec tes autres projets : ce projet
plafonne donc sa propre part (`budget` dans `config.yaml`, en équivalent $ API, ≈ 0,5 $ par jour de bourse mesuré) :

| Réglage | Effet |
|---|---|
| `mode` | `eco` (0,8 $/jour, 5 $/semaine), `normal` (1,2 $ / 8 $), `large` (2,5 $ / 15 $). Dans `local.yaml` : `budget: {mode: eco}` |
| `overrides` | valeurs à forcer par-dessus le mode, ex. `{weekly_usd: 6}` |
| `tier3_share` (dans chaque mode) | les tâches optionnelles (recherche web, dossiers, revue hebdo) s'arrêtent à cette part du budget |
| `pause: true` | aucun appel à Claude (cours, compta et dashboard continuent) |
| `max_call_usd` | plafond d'un seul appel |
| `shared_log` + `global_daily_usd` | fichier commun où d'autres projets ajoutent leurs lignes JSON, pour un plafond global |

Priorités : le chef, le gestionnaire de risque et le rapport final ne sont jamais coupés (sauf pause) ; les analystes,
la revue quotidienne, les post-mortems et le plan s'arrêtent au plafond ; le reste dès 60 % du budget du jour.
Un appel sauté pour le budget est noté « économisé » dans le journal : ce n'est pas une erreur. Un quota épuisé
est noté avec son heure de reset dans `usage.jsonl` et sur le dashboard.

## Apprentissage

Tous les agents relisent à chaque run la base de connaissances (`knowledge/`) :

| Fichier | Contenu |
|---|---|
| `playbook.json` | règles « si X, alors Y », datées, avec cas pour/contre, effet mesuré et historique |
| `playbook_log.json`, `playbook/AAAA-MM-JJ.json` | chaque modification, et une version datée du playbook |
| `predictions.json` | chaque thèse avec horizon et probabilité, évaluée à l'échéance → calibration |
| `notes.json` | dossiers 6 mois des entreprises, notes par secteur et par thème |
| `insights.json` | ce qui a fait (ou non) bouger les cours, biais constatés |
| `agenda.json` | événements à venir et scénarios préparés |

Anti-sur-apprentissage, appliqué par le code : une nouvelle règle naît « hypothèse » ; elle ne devient
« probable » (2 cas) puis « confirmée » (4 cas) qu'avec des cas distincts ; contredite autant que confirmée,
elle devient « contestée » ; 2 cas contre de plus que de cas pour, elle est retirée. 3 nouvelles règles par jour au plus.

| Portefeuille | Rôle |
|---|---|
| Équipe d'agents | les décisions des agents, grille de frais active (BforBank) |
| Agents, grille low-cost | les mêmes ordres, avec la grille de comparaison : l'effet des frais isolé |
| ETF Monde | WPEA acheté au 1er jour et conservé (benchmark) |
| Aléatoire | 4 actions liquides tirées au sort au 1er jour et conservées (contrôle) |

La comptabilité, la validation des ordres et l'exécution sont du code Python testé, pas des LLM.

## Installation (Windows)

```powershell
# depuis C:\Users\paulb\claude-projet2
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install pyyaml pandas yfinance exchange_calendars requests feedparser pytest
cd pea_sim
..\.venv\Scripts\python -m pytest                      # tests
..\.venv\Scripts\python -m sim.refresh                 # test des données (≈ 1 min)
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1   # tâche planifiée 18h00 (+ rattrapage 20h30)
```

Le CLI `claude` (Claude Code) doit être installé et connecté : les agents l'appellent en mode headless.

### Alertes par e-mail

Un mail est envoyé seulement en cas de problème (run planté, agent en erreur, cours manquants,
publication échouée) et pour le rapport final. Le suivi au quotidien se fait sur le dashboard.

1. Créer un mot de passe d'application iCloud : appleid.apple.com → Connexion et sécurité
   → Mots de passe pour app.
2. Le mettre dans `local.yaml` (non versionné), qui surcharge `config.yaml` :

```yaml
notify:
  email: moi@icloud.com
  smtp_password: xxxx-xxxx-xxxx-xxxx
  dashboard_url: https://<utilisateur>.github.io/pea-sim/
publish:
  enabled: true
```

3. Tester : `..\.venv\Scripts\python run_daily.py --test-notify`

## Commandes utiles

```powershell
..\.venv\Scripts\python run_daily.py            # run du jour (séance ou veille)
..\.venv\Scripts\python run_daily.py --force    # refaire le run du jour
..\.venv\Scripts\python -m sim.report           # rapport provisoire (ajouter --llm pour l'analyse rédigée)
```

## Fichiers

| Chemin | Contenu |
|---|---|
| `config.yaml` | frais (2 grilles), spread, limites, seuils, modèles, sources |
| `journal/AAAA-MM-JJ.json` | journal du jour : synthèses, thèses, ordres, refus, exécutions, appels LLM |
| `journal/postmortems.json` | un post-mortem par position soldée |
| `journal/context_notes.md` | notes de contexte de l'analyste actualité |
| `journal/rapport-final.md` | rapport de fin de simulation |
| `data/ledger.db` | comptabilité (SQLite, événements rejouables) |
| `data/market.db` | cours, dividendes, actualités, anomalies de données (régénérable) |
| `docs/` | dashboard (PWA statique servie par GitHub Pages ; version dans `app.js` et `sw.js`) |
| `docs/data/` | export JSON du jour (non versionné), synchronisé vers Supabase |
| `logs/` | un log par jour |
| `sim/` | le code ; `sim/prompts/` les consignes des agents |

## Règles simulées

Long uniquement, actions entières, cash jamais négatif ; 5 ordres/jour et 40 % par titre au maximum ;
éligibilité PEA (ISIN UE/EEE, cotation en euros sur Euronext, liste blanche d'ETF) ; liquidité ≥ 1 M€/jour ;
frais BforBank plafonnés à 0,50 % (plafond PEA) ; spread 0,20 % ; aucune donnée postérieure à la décision.
