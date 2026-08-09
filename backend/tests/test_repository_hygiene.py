from pathlib import Path
import re
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


def test_runtime_versions_are_declared():
    assert (REPOSITORY_ROOT / ".python-version").read_text().strip() == "3.11"
    assert (REPOSITORY_ROOT / ".nvmrc").read_text().strip() == "20"


def test_alembic_baseline_is_present():
    alembic_config = REPOSITORY_ROOT / "backend" / "alembic.ini"
    versions = REPOSITORY_ROOT / "backend" / "alembic" / "versions"

    assert alembic_config.is_file()
    assert any(versions.glob("*.py"))


def test_ci_covers_backend_frontend_and_compose_gates():
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    for command in (
        "pytest -q",
        "npm ci",
        "npm test",
        "npm run build",
        "docker compose config",
    ):
        assert command in workflow


def test_celery_beat_does_not_reference_django_scheduler():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "django_celery_beat" not in compose
    assert "celerybeat-schedule" in compose


def test_gateway_architecture_decisions_and_notice_are_recorded():
    adr_dir = REPOSITORY_ROOT / "docs" / "adr"

    assert (adr_dir / "0001-omniroute-shared-gateway.md").is_file()
    assert (adr_dir / "0002-single-organization-trust-boundary.md").is_file()
    notice = (REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )
    assert "Copyright (c) 2026 diegosouzapw" in notice
    assert "MIT License" in notice


def test_omniroute_compose_service_is_pinned_and_internal_only():
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    service = re.search(
        r"(?ms)^  omniroute:\n(.*?)(?=^  [a-zA-Z][a-zA-Z0-9_-]*:\n|\Z)",
        compose,
    )

    assert service is not None
    service_config = service.group(1)
    assert "e0ce95c592c00f100f5141371dbda976d678ddee" in service_config
    assert "profiles: [gateway]" in service_config
    assert "ports:" not in service_config
    assert "ai_gateway_network" in service_config
    assert re.search(
        r"(?ms)^  ai_gateway_network:\n(?:    .*\n)*?    internal: true$",
        compose,
    )
