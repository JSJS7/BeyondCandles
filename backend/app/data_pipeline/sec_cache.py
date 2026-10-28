"""Shared SEC cache access and atomic JSON writes for every review command."""

import json

from app.data_pipeline.sec import CACHE, cache_response
from app.data_pipeline.source_files import sha256


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending.json")
    pending.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def user_agent():
    from app.core.config import settings

    if not settings.sec_user_agent or "@" not in settings.sec_user_agent:
        raise ValueError(
            "Set SEC_USER_AGENT with an application name and contact address"
        )
    return settings.sec_user_agent


def cached(url, previous=None, json_response=True):
    if previous and previous["url"] == url:
        path = CACHE / previous["file"]
        if (
            path.is_file()
            and path.parent == CACHE
            and sha256(path) == previous["sha256"]
        ):
            return previous
    return cache_response(url, user_agent(), json_response)
