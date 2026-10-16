#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _load_env(repo_root: Path) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in (repo_root / ".env", repo_root / ".env_example"):
        merged.update(_parse_dotenv(path))
    merged.update({k: v for k, v in os.environ.items() if v})
    return merged


def _validate_prefixes(prefixes: list[str]) -> list[str]:
    clean: list[str] = []
    for prefix in prefixes:
        value = prefix.strip().lower().replace("-", "_")
        if not value:
            continue
        if not re.fullmatch(r"[a-z0-9_]+", value):
            raise ValueError(f"Invalid prefix '{prefix}'. Use only [a-z0-9_-].")
        clean.append(value)
    if not clean:
        raise ValueError("At least one non-empty prefix is required")
    return sorted(set(clean))


def _session_filter_sql(prefixes: list[str]) -> str:
    clauses = [
        f"(session_id LIKE '{prefix}_%' OR session_id LIKE '{prefix}-%')"
        for prefix in prefixes
    ]
    return " OR ".join(clauses)


def _run_psql(repo_root: Path, sql: str, env_data: dict[str, str]) -> subprocess.CompletedProcess[str]:
    user = env_data.get("LANGFUSE_POSTGRES_USER", "postgres")
    db = env_data.get("LANGFUSE_POSTGRES_DB", "postgres")
    password = env_data.get("LANGFUSE_POSTGRES_PASSWORD", "postgres")

    env = os.environ.copy()
    env["PGPASSWORD"] = password

    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "langfuse-db",
        "psql",
        "-U",
        user,
        "-d",
        db,
        "-At",
        "-c",
        sql,
    ]
    return subprocess.run(cmd, cwd=repo_root, env=env, text=True, capture_output=True)


def _list_langfuse_sessions(repo_root: Path, prefixes: list[str], env_data: dict[str, str]) -> list[str]:
    where_sql = _session_filter_sql(prefixes)
    sql = (
        "SELECT DISTINCT session_id "
        "FROM traces "
        "WHERE session_id IS NOT NULL AND ("
        + where_sql
        + ") "
        "ORDER BY session_id;"
    )
    result = _run_psql(repo_root, sql, env_data)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to query langfuse-db")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _delete_langfuse_sessions(repo_root: Path, prefixes: list[str], env_data: dict[str, str]) -> None:
    where_sql = _session_filter_sql(prefixes)
    sql = f"""
WITH doomed AS (
    SELECT id, project_id, session_id
    FROM traces
    WHERE session_id IS NOT NULL AND ({where_sql})
),
deleted_observations AS (
    DELETE FROM observations o
    USING doomed d
    WHERE o.trace_id = d.id AND o.project_id = d.project_id
    RETURNING o.id
),
deleted_traces AS (
    DELETE FROM traces t
    USING doomed d
    WHERE t.id = d.id AND t.project_id = d.project_id
    RETURNING t.project_id, t.session_id
)
DELETE FROM trace_sessions ts
USING (
    SELECT DISTINCT project_id, session_id
    FROM deleted_traces
    WHERE session_id IS NOT NULL
) dt
WHERE ts.project_id = dt.project_id
  AND ts.id = dt.session_id;
"""
    result = _run_psql(repo_root, sql, env_data)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to delete Langfuse sessions")


def _count_orphan_traces(repo_root: Path, env_data: dict[str, str]) -> int:
    sql = """
SELECT COUNT(*) FROM (
    SELECT t.id
    FROM traces t
    LEFT JOIN observations o ON o.trace_id = t.id
    GROUP BY t.id
    HAVING COUNT(o.id) <= 1
) sub;
"""
    result = _run_psql(repo_root, sql, env_data)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to count orphan traces")
    return int(result.stdout.strip() or "0")


