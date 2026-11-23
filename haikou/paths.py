"""Stable data-path resolution for scripts and the user-facing service."""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(HERE)
LEGACY_DATABASE = os.path.abspath(os.environ.get(
    "POLICY_DATABASE_LEGACY",
    os.path.join(os.path.expanduser("~"), "Desktop", "database"),
))
WORKSPACE_DATABASE = os.path.join(WORKSPACE, "database")


def default_database() -> str:
    explicit = os.environ.get("POLICY_DATABASE", "").strip()
    if explicit:
        return os.path.abspath(explicit)
    if os.path.isdir(LEGACY_DATABASE):
        return LEGACY_DATABASE
    return WORKSPACE_DATABASE


DEFAULT_DATABASE = default_database()


def resolve_corpus_path(path: str) -> str:
    """Remap an indexed legacy path when the corpus moved with the workspace."""
    if not path:
        return path
    if os.path.exists(path):
        return path
    normalized = os.path.normpath(path)
    legacy = os.path.normpath(LEGACY_DATABASE)
    if normalized == legacy or normalized.startswith(legacy + os.sep):
        relative = os.path.relpath(normalized, legacy)
        candidate = os.path.join(DEFAULT_DATABASE, relative)
        if os.path.exists(candidate):
            return candidate
    return path
