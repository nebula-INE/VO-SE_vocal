"""GitHub Release update detection for the VO-SE desktop application.

The updater deliberately opens the release page instead of replacing the running
application. Replacing a frozen EXE or macOS .app in-place is not safe across
platforms and can leave an installation unusable.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

GITHUB_REPOSITORY = "nebula-INE/VO-SE_vocal"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/tags/latest"
)
TAG_REF_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/git/ref/tags"
COMPARE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/compare"
RELEASE_PAGE_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/latest"
REQUEST_TIMEOUT_SECONDS = 6

# release.yml replaces this value with the exact commit SHA used for packaging.
# Local/source builds do not perform release notifications.
CURRENT_BUILD_SHA = "development"


def _read_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "VO-SE-Pro-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(
        request, timeout=REQUEST_TIMEOUT_SECONDS
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub API response must be a JSON object")
    return payload


def _resolve_tag_commit(tag_name: str) -> str:
    """Resolve either a lightweight or annotated Git tag to its commit SHA."""
    encoded_tag = urllib.parse.quote(tag_name, safe="")
    payload = _read_json(f"{TAG_REF_API}/{encoded_tag}")
    obj = payload.get("object")
    if not isinstance(obj, dict):
        raise ValueError("Git tag response has no object")

    for _ in range(3):
        object_type = obj.get("type")
        object_sha = obj.get("sha")
        if not isinstance(object_sha, str) or not object_sha:
            raise ValueError("Git tag object has no SHA")
        if object_type == "commit":
            return object_sha
        if object_type != "tag":
            raise ValueError(f"Unsupported Git tag object type: {object_type}")

        annotated = _read_json(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/git/tags/{object_sha}"
        )
        obj = annotated.get("object")
        if not isinstance(obj, dict):
            raise ValueError("Annotated Git tag has no target object")

    raise ValueError("Git tag nesting is too deep")


def check_for_update(
    current_build_sha: str = CURRENT_BUILD_SHA,
) -> tuple[bool, str, str, str]:
    """Return (has_update, display_version, release_page_url, download_url).

    A release is considered newer only when its commit is ahead of the installed
    build on GitHub's commit graph. This avoids treating a build from a newer
    development commit as outdated merely because the latest release tag differs.
    """
    if not current_build_sha or current_build_sha.lower() in {
        "development",
        "unknown",
        "0" * 40,
    }:
        return False, "", RELEASE_PAGE_URL, ""

    release = _read_json(LATEST_RELEASE_API)
    tag_name = release.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name:
        raise ValueError("Latest release has no tag_name")

    latest_sha = _resolve_tag_commit(tag_name)
    compare_url = (
        f"{COMPARE_API}/{urllib.parse.quote(current_build_sha, safe='')}"
        f"...{urllib.parse.quote(latest_sha, safe='')}"
    )
    comparison = _read_json(compare_url)
    ahead_by = comparison.get("ahead_by", 0)
    has_update = (
        comparison.get("status") == "ahead"
        and isinstance(ahead_by, int)
        and ahead_by > 0
    )

    page_url = release.get("html_url")
    if not isinstance(page_url, str) or not page_url.startswith("https://github.com/"):
        page_url = RELEASE_PAGE_URL

    release_name = release.get("name")
    label = release_name if isinstance(release_name, str) and release_name else tag_name
    display_version = f"{label} ({latest_sha[:7]})"
    return has_update, display_version, page_url, ""


class UpdateChecker(QThread):
    """Runs the release check off the GUI thread and emits a Qt-safe result."""

    result_ready = Signal(bool, str, str, str)

    def run(self) -> None:
        try:
            result = check_for_update()
        except Exception:
            logger.debug("VO-SE update check failed", exc_info=True)
            result = (False, "", RELEASE_PAGE_URL, "")
        self.result_ready.emit(*result)
