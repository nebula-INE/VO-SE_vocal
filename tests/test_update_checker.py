from __future__ import annotations

import json
from urllib.request import Request

from modules.updater import update_checker


class FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._data


def install_fake_api(monkeypatch, current_sha, latest_sha, status="ahead", ahead_by=3):
    release = {
        "tag_name": "latest",
        "name": "VO-SE PRO (Official Edition)",
        "html_url": "https://github.com/nebula-INE/VO-SE_vocal/releases/tag/latest",
    }
    ref = {"object": {"type": "commit", "sha": latest_sha}}
    comparison = {"status": status, "ahead_by": ahead_by}
    responses = {
        update_checker.LATEST_RELEASE_API: release,
        f"{update_checker.TAG_REF_API}/latest": ref,
        f"{update_checker.COMPARE_API}/{current_sha}...{latest_sha}": comparison,
    }
    requested = []

    def fake_urlopen(request, timeout):
        assert isinstance(request, Request)
        requested.append(request.full_url)
        assert timeout == update_checker.REQUEST_TIMEOUT_SECONDS
        return FakeResponse(responses[request.full_url])

    monkeypatch.setattr(update_checker.urllib.request, "urlopen", fake_urlopen)
    return requested


def test_check_for_update_detects_release_ahead(monkeypatch):
    current = "1" * 40
    latest = "2" * 40
    requested = install_fake_api(monkeypatch, current, latest)

    has_update, label, page_url, download_url = update_checker.check_for_update(current)

    assert has_update is True
    assert latest[:7] in label
    assert page_url.endswith("/releases/tag/latest")
    assert download_url == ""
    assert len(requested) == 3


def test_check_for_update_skips_same_or_older_release(monkeypatch):
    current = "1" * 40
    latest = "2" * 40
    install_fake_api(
        monkeypatch,
        current,
        latest,
        status="behind",
        ahead_by=0,
    )

    has_update, _label, _page_url, _download_url = update_checker.check_for_update(current)

    assert has_update is False


def test_check_for_update_skips_development_build_without_network(monkeypatch):
    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("development builds must not call the GitHub API")

    monkeypatch.setattr(update_checker.urllib.request, "urlopen", unexpected_request)

    result = update_checker.check_for_update("development")

    assert result[0] is False


def test_resolve_annotated_tag(monkeypatch):
    annotated_sha = "a" * 40
    commit_sha = "b" * 40
    responses = {
        f"{update_checker.TAG_REF_API}/v1.2.3": {
            "object": {"type": "tag", "sha": annotated_sha}
        },
        f"https://api.github.com/repos/{update_checker.GITHUB_REPOSITORY}/git/tags/{annotated_sha}": {
            "object": {"type": "commit", "sha": commit_sha}
        },
    }

    def fake_urlopen(request, timeout):
        return FakeResponse(responses[request.full_url])

    monkeypatch.setattr(update_checker.urllib.request, "urlopen", fake_urlopen)

    assert update_checker._resolve_tag_commit("v1.2.3") == commit_sha
