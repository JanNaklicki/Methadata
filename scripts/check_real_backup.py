"""Kontrola na rzeczywistych plikach (BACKUP/): czy canonical_id z kontraktu odtwarza zaobserwowane
duplikaty i rodziny. Tylko SHA-256 plików, nic nie jest kopiowane do repo.
Użycie: python scripts/check_real_backup.py [BACKUP]"""
import hashlib
import sys
from pathlib import Path

from methadata.contracts import ids

root = Path(sys.argv[1] if len(sys.argv) > 1 else "BACKUP")


def h(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def canon(d):
    arts = []
    for p in d.iterdir():
        if p.is_file() and p.suffix.lower() in (".html", ".htm"):
            arts.append(("landing_html", h(p)))
        elif p.is_file() and p.suffix.lower() == ".png":
            arts.append(("landing_screenshot", h(p)))
        elif p.is_file() and p.suffix.lower() == ".har":
            arts.append(("har", h(p)))
    return ids.canonical_id(arts)


def images(d):
    out = set()
    for sub in d.iterdir():
        if sub.is_dir():
            out |= {h(f) for f in sub.iterdir() if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")}
    return out


c = {d.name: canon(d) for d in sorted(root.iterdir()) if d.is_dir() and d.name.startswith("p")}
print("p1 == p2 (kopie):", c["p1"] == c["p2"])
print("p2 != p7 != p15 (różne zapisy):", len({c["p2"], c["p7"], c["p15"]}) == 3)
uniq = {v for k, v in c.items()}
print("unikalne canonical_id:", len(uniq), "z", len(c))
fam = [images(root / n) for n in ("p2", "p7", "p15")]
print("rodzina p2/p7/p15, wspólne grafiki:", len(fam[0] & fam[1] & fam[2]), "z", len(fam[0] | fam[1] | fam[2]))
