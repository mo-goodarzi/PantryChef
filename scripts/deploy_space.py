"""Build and upload the Hugging Face Space (code only; the data is in a private dataset).

Needs `hf auth login` with a write token and a PRO account (Docker Spaces need one).

Usage:
    uv run python scripts/deploy_space.py --space <user>/pantry-chef \\
        --data-repo <user>/pantry-chef-data
    uv run python scripts/deploy_space.py ... --dry-run     # only build the folder

The Space Dockerfile is the project's Dockerfile with the Space's start command, so the
two never drift apart. Secrets are NOT set by this script: add OPENAI_API_KEY,
YOUTUBE_API_KEY and HF_TOKEN (read access to the data repo) in the Space settings.
"""

import argparse
import re
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
CODE = ["pyproject.toml", "uv.lock", ".python-version", "src", "ui", ".streamlit", "scripts"]
SPACE = ROOT / "deploy" / "space"
START = 'COPY deploy ./deploy\nEXPOSE 7860\nCMD ["sh", "deploy/space/start.sh"]\n'


def space_dockerfile(dockerfile: str) -> str:
    """The project's Dockerfile, ending with the Space's port and start script."""
    body, count = re.subn(r"\nEXPOSE [^\n]*\nCMD [^\n]*\n?$", "\n" + START, dockerfile)
    if count != 1:
        raise ValueError("Dockerfile does not end with EXPOSE + CMD; update deploy_space.py")
    return body


def build(dest: Path) -> list[str]:
    """The Space repo's files in `dest` (code, Space README, Dockerfile, start script)."""
    for name in CODE:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, dest / name)
    shutil.copy2(SPACE / "README.md", dest / "README.md")  # the Space card (front matter)
    (dest / "deploy" / "space").mkdir(parents=True)
    shutil.copy2(SPACE / "start.sh", dest / "deploy" / "space" / "start.sh")
    (dest / "Dockerfile").write_text(space_dockerfile((ROOT / "Dockerfile").read_text()))
    return sorted(p.name for p in dest.iterdir())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--space", required=True, help="<user>/<space name>")
    parser.add_argument("--data-repo", required=True, help="<user>/<private dataset>")
    parser.add_argument("--budget", type=float, default=2.0, help="USD per day")
    parser.add_argument("--max-turns", type=int, default=40, help="per conversation")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        files = build(Path(tmp))
        print("Space files:", ", ".join(files))
        if args.dry_run:
            print(Path(tmp, "Dockerfile").read_text().rsplit("\n", 4)[-4:])
            return
        from huggingface_hub import HfApi

        api = HfApi()
        api.create_repo(args.space, repo_type="space", space_sdk="docker", exist_ok=True)
        variables = {
            "DATA_REPO": args.data_repo,
            "DAILY_BUDGET_USD": str(args.budget),
            "MAX_TURNS_PER_SESSION": str(args.max_turns),
        }
        for key, value in variables.items():
            api.add_space_variable(args.space, key, value)
        api.upload_folder(
            folder_path=tmp, repo_id=args.space, repo_type="space", commit_message="Deploy"
        )
    print(f"Uploaded: https://huggingface.co/spaces/{args.space}")
    print(
        "Set these secrets in the Space settings if not done yet: OPENAI_API_KEY, "
        "YOUTUBE_API_KEY, HF_TOKEN (read access to the data repo)."
    )


if __name__ == "__main__":
    main()
