import pytest


@pytest.fixture(autouse=True)
def offline_services(monkeypatch):
    """Testy są hermetyczne: żadnych zapytań do zewnętrznych usług, nawet gdy w .env jest klucz API.

    Podsumowania grup (OpenAI) i wzbogacanie domen (DNS/TLS/RDAP) mają deterministyczne tryby bez sieci.
    """
    monkeypatch.setenv("METHADATA_DISABLE_LLM", "1")
    monkeypatch.setenv("METHADATA_DISABLE_ENRICHMENT", "1")
