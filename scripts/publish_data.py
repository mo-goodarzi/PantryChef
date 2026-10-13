"""Upload the data the Space needs to a PRIVATE Hugging Face dataset repo.

pantry.db, chroma/, two LLM answer caches, and a state.db seed with only the paid caches
(matcher and video answers; no profiles, conversations or spend). The Kaggle data is not
ours to publish, so the script refuses to upload to a repo that is not private.

Usage (after `hf auth login`):
    uv run python scripts/publish_data.py --repo <user>/pantry-chef-data
    uv run python scripts/publish_data.py --repo ... --dry-run    # only build the folder
"""

import argparse
import tempfile
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.deploy import stage_data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="<user>/<private dataset name>")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    processed = get_settings().db_path.parent
    with tempfile.TemporaryDirectory() as tmp:
        files = stage_data(processed, Path(tmp))
        size = sum(p.stat().st_size for p in Path(tmp).rglob("*") if p.is_file())
        print(f"Data files: {', '.join(files)} ({size / 1e9:.2f} GB)")
        if args.dry_run:
            return
        from huggingface_hub import HfApi

        api = HfApi()
        api.create_repo(args.repo, repo_type="dataset", private=True, exist_ok=True)
        if not api.repo_info(args.repo, repo_type="dataset").private:
            raise SystemExit(f"{args.repo} is public: make it private before uploading data")
        api.upload_folder(
            folder_path=tmp, repo_id=args.repo, repo_type="dataset", commit_message="Data"
        )
    print(f"Uploaded to the private dataset {args.repo}")


if __name__ == "__main__":
    main()
