"""Publication du dashboard : commit + push des fichiers générés vers GitHub (GitHub Pages sert docs/)."""
import subprocess
from pathlib import Path


class PublishError(RuntimeError):
    pass


def _git(root: Path, *args) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise PublishError(f"git {' '.join(args)} : {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout


def publish(cfg: dict, root: Path, message: str) -> bool:
    """Renvoie True si quelque chose a été poussé."""
    if not cfg["publish"]["enabled"]:
        return False
    _git(root, "add", "--", *cfg["publish"]["paths"])
    if not _git(root, "status", "--porcelain", "--", *cfg["publish"]["paths"]).strip():
        return False
    _git(root, "commit", "-m", message)
    for attempt in range(3):
        try:
            _git(root, "push")
            return True
        except PublishError:
            if attempt == 2:
                raise
            _git(root, "pull", "--rebase")
    return False
