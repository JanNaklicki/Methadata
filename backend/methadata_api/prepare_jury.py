"""Przygotuj lokalny indeks jury i osobny plik do przesłania w podglądzie."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from methadata.devtools.realdata import BACKUP
from methadata.ingest import FileImporter
from methadata.ingest.importer import JUNK, TAG_PAGE

from .settings import DEFAULT_JURY_CONFIG, MAX_UPLOAD_BYTES, Settings
from .storage import Storage, atomic_write_json


def _package(source: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory(dir=destination.parent) as work:
        package = Path(work) / destination.name
        with ZipFile(package, "w", compression=ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                rel = path.relative_to(source)
                if any(part.lower() in JUNK or part.startswith("._") for part in rel.parts):
                    continue
                if path.is_symlink() or not path.is_file():
                    continue
                # Ten sam podział ról co przy imporcie katalogu: PNG obok HTML to zrzut strony.
                name = Path("screenshots") / rel if len(rel.parts) == 1 and path.suffix.lower() == ".png" else rel
                archive.write(path, name.as_posix())
        if package.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("Pakiet przekracza limit 50 MiB.")
        package.replace(destination)


def prepare(data_dir: Path, *, config_path: Path = DEFAULT_JURY_CONFIG, backup: Path = BACKUP) -> Path:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    names, upload = cfg["index"], cfg["upload"]
    if not names or len(set(names)) != len(names) or upload in names:
        raise ValueError("Indeks wymaga unikalnych materiałów i musi pomijać materiał do przesłania.")
    for name in [*names, upload]:
        source = backup / name
        if not source.is_dir() or source.is_symlink():
            raise ValueError(f"Brak katalogu źródłowego {name} w {backup}.")
    storage = Storage(Settings(data_dir=data_dir))
    storage.prepare()
    report = FileImporter().import_sources([backup / name for name in names], storage.store())
    observations = {e.source_ref: e.observation_id for e in report.entries if TAG_PAGE in e.tags}
    if report.errors or set(observations) != {f"dir:{name}" for name in names}:
        raise ValueError("Import korpusu nie dostarczył wszystkich zapisów stron. Sprawdź dziennik store/imports.jsonl.")
    package = data_dir / f"{upload}.zip"
    _package(backup / upload, package)
    labels = {**storage.labels(), **{observations[f"dir:{name}"]: name for name in names}}
    atomic_write_json(storage.settings.meta_dir / "labels.json", labels)
    atomic_write_json(storage.settings.meta_dir / "index.json", {
        "dataset_name": cfg["dataset_name"],
        "observation_ids": [observations[f"dir:{name}"] for name in names],
    })
    return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        package = prepare(args.data_dir)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Pakiet do przesłania: {package}")


if __name__ == "__main__":
    main()
