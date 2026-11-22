from types import SimpleNamespace

from methadata_api import summarizer


def _graph():
    return {"clusters": [{"cluster_id": "c1", "material_ids": []}], "materials": []}


def test_llm_is_off_by_default_even_with_a_key_in_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("METHADATA_ENABLE_LLM", raising=False)
    monkeypatch.delenv("METHADATA_DISABLE_LLM", raising=False)
    monkeypatch.setattr(summarizer, "_call_openai", lambda *a, **k: (_ for _ in ()).throw(AssertionError("wywołano zewnętrzne API")))
    graph = _graph()
    summarizer.summarize_clusters(SimpleNamespace(observations=[], features=[], runs=[], pairs=[]), graph, tmp_path / "c.json")
    assert graph["clusters"][0]["summary"] == summarizer.GENERIC_SUMMARY


def test_llm_needs_explicit_opt_in_and_can_still_be_disabled(monkeypatch):
    monkeypatch.setenv("METHADATA_ENABLE_LLM", "1")
    monkeypatch.delenv("METHADATA_DISABLE_LLM", raising=False)
    assert summarizer.llm_enabled()
    monkeypatch.setenv("METHADATA_DISABLE_LLM", "1")
    assert not summarizer.llm_enabled()
