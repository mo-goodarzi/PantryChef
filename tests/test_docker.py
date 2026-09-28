"""Container setup checks that need no Docker daemon."""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def locked_version(package: str) -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    return next(p["version"] for p in lock["package"] if p["name"] == package)


def test_dockerfile_installs_the_locked_torch_version():
    dockerfile = (ROOT / "Dockerfile").read_text()
    [version] = re.findall(r"^ARG TORCH_VERSION=(\S+)$", dockerfile, flags=re.MULTILINE)
    assert version == locked_version("torch")


def test_dockerfile_never_re_resolves_dependencies():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert dockerfile.count("uv sync --locked") == 2


def test_data_is_mounted_not_baked_in():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert "./data:/app/data" in compose["services"]["api"]["volumes"]
    ignored = (ROOT / ".dockerignore").read_text().split()
    assert {"data", ".env", ".git"} <= set(ignored)


def test_ui_talks_to_the_api_service():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    ui = compose["services"]["ui"]
    assert ui["environment"]["PANTRY_CHEF_API_URL"] == "http://api:8000"
    assert ui["depends_on"]["api"]["condition"] == "service_healthy"
