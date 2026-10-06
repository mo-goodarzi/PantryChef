"""Settings and the example env file."""

from pathlib import Path

from pantry_chef.config import Settings

ROOT = Path(__file__).parents[1]


def example_values() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def test_env_example_never_contains_keys():
    """.env.example is committed and public: every key must stay empty."""
    secrets = {name: v for name, v in example_values().items() if name.endswith("_KEY") and v}
    assert secrets == {}, f"remove the values of {sorted(secrets)} from .env.example"


def test_env_example_names_are_settings():
    fields = {name.upper() for name in Settings.model_fields}
    unknown = set(example_values()) - fields - {"LANGFUSE_BASE_URL"}
    assert unknown == set(), f".env.example names the app does not read: {sorted(unknown)}"


def test_langfuse_host_also_reads_langfuse_base_url(monkeypatch):
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://eu.example")
    assert Settings(_env_file=None).langfuse_host == "https://eu.example"
    monkeypatch.setenv("LANGFUSE_HOST", "https://host.example")
    monkeypatch.delenv("LANGFUSE_BASE_URL")
    assert Settings(_env_file=None).langfuse_host == "https://host.example"


def test_ingredient_labels_use_label_model_not_llm_model(monkeypatch, tmp_path, capsys):
    """The labels' allergens feed the SQL filter: a cheaper LLM_MODEL must not reach them."""
    import importlib.util
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location(
        "label_ingredients", ROOT / "scripts" / "label_ingredients.py"
    )
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    settings = Settings(_env_file=None, llm_model="gpt-5.4-nano", label_model="gpt-5.4-mini")
    models = []
    monkeypatch.setattr(script, "get_settings", lambda: settings)
    monkeypatch.setattr(script, "ingredient_names_by_frequency", lambda db: ["egg"])
    monkeypatch.setattr(script, "create_llm", lambda s: models.append(s.llm_model) or "llm")
    summary = SimpleNamespace(requested=1, already_cached=0, labeled=1, missing=0, failed_batches=0)
    monkeypatch.setattr(script, "label_ingredients", lambda *a, **k: summary)
    monkeypatch.setattr("sys.argv", ["label_ingredients.py", "--cache", str(tmp_path / "c")])
    script.main()
    assert models == ["gpt-5.4-mini"]
    assert "model: gpt-5.4-mini" in capsys.readouterr().out
