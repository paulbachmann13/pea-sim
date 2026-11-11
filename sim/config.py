"""Chargement et contrôle de config.yaml (+ local.yaml optionnel, non versionné, pour les réglages privés)."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config.yaml"
LOCAL_PATH = ROOT / "local.yaml"


class ConfigError(ValueError):
    pass


def merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path=None, local=LOCAL_PATH) -> dict:
    with open(path or DEFAULT_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if local and Path(local).exists():
        with open(local, encoding="utf-8") as f:
            cfg = merge(cfg, yaml.safe_load(f) or {})
    check_config(cfg)
    return cfg


def fee_grid(cfg: dict, name: str | None = None) -> dict:
    return cfg["fees"]["grids"][name or cfg["fees"]["active"]]


def check_config(cfg: dict) -> None:
    grids = cfg["fees"]["grids"]
    if cfg["fees"]["active"] not in grids:
        raise ConfigError(f"grille de frais active inconnue : {cfg['fees']['active']}")
    for name, grid in grids.items():
        tiers = grid["tiers"]
        if not tiers or tiers[-1].get("up_to") is not None:
            raise ConfigError(f"grille {name} : le dernier palier doit avoir up_to: null")
        for t in tiers:
            if ("fixed" in t) == ("rate" in t):
                raise ConfigError(f"grille {name} : chaque palier a soit 'fixed', soit 'rate'")
    if cfg["limits"]["max_orders_per_day"] < 1:
        raise ConfigError("max_orders_per_day doit être >= 1")
    if not 0 < cfg["limits"]["max_position_pct"] <= 100:
        raise ConfigError("max_position_pct doit être dans ]0, 100]")
