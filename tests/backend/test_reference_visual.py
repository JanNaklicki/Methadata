from methadata_api import reference_visual as ref


def test_reference_images_are_labelled_from_file_and_folder_names(tmp_path):
    (tmp_path / "Jan_Kowalski.jpg").write_bytes(b"x")
    (tmp_path / "Anna-Nowak").mkdir()
    (tmp_path / "Anna-Nowak" / "1.png").write_bytes(b"x")
    (tmp_path / "notatki.txt").write_text("nie obraz")
    assert sorted(label for label, _ in ref._discover_references(tmp_path)) == ["Anna Nowak", "Jan Kowalski"]


def test_directory_comes_from_env_before_default_locations(tmp_path, monkeypatch):
    monkeypatch.setenv("METHADATA_REFERENCE_DIR", str(tmp_path))
    assert ref._reference_dir() == tmp_path
    monkeypatch.delenv("METHADATA_REFERENCE_DIR")
    assert ref._reference_dir().name == "reference_figures"


def test_bundled_reference_photos_match_a_resized_copy_but_not_another_person(tmp_path, monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace

    PIL = __import__("pytest").importorskip("PIL.Image")
    phash = __import__("pytest").importorskip("methadata.experiments.perceptual.phash")
    monkeypatch.delenv("METHADATA_REFERENCE_DIR", raising=False)
    directory = ref._reference_dir()
    labels = {label for label, _ in ref._discover_references(directory)}
    assert {"Karol Nawrocki", "Tomasz Sekielski", "Rafał Brzoska"} <= labels

    def feature(observation_id, path):
        with PIL.open(path) as image:
            small = image.convert("RGB").resize((image.width // 2, image.height // 2))
            buffer = BytesIO()
            small.save(buffer, "JPEG", quality=70)
        return SimpleNamespace(family="perceptual", kind="phash", observation_id=observation_id,
                               value={"phash": phash.to_hex(phash.phash_bytes(buffer.getvalue()))})

    bundle = SimpleNamespace(
        observations=[SimpleNamespace(observation_id="o1", canonical_id="m1"), SimpleNamespace(observation_id="o2", canonical_id="m2")],
        features=[feature("o1", directory / "Rafał_Brzoska.jpg"), feature("o2", directory / "Tomasz_Sekielski.jpg")])
    found = ref.match_reference_figures(bundle)
    assert [hit["person"] for hit in found["m1"]] == ["Rafał Brzoska"]
    assert [hit["person"] for hit in found["m2"]] == ["Tomasz Sekielski"]
