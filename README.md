# PEA sim — une équipe d'agents LLM sur un PEA fictif

1 000 € fictifs, 30 jours, actions et ETF éligibles PEA. Une équipe d'agents Claude décide chaque soir
des ordres, un simulateur déterministe les exécute à l'ouverture du lendemain. Aucun argent réel, aucun courtier.

## Fonctionnement

Chaque jour à 18h00 (planificateur Windows), `run_daily.py` :

1. met à jour les cours (yfinance, avec l'historique Euronext en secours et en contrôle croisé) ;
2. crédite les dividendes, puis exécute les ordres de la veille au **cours d'ouverture** (spread + frais) ;
3. valorise les 4 portefeuilles à la clôture ;
4. écrit les post-mortems des positions soldées ;
5. fait tourner l'équipe : **analyste marché** → **analyste actualité** → **chef** → contrôles automatiques
   → **gestionnaire de risque** (peut bloquer) → ordres pour demain ;
6. exporte les données du dashboard et les synchronise vers **Supabase** (table `pea_docs`) ;
   la PWA, hébergée sur GitHub Pages, les y lit directement. Un mail n'est envoyé qu'en cas de problème.

Le week-end et les jours fériés : veille uniquement (actualités et notes de contexte, aucun ordre).
Le dernier jour : rapport final.

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
