"""Journal : un fichier JSON par jour, notes de contexte, post-mortems, ordres en attente.

journal/
  2026-11-12.json          run de séance (décisions, sources, validations, exécutions)
  2026-11-14-veille.json   run de veille (week-end / férié)
  context_notes.md         notes de contexte tenues par l'analyste actualité
  postmortems.json         un post-mortem par position clôturée
  pending_orders.json      ordres décidés, à exécuter à la prochaine ouverture
"""
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path


def _default(o):
    if isinstance(o, Decimal):
        return str(o)
    if is_dataclass(o):
        return asdict(o)
    raise TypeError(f"non sérialisable : {type(o)}")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=_default), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


class Journal:
    def __init__(self, root):
        self.root = Path(root)

    # --- Jours ---
    def day_path(self, d: str, mode: str = "trading") -> Path:
        return self.root / (f"{d}.json" if mode == "trading" else f"{d}-veille.json")

    def write_day(self, entry: dict) -> Path:
        path = self.day_path(entry["date"], entry["mode"])
        write_json(path, entry)
        return path

    def read_day(self, d: str, mode: str = "trading") -> dict | None:
        return read_json(self.day_path(d, mode))

    def trading_days(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("????-??-??.json"))

    def recent(self, before: str, n: int) -> list[dict]:
        days = [d for d in self.trading_days() if d < before][-n:]
        return [self.read_day(d) for d in days]

    def last_run_at(self) -> str | None:
        """Dernier run réussi : un run en erreur ne doit pas raccourcir la fenêtre d'actualités du suivant."""
        entries = (read_json(p) for p in self.root.glob("????-??-??*.json"))
        return max((e["run_at"] for e in entries if e.get("run_at") and not e.get("errors")), default=None)

    def is_complete(self, d: str, mode: str = "trading") -> bool:
        """Un jour de séance est terminé s'il a une décision du chef (ou est le dernier jour) ;
        sinon (panne LLM) le run suivant le refait."""
        e = self.read_day(d, mode)
        return bool(e) and (mode != "trading" or bool(e.get("final") or e.get("chef")))

    # --- Notes de contexte ---
    def context_notes(self) -> str:
        p = self.root / "context_notes.md"
        return p.read_text(encoding="utf-8") if p.exists() else "(aucune note pour l'instant)"

    def set_context_notes(self, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "context_notes.md").write_text(text, encoding="utf-8")

    # --- Post-mortems ---
    def postmortems(self) -> list[dict]:
        return read_json(self.root / "postmortems.json", [])

    def add_postmortems(self, items: list[dict]) -> None:
        write_json(self.root / "postmortems.json", self.postmortems() + items)

    # --- Ordres en attente ---
    def save_pending(self, decision_date: str, exec_date: str, orders: list[dict]) -> None:
        write_json(self.root / "pending_orders.json",
                   {"decision_date": decision_date, "exec_date": exec_date, "orders": orders})

    def load_pending(self) -> dict | None:
        return read_json(self.root / "pending_orders.json")

    def clear_pending(self) -> None:
        (self.root / "pending_orders.json").unlink(missing_ok=True)


def digest(entry: dict) -> str:
    """Résumé compact d'un jour de journal, relu par le chef."""
    lines = [f"### {entry['date']}"]
    for e in entry.get("executions", []):
        status = f"exécuté {e['qty']} à {e['price']} (frais {e['fees']})" if e.get("filled") else f"NON exécuté : {e['reason']}"
        lines.append(f"- Exécution de la veille : {e['side']} {e['ticker']} → {status}")
    chef = entry.get("chef") or {}
    if chef.get("market_view"):
        lines.append(f"- Vue : {chef['market_view'][:400]}")
    for o in entry.get("final_orders", []):
        lines.append(f"- Ordre : {o['side']} {o['qty']} {o['ticker']} — {o['thesis'][:200]}")
    for o in entry.get("refused", []):
        lines.append(f"- Refusé ({o['by']}) : {o['side']} {o['ticker']} — {o['reason'][:150]}")
    if not entry.get("final_orders") and chef.get("no_trade_reason"):
        lines.append(f"- Aucun ordre : {chef['no_trade_reason'][:200]}")
    if entry.get("portfolio"):
        p = entry["portfolio"]
        lines.append(f"- Portefeuille : {p['total']} € ({p['return_pct']} %)")
    return "\n".join(lines)
