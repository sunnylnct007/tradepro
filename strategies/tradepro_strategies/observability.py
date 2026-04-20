"""Run IDs, structured JSONL logging, and immutable run manifests.

Every backtest or simulation gets a `run_id` (uuid4). Logs go to
    ~/.tradepro/logs/<date>/<run_id>.jsonl
Artefacts (equity curve, trades, manifest) go to
    ~/.tradepro/artefacts/<run_id>/

The manifest is the reproducibility anchor: inputs + stats + git SHA of the
strategies package. Given a run_id you can replay the exact config.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_ROOT = Path.home() / ".tradepro" / "logs"
ART_ROOT = Path.home() / ".tradepro" / "artefacts"


def new_run_id() -> str:
    return str(uuid.uuid4())


def git_sha() -> str | None:
    try:
        here = Path(__file__).resolve().parent
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=here, text=True,
            stderr=subprocess.DEVNULL, timeout=2,
        ).strip()
    except Exception:
        return None


class RunLogger:
    """JSONL event stream + artefact directory for a single run."""

    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or new_run_id()
        today = datetime.now(timezone.utc).date().isoformat()
        self.log_file = LOG_ROOT / today / f"{self.run_id}.jsonl"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.artefact_dir = ART_ROOT / self.run_id
        self.artefact_dir.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields: Any) -> None:
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        with self.log_file.open("a") as f:
            f.write(json.dumps(line, default=str) + "\n")

    def write_manifest(self, *, inputs: dict, stats: dict, extra: dict | None = None) -> dict:
        manifest = {
            "run_id": self.run_id,
            "git_sha": git_sha(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "inputs": inputs,
            "stats": stats,
            **(extra or {}),
        }
        (self.artefact_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str)
        )
        return manifest
