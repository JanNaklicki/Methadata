"""Rzeczywiste dane (BACKUP): skan obrazów pod kątem manifestu C2PA. Pomijany, gdy brak katalogu."""
import pytest

from methadata.devtools.realdata import BACKUP
from methadata.sources.media_provenance import MediaProvenanceComparator, MediaProvenanceExtractor, read_provenance
from methadata.sources.media_provenance.reader import Unreadable, Unsupported, sniff_mime

pytestmark = pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")


def _scan():
    out = {}
    for ext in ("jpg", "jpeg", "png"):
        for p in BACKUP.rglob(f"*.{ext}"):
            data = p.read_bytes()
            if sniff_mime(data) is None:
                continue
            try:
                out[p] = (data, read_provenance(data))
            except (Unsupported, Unreadable):
                continue
    return out


def test_scan_backup_images_for_c2pa():
    scan = _scan()
    if not scan:
        pytest.skip("brak obrazów w BACKUP")
    with_manifest = {p: pr for p, (_, pr) in scan.items() if pr.present}
    print(f"BACKUP: {len(scan)} obrazów, z manifestem C2PA: {len(with_manifest)}")
    for pr in with_manifest.values():
        assert pr.validation_state in {"Valid", "Trusted", "Invalid"}
    # Ustalone przy S07: 2 z 176 obrazów to PNG wygenerowane przez OpenAI, z manifestem C2PA
    # (Valid; certyfikat spoza listy zaufania). Pozostałe: brak danych.
    for pr in with_manifest.values():
        assert pr.claim_generator == "OpenAI Media Service API"
        assert any(a.startswith("c2pa.created|trainedAlgorithmicMedia") for a in pr.actions)
        # ścieżka zapasowa zgadza się co do obecności i etykiety aktywnego manifestu
    for p, (data, pr) in scan.items():
        if pr.present:
            fb = read_provenance(data, force_fallback=True)
            assert fb.present and fb.active_manifest == pr.active_manifest


def test_two_real_openai_pngs_share_generator_but_not_source(mem, make_obs):
    from methadata.contracts.models import ArtifactRole
    found = [(p, d) for p, (d, pr) in _scan().items() if pr.present]
    if len(found) < 2:
        pytest.skip("brak dwóch obrazów z manifestem w BACKUP")
    obs = [make_obs(p.name, d, ArtifactRole.creative_image, "image/png") for p, d in found[:2]]
    feats = {o.observation_id: MediaProvenanceExtractor().extract(o, reader=mem)[0] for o in obs}
    ev, = MediaProvenanceComparator().compare(obs[0], obs[1], feats, run_id="run-real")
    # mylące dopasowanie: ten sam generator i wystawca, ale bez wspólnego składnika -> brak dowodu
    assert ev.score is None and "bez wspólnego składnika" in ev.explanation
