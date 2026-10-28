"""Read tracked policy files (reviewed rules) with one version check and one cache."""

import json
from functools import lru_cache


@lru_cache(maxsize=16)
def read_policy_file(path, modified):
    """Cache one parsed file; the modification timestamp invalidates a replaced file."""
    return json.loads(path.read_text(encoding="utf-8"))


def load_policy(path, name, versions=(1,)):
    """A missing policy is a configuration error, never an implicit "needs review"."""
    if not path.is_file():
        raise FileNotFoundError(f"Missing {name} policy file: {path}")
    document = read_policy_file(path, path.stat().st_mtime_ns)
    if document["version"] not in versions:
        raise ValueError(f"Unsupported {name} policy version")
    return document
