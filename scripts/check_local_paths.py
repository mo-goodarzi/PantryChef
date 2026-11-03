"""Pre-commit hook: fail if a staged file names a folder on this machine.

Absolute paths (a temp folder, a home folder) or an AI assistant's scratchpad files in a
committed report show the owner's machine and mean nobody else can open the file. Reports
name their files from the repo root instead (evaluation/e2e.py: describe_cases).

Usage (pre-commit passes the staged files):
    uv run python scripts/check_local_paths.py FILE [FILE ...]
"""

import sys
from pathlib import Path

FORBIDDEN = ("/private/tmp", "/Users/", "scratchpad")


def local_paths(text: str) -> list[tuple[int, str]]:
    """(line number, forbidden text) for every line that names a local folder."""
    return [
        (number, word)
        for number, line in enumerate(text.splitlines(), start=1)
        for word in FORBIDDEN
        if word in line
    ]


def main(files: list[str]) -> int:
    found = 0
    for name in files:
        text = Path(name).read_text(errors="replace")
        for number, word in local_paths(text):
            print(f"{name}:{number}: contains {word!r} (a path on this machine)")
            found += 1
    if found:
        print("Use a path from the repo root, or leave the file out of the commit.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
