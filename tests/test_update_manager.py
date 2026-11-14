from __future__ import annotations

import hashlib
import threading

import pytest

from modules.updater import update_manager


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def make_release(name: str, *, digest: str | None = None):
    return {
        "name": "VO-SE PRO test",
        "tag_name": "latest",
        "assets": [
            {
                "name": name,
                "browser_download_url": (
                    "https://github.com/nebula-INE/VO-SE_vocal/"
                    f"releases/download/latest/{name}"
                ),
                "size": 123,
                "digest": digest or ("sha256:" + "a" * 64),
            }
        ],
    }


def test_platform_asset_selection_for_supported_targets(monkeypatch):
    monkeypatch.setattr(update_manager.sys, "platform", "linux")

    assert update_manager.platform_asset_name("Windows", "AMD64") == "VO-SE_vocal_Win.exe"
    assert (
        update_manager.platform_asset_name("Darwin", "arm64")
        == "VO-SE_vocal_Mac_AppleSilicon_update.zip"
    )
    assert (
        update_manager.platform_asset_name("Darwin", "x86_64")
        == "VO-SE_vocal_Mac_Intel_update.zip"
    )
    assert (
        update_manager.platform_asset_name("Linux", "x86_64")
        == "VO-SE_vocal_Linux_x86_64.tar.gz"
    )


def test_debian_installation_selects_deb_asset(monkeypatch):
    monkeypatch.setattr(update_manager.sys, "platform", "linux")

    name = update_manager.platform_asset_name(
        "Linux",
        "x86_64",
        "/opt/vo-se-vocal/VO-SE_vocal_Linux",
    )

    assert name == "vo-se-vocal_amd64.deb"


@pytest.mark.parametrize(
    ("system", "machine"),
    [("Windows", "arm64"), ("Darwin", "powerpc"), ("Linux", "aarch64"), ("FreeBSD", "x86_64")],
)
def test_platform_asset_selection_rejects_unsupported_platforms(system, machine):
    with pytest.raises(update_manager.UpdateError):
        update_manager.platform_asset_name(system, machine)


def test_select_release_asset_requires_matching_asset_and_digest():
    asset = update_manager.select_release_asset(
        make_release("VO-SE_vocal_Win.exe"),
        system="Windows",
        machine="x86_64",
    )

    assert asset.name == "VO-SE_vocal_Win.exe"
    assert asset.sha256 == "a" * 64
    assert asset.size == 123
    assert asset.version == "VO-SE PRO test"

    with pytest.raises(update_manager.UpdateError, match="SHA-256"):
        update_manager.select_release_asset(
            make_release("VO-SE_vocal_Win.exe", digest=None)
            | {"assets": [{
                "name": "VO-SE_vocal_Win.exe",
                "browser_download_url": "https://github.com/nebula-INE/VO-SE_vocal/releases/download/latest/file.exe",
                "size": 123,
            }]},
            system="Windows",
            machine="x86_64",
        )


def test_select_release_asset_rejects_untrusted_url_and_missing_file():
    release = make_release("VO-SE_vocal_Win.exe")
    release["assets"][0]["browser_download_url"] = "https://example.invalid/file.exe"
    with pytest.raises(update_manager.UpdateError, match="ダウンロード先"):
        update_manager.select_release_asset(release, "Windows", "x86_64")

    with pytest.raises(update_manager.UpdateError, match="ありません"):
        update_manager.select_release_asset(
            {"assets": []}, "Windows", "x86_64"
        )


def test_download_release_asset_verifies_size_and_sha256(monkeypatch, tmp_path):
    payload = b"release package bytes"
    asset = update_manager.ReleaseAsset(
        name="VO-SE_vocal_Win.exe",
        download_url=(
            "https://github.com/nebula-INE/VO-SE_vocal/releases/download/"
            "latest/VO-SE_vocal_Win.exe"
        ),
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        version="test",
    )
    monkeypatch.setattr(
        update_manager.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )
    updates = []

    destination = update_manager.download_release_asset(
        asset,
        destination_dir=tmp_path,
        progress_callback=lambda percent, label: updates.append((percent, label)),
    )

    assert destination.read_bytes() == payload
    assert updates[-1][0] == 100
    assert not list(tmp_path.glob("*.part"))


def test_download_release_asset_rejects_digest_mismatch(monkeypatch, tmp_path):
    payload = b"tampered package"
    asset = update_manager.ReleaseAsset(
        name="VO-SE_vocal_Win.exe",
        download_url=(
            "https://github.com/nebula-INE/VO-SE_vocal/releases/download/"
            "latest/VO-SE_vocal_Win.exe"
        ),
        size=len(payload),
        sha256="0" * 64,
        version="test",
    )
    monkeypatch.setattr(
        update_manager.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )

    with pytest.raises(update_manager.UpdateError, match="SHA-256"):
        update_manager.download_release_asset(asset, destination_dir=tmp_path)

    assert not (tmp_path / asset.name).exists()
    assert not list(tmp_path.glob("*.part"))


def test_download_release_asset_rejects_size_mismatch(monkeypatch, tmp_path):
    payload = b"short"
    asset = update_manager.ReleaseAsset(
        name="VO-SE_vocal_Win.exe",
        download_url=(
            "https://github.com/nebula-INE/VO-SE_vocal/releases/download/"
            "latest/VO-SE_vocal_Win.exe"
        ),
        size=len(payload) + 1,
        sha256=hashlib.sha256(payload).hexdigest(),
        version="test",
    )
    monkeypatch.setattr(
        update_manager.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )

    with pytest.raises(update_manager.UpdateError, match="サイズが一致"):
        update_manager.download_release_asset(asset, destination_dir=tmp_path)


def test_download_release_asset_honors_cancellation(monkeypatch, tmp_path):
    payload = b"package"
    asset = update_manager.ReleaseAsset(
        name="VO-SE_vocal_Win.exe",
        download_url=(
            "https://github.com/nebula-INE/VO-SE_vocal/releases/download/"
            "latest/VO-SE_vocal_Win.exe"
        ),
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        version="test",
    )
    monkeypatch.setattr(
        update_manager.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(update_manager.UpdateCancelledError):
        update_manager.download_release_asset(
            asset,
            destination_dir=tmp_path,
            cancel_event=cancelled,
        )
