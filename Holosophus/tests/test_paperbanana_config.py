from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_paperbanana_is_git_ignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/paperbanana/" in gitignore


def test_paperbanana_service_builds_from_upstream_git() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["paperbanana-mcp"]
    build = service["build"]

    assert build["dockerfile"] == "docker/paperbanana-mcp.Dockerfile"
    assert build["args"]["PAPERBANANA_GIT_URL"] == "${PAPERBANANA_GIT_URL:-https://github.com/llmsresearch/paperbanana.git}"
    assert build["args"]["PAPERBANANA_GIT_REF"] == "${PAPERBANANA_GIT_REF:-v0.1.2}"


def test_paperbanana_dockerfile_clones_pinned_ref() -> None:
    dockerfile = (REPO_ROOT / "docker" / "paperbanana-mcp.Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "ARG PAPERBANANA_GIT_URL=https://github.com/llmsresearch/paperbanana.git" in dockerfile
    assert "ARG PAPERBANANA_GIT_REF=v0.1.2" in dockerfile
    assert 'git clone "${PAPERBANANA_GIT_URL}" paperbanana' in dockerfile
    assert 'git checkout "${PAPERBANANA_GIT_REF}"' in dockerfile
    assert 'pip install --no-cache-dir "/opt/paperbanana[mcp]"' in dockerfile
