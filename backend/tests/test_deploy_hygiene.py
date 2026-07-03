from pathlib import Path


def test_root_dockerignore_excludes_sensitive_env_files() -> None:
    repo = Path(__file__).resolve().parents[2]
    dockerignore = repo / ".dockerignore"
    assert dockerignore.exists(), ".dockerignore is missing"

    lines = {
        line.strip()
        for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    required = {"frontend/.env.local", "backend/.env", "deploy/env", "deploy/data"}
    missing = sorted(required - lines)
    assert not missing, f"missing dockerignore entries: {missing}"
