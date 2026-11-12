"""Point d'entrée du run quotidien, lancé par le planificateur de tâches Windows à 18h00.

  python run_daily.py              run normal (séance ou veille selon le jour)
  python run_daily.py --force      refait le run du jour même s'il a déjà eu lieu
  python run_daily.py --test-notify  envoie une notification de test

Toute erreur est journalisée dans logs/ et notifiée sur le téléphone : un run ne plante jamais en silence.
"""
import argparse
import logging
import sys
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from sim.config import ROOT, load_config, merge
from sim.daily import record_status, run
from sim.notify import notify

PARIS = ZoneInfo("Europe/Paris")


def setup_logging(now: datetime):
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s",
                        handlers=[logging.FileHandler(ROOT / "logs" / f"{now:%Y-%m-%d}.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--test-notify", action="store_true")
    args = p.parse_args()

    now = datetime.now(PARIS)
    setup_logging(now)
    log = logging.getLogger("run_daily")
    cfg = None
    try:
        cfg = load_config()
        if args.test_notify:
            ok = notify(cfg, "PEA sim : test", "Les notifications fonctionnent ✅", tags=["white_check_mark"])
            print("notification envoyée" if ok else "échec (voir le log)")
            return 0 if ok else 1
        result = run(now, cfg, force=args.force)
        log.info("%s | %s\n%s", result.mode, result.title, result.message)
        if result.warnings:
            log.warning("avertissements : %s", result.warnings)
            notify(cfg, "⚠️ " + result.title, result.message + "\n\nAvertissements :\n- " + "\n- ".join(result.warnings),
                   priority=4, tags=["warning"])
        elif result.mode == "final" or (result.mode == "trading" and cfg["notify"]["daily_summary"]):
            notify(cfg, result.title, result.message, tags=["chart_with_upwards_trend"])
        return 0
    except Exception as e:
        log.exception("le run a échoué")
        cfg = cfg or raw_config()
        if cfg:
            tb = "".join(traceback.format_exception(e))[-1500:]
            notify(cfg, "❌ PEA sim : le run a échoué", f"{type(e).__name__} : {e}\n\n{tb}", priority=5, tags=["x"])
            try:
                record_status(cfg, ROOT, now, "error", "Le run a échoué", [], error=f"{type(e).__name__} : {e}"[:300])
            except Exception:
                log.exception("état du run non enregistré")
        return 1


def raw_config() -> dict | None:
    """Config lue sans contrôle, pour pouvoir notifier même si config.yaml est invalide."""
    cfg = {}
    for p in (ROOT / "config.yaml", ROOT / "local.yaml"):
        try:
            cfg = merge(cfg, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
        except Exception:
            pass
    return cfg if cfg.get("notify") else None


if __name__ == "__main__":
    sys.exit(main())
