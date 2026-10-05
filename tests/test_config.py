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
