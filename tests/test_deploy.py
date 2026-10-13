"""Hugging Face Space packaging: the data bundle, the state seed, the start-up download and
the Space's Dockerfile and README. No network: huggingface_hub is faked."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

from pantry_chef.db.state import ProfileStore, open_state_db
from pantry_chef.deploy import fetch, seed_state, stage_data
from pantry_chef.models.profile import UserProfile

ROOT = Path(__file__).parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state_with_user_data(path):
    state = open_state_db(path)
    with state:
        state.execute(
            "INSERT INTO match_cache VALUES ('eggs', 'egg', 'same', 'llm:v2:gpt-5.4-mini', NULL)"
        )
        state.execute("INSERT INTO daily_spend VALUES ('2026-10-13', 1.5)")
    ProfileStore(state).save("alice", UserProfile(consent_to_store=True))
    state.close()


def test_the_seed_keeps_paid_caches_and_drops_user_data(tmp_path):
    state_with_user_data(tmp_path / "state.db")
    copied = seed_state(tmp_path / "state.db", tmp_path / "seed.db")

    assert copied == {"match_cache": 1, "video_cache": 0}
    seed = open_state_db(tmp_path / "seed.db")
    assert seed.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
    assert seed.execute("SELECT COUNT(*) FROM daily_spend").fetchone()[0] == 0


def test_stage_data_has_what_the_app_reads(tmp_path):
    processed = tmp_path / "processed"
    (processed / "chroma").mkdir(parents=True)
    (processed / "chroma" / "index.bin").write_bytes(b"x")
    for name in ["pantry.db", "allergy_reviews.json", "hidden_allergens.json"]:
        (processed / name).write_text("{}")
    (processed / "wish_fit.json").write_text("{}")  # holds wishes people typed: not shipped
    state_with_user_data(processed / "state.db")

    files = stage_data(processed, tmp_path / "out")
    assert files == [
        "allergy_reviews.json",
        "chroma",
        "hidden_allergens.json",
        "pantry.db",
        "state.db",
    ]
    assert (tmp_path / "out" / "chroma" / "index.bin").exists()


def test_fetch_downloads_once_and_keeps_this_runs_state(tmp_path, monkeypatch):
    calls = []

    def fake_download(repo_id, **kwargs):
        calls.append((repo_id, kwargs))
        dest = kwargs["local_dir"]
        (dest / "chroma").mkdir(parents=True, exist_ok=True)
        (dest / "pantry.db").write_text("db")

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_download)
    dest = tmp_path / "processed"
    dest.mkdir()
    (dest / "state.db").write_text("this run's conversations")

    assert fetch("me/data", dest, token="t") is True
    [(repo_id, kwargs)] = calls
    assert repo_id == "me/data" and kwargs["repo_type"] == "dataset" and kwargs["token"] == "t"
    assert kwargs["ignore_patterns"] == ["state.db"]  # the seed never overwrites live state
    assert fetch("me/data", dest, token="t") is False  # already there: no download
    assert len(calls) == 1


def test_space_dockerfile_is_the_project_dockerfile_with_the_space_start(tmp_path):
    deploy_space = load_script("deploy_space")
    dockerfile = (ROOT / "Dockerfile").read_text()
    space = deploy_space.space_dockerfile(dockerfile)

    assert space.startswith(dockerfile.rsplit("\nEXPOSE", 1)[0])  # everything else the same
    assert space.rstrip().endswith('CMD ["sh", "deploy/space/start.sh"]')
    assert "EXPOSE 7860" in space and "8501" not in space


def test_space_folder_has_code_and_never_data_or_secrets(tmp_path):
    deploy_space = load_script("deploy_space")
    files = deploy_space.build(tmp_path)
    assert {"Dockerfile", "README.md", "src", "ui", "uv.lock", "deploy"} <= set(files)
    assert not {"data", ".env", "eval", "tests"} & set(files)
    assert not list(tmp_path.rglob("*.db"))


def test_space_card_and_start_script_use_the_same_port():
    card = (ROOT / "deploy" / "space" / "README.md").read_text()
    meta = yaml.safe_load(card.split("---")[1])
    assert meta["sdk"] == "docker"
    start = (ROOT / "deploy" / "space" / "start.sh").read_text()
    assert f"--server.port={meta['app_port']}" in start
    assert "--host 127.0.0.1" in start  # the API is never exposed publicly
    assert re.search(r"python -m pantry_chef\.deploy fetch", start)


def test_publish_refuses_a_public_repo(monkeypatch, tmp_path):
    publish = load_script("publish_data")

    class FakeApi:
        def create_repo(self, *args, **kwargs):
            assert kwargs["private"] is True

        def repo_info(self, *args, **kwargs):
            return type("Info", (), {"private": False})()

        def upload_folder(self, **kwargs):
            raise AssertionError("must not upload to a public repo")

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    monkeypatch.setattr(publish, "stage_data", lambda processed, staging: ["pantry.db"])
    monkeypatch.setattr(sys, "argv", ["publish_data.py", "--repo", "me/data"])
    try:
        publish.main()
    except SystemExit as stop:
        assert "is public" in str(stop)
    else:
        raise AssertionError("expected a refusal")


def test_only_a_clean_up_to_date_main_is_deployed(monkeypatch):
    deploy_space = load_script("deploy_space")
    state = {"branch": "main", "status": "", "head": "abc", "origin": "abc"}

    def fake_git(*args):
        if args[0] == "fetch":
            return ""
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return state["branch"]
        if args[0] == "status":
            return state["status"]
        if args == ("rev-parse", "HEAD"):
            return state["head"]
        if args == ("rev-parse", "origin/main"):
            return state["origin"]
        return "abc1234"  # --short HEAD

    monkeypatch.setattr(deploy_space, "git", fake_git)
    assert deploy_space.deployable_commit() == "abc1234"
    for change, reason in [
        ({"branch": "feature"}, "not on main"),
        ({"status": " M ui/streamlit_app.py"}, "uncommitted changes"),
        ({"origin": "def"}, "differs from origin/main"),
    ]:
        monkeypatch.setitem(state, next(iter(change)), next(iter(change.values())))
        with pytest.raises(SystemExit, match=reason):
            deploy_space.deployable_commit()
        state.update({"branch": "main", "status": "", "head": "abc", "origin": "abc"})
