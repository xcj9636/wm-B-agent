from pathlib import Path
import subprocess


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_readme_has_no_merge_conflict_markers():
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    for marker in ("<<<<<<<", "=======", ">>>>>>>"):
        assert marker not in readme


def test_every_gitlink_has_a_submodule_mapping():
    staged_files = subprocess.run(
        ["git", "ls-files", "--stage"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    gitlinks = {
        line.split(maxsplit=3)[3]
        for line in staged_files
        if line.startswith("160000 ")
    }

    gitmodules = REPOSITORY_ROOT / ".gitmodules"
    configured_paths: set[str] = set()
    if gitmodules.exists():
        configured = subprocess.run(
            [
                "git",
                "config",
                "-f",
                str(gitmodules),
                "--get-regexp",
                r"^submodule\..*\.path$",
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        configured_paths = {line.split(maxsplit=1)[1] for line in configured}

    assert gitlinks <= configured_paths
