"""Frozen-source paths, JSON reads and file checksums shared by data pipelines."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "data/manifest.json"
SELECTION = ROOT / "data/companies-200.json"


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
