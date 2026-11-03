"""The pre-commit hook that keeps local folder paths out of committed files. The paths are
built from the hook's own list, so this file does not trip the hook."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("hook", ROOT / "scripts" / "check_local_paths.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
TMP, HOME, SCRATCH = hook.FORBIDDEN


def test_a_local_path_is_found_with_its_line_number():
    text = f"# Report\n\nCases: 3 (`{TMP}/x/{SCRATCH}/e.json`)\nok\n"
    assert hook.local_paths(text) == [(3, TMP), (3, SCRATCH)]
    assert hook.local_paths(f"see {HOME}me/notes") == [(1, HOME)]


def test_repo_paths_and_lookalikes_pass():
    text = "Cases: 10 (`eval/cases/e2e.json`)\nusers/ and /private keys\n/tmp alone\n"
    assert hook.local_paths(text) == []


def test_the_hook_fails_only_when_a_file_has_a_local_path(tmp_path, capsys):
    clean, leaky = tmp_path / "clean.md", tmp_path / "leaky.md"
    clean.write_text("Cases: 10 (`eval/cases/e2e.json`)\n")
    leaky.write_text(f"Cases: 3 (`{TMP}/e.json`)\n")
    assert hook.main([str(clean)]) == 0
    assert hook.main([str(clean), str(leaky)]) == 1
    assert "leaky.md:1:" in capsys.readouterr().out
