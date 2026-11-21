import stat

from methadata.privacy.pii import key_id, mask, pii_key, pii_token


def test_env_dir_default_and_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("METHADATA_PII_KEY", "A")
    t1, k1 = pii_token("v", "ns")
    assert pii_token("v", "ns") == (t1, k1) and pii_token("v", "other")[0] != t1 and len(t1) == 20 and "A" not in t1
    monkeypatch.setenv("METHADATA_PII_KEY", "B")
    t2, k2 = pii_token("v", "ns")
    assert t2 != t1 and k2 != k1
    monkeypatch.delenv("METHADATA_PII_KEY")
    monkeypatch.setenv("METHADATA_PII_KEY_DIR", str(tmp_path))
    k = pii_key()
    f = tmp_path / "pii.key"
    assert f.exists() and stat.S_IMODE(f.stat().st_mode) == 0o600 and pii_key() == k and key_id(k)


def test_mask():
    assert mask("abcdefghij") == "ab…hij" and mask("abc") == "abc"
