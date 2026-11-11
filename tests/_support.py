"""Shared test fixtures: offline live feeds, an isolated community store, quick HTTP servers.

Import as ``from tests import _support`` after the test file has put the repository
root on sys.path; that works under ``unittest discover -s tests`` and when a file
is run by path.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DATA = ROOT / "data"


def offline_public_live() -> None:
    """Call from setUpModule: server.fetch_public_live returns no feeds until the module ends."""
    live_patch = patch("server.fetch_public_live", return_value={"stations": [], "feeds": {}})
    live_patch.start()
    unittest.addModuleCleanup(live_patch.stop)


def isolate_community_store() -> None:
    """Call from setUpModule: the community store lives in a temporary directory until
    the module ends, so no test opens the private data/community.db."""
    import community

    original = (community._DB_PATH, community._PHOTOS_DIR)
    directory = Path(tempfile.mkdtemp(prefix="volta-community-"))
    community.configure(db_path=directory / "community.db", photos_dir=directory / "photos")

    def restore() -> None:
        community.configure(db_path=original[0], photos_dir=original[1])
        shutil.rmtree(directory, ignore_errors=True)

    unittest.addModuleCleanup(restore)


def serve_in_thread(httpd) -> Callable[[], None]:
    """Serve httpd on a daemon thread; return a stop function that shuts down, closes and joins.

    A short poll interval keeps shutdown() from blocking for the default half second.
    """
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    def stop() -> None:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    return stop
