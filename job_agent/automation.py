from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
import uuid

from .db import DATA_DIR


def launch_prepared_application(
    url: str,
    profile: dict[str, Any],
    resume_path: str = "",
    cover_letter_path: str = "",
) -> None:
    """Open and fill safe known fields in a separate visible browser process."""
    manifest_dir = DATA_DIR / "browser_sessions"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_dir / f"{uuid.uuid4().hex}.json"
    manifest.write_text(
        json.dumps(
            {
                "url": url,
                "profile": profile,
                "resume_path": resume_path,
                "cover_letter_path": cover_letter_path,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "job_agent.browser_runner", str(manifest)],
            cwd=str(Path(__file__).resolve().parent.parent),
            creationflags=flags,
            close_fds=True,
        )
    except OSError:
        manifest.unlink(missing_ok=True)
        raise
