"""Frozen-source paths, JSON reads and writes and file checksums shared by data pipelines."""

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "data/manifest.json"
SELECTION = ROOT / "data/companies-200.json"


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


# While a --check run is active: the JSON text each write would have published,
# by resolved path. Nothing is written to disk until the run ends.
_held_writes = None

# Top-level keys that change on every run without changing the reviewed data.
RUN_TIMESTAMP_KEYS = ("reviewed_at",)


def read_json(path):
    if _held_writes is not None:
        held = _held_writes.get(Path(path).resolve())
        if held is not None:
            # A later step of the same run reads what an earlier step would have written.
            return json.loads(held)
    return json.loads(Path(path).read_text(encoding="utf-8"))


@contextmanager
def held_writes():
    """Hold every write_json in memory for the duration of a --check run.

    Yields a dict filled when the run ends: relative paths whose content would
    change ("changed") or stay the same ("unchanged"), ignoring run timestamps.
    """
    global _held_writes
    _held_writes = {}
    comparison = {}
    try:
        yield comparison
        comparison.update(compare_held_writes(_held_writes))
    finally:
        _held_writes = None


def compare_held_writes(held):
    changed = []
    unchanged = []
    for path, text in sorted(held.items()):
        relative = path.relative_to(ROOT).as_posix()
        if path.exists() and same_data(path.read_text(encoding="utf-8"), text):
            unchanged.append(relative)
        else:
            changed.append(relative)
    return {"changed": changed, "unchanged": unchanged}


def same_data(old_text, new_text):
    old_document = json.loads(old_text)
    new_document = json.loads(new_text)
    for document in (old_document, new_document):
        if isinstance(document, dict):
            for key in RUN_TIMESTAMP_KEYS:
                document.pop(key, None)
    return old_document == new_document


def write_json(path, document, *, ensure_ascii=True):
    """Publish complete JSON only; an interrupted write leaves the old file intact."""
    path = Path(path)
    if _held_writes is not None:
        text = json.dumps(document, indent=2, ensure_ascii=ensure_ascii) + "\n"
        _held_writes[path.resolve()] = text
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle, indent=2, ensure_ascii=ensure_ascii)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
