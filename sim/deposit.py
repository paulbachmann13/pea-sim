"""Versement supplémentaire dans la simulation.

  python -m sim.deposit 4000                 versement aujourd'hui dans les 4 portefeuilles
  python -m sim.deposit 4000 --date 2026-11-16

Le versement est ajouté à TOUS les livres (agents, agents low-cost, benchmark, aléatoire) pour que la comparaison
reste équitable. Il est supposé arriver après la clôture de sa date : il est investissable à partir de la séance
suivante (le benchmark et l'aléatoire le font automatiquement ; les agents décident le soir). Les performances en %
sont pondérées par le temps, donc un versement ne crée ni gain ni perte apparents.
"""
import argparse
from datetime import date
from decimal import Decimal

from .config import ROOT, load_config
from .daily import BOOKS, open_env
from .data import PriceStore


def add_deposit(env, store, amount, day: str, force: bool = False) -> list[str]:
    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError("le versement doit être positif")
    if not force and any(d == day and a == amount for d, a in env.agents.deposit_events()):
        raise ValueError(f"un versement de {amount} € daté du {day} existe déjà (--force pour l'ajouter quand même)")
    notes = []
    for book in BOOKS:
        led = env.ledgers[book]
        led.deposit(day, amount)
        if led.last_snapshot_date() == day:                 # run du jour déjà passé : on remet à jour sa valeur
            led.snapshot(day, store.marks(list(led.state().positions), day))
            notes.append(f"{book} : valeur du {day} recalculée")
    return notes


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("amount", type=float, help="montant du versement, en euros")
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    cfg = load_config()
    env = open_env(cfg, ROOT)
    store = PriceStore(ROOT / cfg["data"]["market_db"])
    for n in add_deposit(env, store, args.amount, args.date, args.force):
        print(n)
    st = env.agents.state()
    print(f"Versement de {args.amount:g} € enregistré le {args.date}. Total déposé : {st.deposits} €, cash : {st.cash} €.")


if __name__ == "__main__":
    main()
