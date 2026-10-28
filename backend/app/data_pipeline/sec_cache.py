"""Shared SEC cache access and request spacing for every review command."""

from threading import Event, Lock
from time import monotonic, sleep

from app.data_pipeline.sec import CACHE, SecAccessDenied, cache_response
from app.data_pipeline.source_files import sha256

# SEC allows at most ten requests per second; the reviews stay well below.
REQUEST_INTERVAL = 0.25


class RequestGate:
    """Space requests of concurrent download workers to four per second.

    After SEC denies access (HTTP 403) every worker stops; no bypass is attempted.
    """

    def __init__(self, stopped_message):
        self.lock = Lock()
        self.stopped = Event()
        self.last_request = 0.0
        self.stopped_message = stopped_message

    def check_running(self):
        if self.stopped.is_set():
            raise ValueError(self.stopped_message)

    def wait_turn(self):
        with self.lock:
            self.check_running()
            sleep(max(0, self.last_request + REQUEST_INTERVAL - monotonic()))
            self.check_running()
            self.last_request = monotonic()

    def run_alone(self, download, interval):
        """Run a multi-request download while no other worker sends requests."""
        with self.lock:
            self.check_running()
            sleep(max(0, self.last_request + interval - monotonic()))
            try:
                download()
            finally:
                self.last_request = monotonic()

    def fetch(self, url):
        """Download one statement file through the gate; stop all workers on 403."""
        self.wait_turn()
        try:
            return cached(url, json_response=False)
        except SecAccessDenied:
            self.stopped.set()
            raise


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