def _delete_orphan_traces(repo_root: Path, env_data: dict[str, str]) -> int:
    """Delete traces with ≤1 observation (stub/orphan traces). Returns deleted count."""
    sql = """
WITH orphans AS (
    SELECT t.id, t.project_id, t.session_id
    FROM traces t
    LEFT JOIN observations o ON o.trace_id = t.id
    GROUP BY t.id, t.project_id, t.session_id
    HAVING COUNT(o.id) <= 1
),
deleted_observations AS (
    DELETE FROM observations o
    USING orphans orp
    WHERE o.trace_id = orp.id AND o.project_id = orp.project_id
    RETURNING o.id
),
deleted_traces AS (
    DELETE FROM traces t
    USING orphans orp
    WHERE t.id = orp.id AND t.project_id = orp.project_id
    RETURNING t.project_id, t.session_id
),
orphaned_sessions AS (
    SELECT DISTINCT dt.project_id, dt.session_id
    FROM deleted_traces dt
    WHERE dt.session_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM traces t2
        WHERE t2.session_id = dt.session_id AND t2.project_id = dt.project_id
    )
)
DELETE FROM trace_sessions ts
USING orphaned_sessions os
WHERE ts.project_id = os.project_id AND ts.id = os.session_id;
"""
    result = _run_psql(repo_root, sql, env_data)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to delete orphan traces")
    return 0


def _list_workdir_dirs(workdir: Path, prefixes: list[str]) -> list[Path]:
    if not workdir.exists():
        return []
    out: list[Path] = []
    for child in workdir.iterdir():
        if not child.is_dir():
            continue
        name = child.name.lower()
        if any(name.startswith(f"{prefix}_") or name.startswith(f"{prefix}-") for prefix in prefixes):
            out.append(child)
    return sorted(out)


def _delete_workdir_dirs(dirs: list[Path]) -> None:
    for path in dirs:
        shutil.rmtree(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cleanup debug/smoke research artifacts: remove workdir subfolders and "
            "matching Langfuse sessions/traces."
        )
    )
    parser.add_argument(
        "--prefix",
        action="append",
        default=["debug", "smoke"],
        help="Session prefix to clean (can be repeated). Default: debug, smoke",
    )
    parser.add_argument(
        "--workdir",
        default="workdir",
        help="Path to workdir root. Default: workdir",
    )
    parser.add_argument(
        "--skip-workdir",
        action="store_true",
        help="Skip deleting local workdir directories",
    )
    parser.add_argument(
        "--skip-langfuse",
        action="store_true",
        help="Skip deleting Langfuse sessions/traces",
    )
    parser.add_argument(
        "--orphans",
        action="store_true",
        help="Also delete traces with ≤1 observation (stub/orphan traces)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be deleted",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    prefixes = _validate_prefixes(args.prefix)
    workdir = (repo_root / args.workdir).resolve()
    env_data = _load_env(repo_root)

    print(f"Prefixes: {', '.join(prefixes)}")

    workdir_dirs: list[Path] = []
    if not args.skip_workdir:
        workdir_dirs = _list_workdir_dirs(workdir, prefixes)
        print(f"Workdir directories matched: {len(workdir_dirs)}")
        for path in workdir_dirs:
            print(f"  - {path.relative_to(repo_root)}")

    langfuse_sessions: list[str] = []
    orphan_count = 0
    if not args.skip_langfuse:
        try:
            langfuse_sessions = _list_langfuse_sessions(repo_root, prefixes, env_data)
            print(f"Langfuse sessions matched: {len(langfuse_sessions)}")
            for session_id in langfuse_sessions:
                print(f"  - {session_id}")
        except Exception as exc:
            print(f"Langfuse query failed: {exc}")
            if not args.dry_run:
                return 1

        if args.orphans:
            try:
                orphan_count = _count_orphan_traces(repo_root, env_data)
                print(f"Orphan traces (≤1 observation): {orphan_count}")
            except Exception as exc:
                print(f"Orphan count failed: {exc}")
                if not args.dry_run:
                    return 1

    if args.dry_run:
        print("Dry run complete. No changes applied.")
        return 0

    if not args.skip_workdir and workdir_dirs:
        _delete_workdir_dirs(workdir_dirs)
        print(f"Deleted workdir directories: {len(workdir_dirs)}")

    if not args.skip_langfuse:
        if langfuse_sessions:
            try:
                _delete_langfuse_sessions(repo_root, prefixes, env_data)
                print(f"Deleted Langfuse sessions/traces: {len(langfuse_sessions)}")
            except Exception as exc:
                print(f"Langfuse delete failed: {exc}")
                return 1

        if args.orphans and orphan_count > 0:
            try:
                _delete_orphan_traces(repo_root, env_data)
                print(f"Deleted orphan traces: {orphan_count}")
            except Exception as exc:
                print(f"Orphan delete failed: {exc}")
                return 1

    print("Cleanup done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
