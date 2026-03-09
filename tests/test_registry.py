from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest

from peon_relay.config import RegistryConfig
from peon_relay.registry import (
    InstallResult,
    RegistryClient,
    RegistryIndex,
    RegistryPack,
)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

SAMPLE_MANIFEST = {
    "cesp_version": "1.0",
    "name": "testpack",
    "display_name": "Test Pack",
    "categories": {
        "session.start": {"sounds": [{"file": "ready.wav"}]},
    },
}

SAMPLE_MANIFEST_BYTES = json.dumps(SAMPLE_MANIFEST).encode()
SAMPLE_MANIFEST_SHA = hashlib.sha256(SAMPLE_MANIFEST_BYTES).hexdigest()


def _make_registry_pack(
    name: str = "testpack",
    source_repo: str = "TestOrg/test-packs",
    source_ref: str = "v1.0.0",
    source_path: str = "testpack",
    trust_tier: str = "official",
    categories: list[str] | None = None,
    tags: list[str] | None = None,
    manifest_sha256: str = "",
) -> dict:
    return {
        "name": name,
        "display_name": f"Display {name}",
        "version": "1.0.0",
        "description": f"A {name} pack",
        "author": {"name": "tester", "github": "tester"},
        "trust_tier": trust_tier,
        "categories": categories or ["session.start", "task.complete"],
        "language": "en",
        "license": "CC-BY-NC-4.0",
        "sound_count": 1,
        "total_size_bytes": 1024,
        "source_repo": source_repo,
        "source_ref": source_ref,
        "source_path": source_path,
        "manifest_sha256": manifest_sha256,
        "tags": tags or ["test"],
        "preview_sounds": [],
        "added": "2026-01-01",
        "updated": "2026-01-01",
    }


def _make_registry_json(*packs: dict) -> dict:
    return {"version": 1, "packs": list(packs)}


def _make_tarball(
    repo_name: str,
    ref: str,
    source_path: str,
    files: dict[str, bytes],
) -> bytes:
    """Build an in-memory tarball matching GitHub's archive format."""
    ref_stripped = ref.lstrip("v")
    top_dir = f"{repo_name}-{ref_stripped}"

    if source_path and source_path != ".":
        prefix = f"{top_dir}/{source_path}"
    else:
        prefix = top_dir

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=f"{prefix}/{name}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    buf.seek(0)
    return buf.read()


def _mock_transport(responses: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Create a mock transport that returns canned responses by URL pattern."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for pattern, response in responses.items():
            if pattern in url:
                return response
        return httpx.Response(404, text="Not found")

    return httpx.MockTransport(handler)


@pytest.fixture
def registry_config() -> RegistryConfig:
    return RegistryConfig(
        urls=["https://test-registry.example.com/index.json"],
        cache_ttl_seconds=300,
        download_timeout_seconds=30,
    )


# ---------------------------------------------------------------------------
# RegistryIndex parsing
# ---------------------------------------------------------------------------


class TestRegistryIndex:
    def test_parse_index(self):
        data = _make_registry_json(
            _make_registry_pack("pack_a"),
            _make_registry_pack("pack_b"),
        )
        index = RegistryIndex.model_validate(data)
        assert index.version == 1
        assert len(index.packs) == 2
        assert index.packs[0].name == "pack_a"

    def test_parse_pack_fields(self):
        pack_data = _make_registry_pack(
            "mypack",
            trust_tier="community",
            categories=["session.start"],
            tags=["gaming", "fun"],
        )
        pack = RegistryPack.model_validate(pack_data)
        assert pack.name == "mypack"
        assert pack.trust_tier == "community"
        assert pack.categories == ["session.start"]
        assert pack.tags == ["gaming", "fun"]
        assert pack.author.name == "tester"


# ---------------------------------------------------------------------------
# list_available
# ---------------------------------------------------------------------------


class TestListAvailable:
    @pytest.mark.asyncio
    async def test_basic_listing(self, tmp_path: Path, registry_config: RegistryConfig):
        index_json = _make_registry_json(
            _make_registry_pack("alpha"),
            _make_registry_pack("beta"),
        )
        transport = _mock_transport({
            "index.json": httpx.Response(200, json=index_json),
        })
        client = RegistryClient(registry_config, str(tmp_path))
        # Monkeypatch to use mock transport
        client.fetch_index = _make_fetch_index(transport, registry_config)

        results = await client.list_available()
        assert len(results) == 2
        names = {p.name for p in results}
        assert names == {"alpha", "beta"}

    @pytest.mark.asyncio
    async def test_filter_by_search(self, tmp_path: Path, registry_config: RegistryConfig):
        index_json = _make_registry_json(
            _make_registry_pack("warcraft", tags=["gaming", "rts"]),
            _make_registry_pack("arnold", tags=["movies", "action"]),
        )
        transport = _mock_transport({
            "index.json": httpx.Response(200, json=index_json),
        })
        client = RegistryClient(registry_config, str(tmp_path))
        client.fetch_index = _make_fetch_index(transport, registry_config)

        results = await client.list_available(search="gaming")
        assert len(results) == 1
        assert results[0].name == "warcraft"

    @pytest.mark.asyncio
    async def test_filter_by_category(self, tmp_path: Path, registry_config: RegistryConfig):
        index_json = _make_registry_json(
            _make_registry_pack("a", categories=["session.start"]),
            _make_registry_pack("b", categories=["task.complete"]),
        )
        transport = _mock_transport({
            "index.json": httpx.Response(200, json=index_json),
        })
        client = RegistryClient(registry_config, str(tmp_path))
        client.fetch_index = _make_fetch_index(transport, registry_config)

        results = await client.list_available(category="task.complete")
        assert len(results) == 1
        assert results[0].name == "b"

    @pytest.mark.asyncio
    async def test_filter_by_trust_tier(self, tmp_path: Path, registry_config: RegistryConfig):
        index_json = _make_registry_json(
            _make_registry_pack("official_pack", trust_tier="official"),
            _make_registry_pack("community_pack", trust_tier="community"),
        )
        transport = _mock_transport({
            "index.json": httpx.Response(200, json=index_json),
        })
        client = RegistryClient(registry_config, str(tmp_path))
        client.fetch_index = _make_fetch_index(transport, registry_config)

        results = await client.list_available(trust_tier="official")
        assert len(results) == 1
        assert results[0].name == "official_pack"

    @pytest.mark.asyncio
    async def test_deduplication_across_registries(
        self, tmp_path: Path
    ):
        config = RegistryConfig(
            urls=[
                "https://registry1.example.com/index.json",
                "https://registry2.example.com/index.json",
            ]
        )
        index1 = _make_registry_json(_make_registry_pack("shared", trust_tier="official"))
        index2 = _make_registry_json(_make_registry_pack("shared", trust_tier="community"))

        transport = _mock_transport({
            "registry1": httpx.Response(200, json=index1),
            "registry2": httpx.Response(200, json=index2),
        })
        client = RegistryClient(config, str(tmp_path))
        client.fetch_index = _make_fetch_index(transport, config)

        results = await client.list_available()
        assert len(results) == 1
        # First registry wins
        assert results[0].trust_tier == "official"


def _make_fetch_index(transport: httpx.MockTransport, config: RegistryConfig):
    """Create a replacement fetch_index that uses the mock transport."""

    async def fetch_index(url: str) -> RegistryIndex:
        async with httpx.AsyncClient(transport=transport) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            return RegistryIndex.model_validate(resp.json())

    return fetch_index


# ---------------------------------------------------------------------------
# install_pack
# ---------------------------------------------------------------------------


class TestInstallPack:
    @pytest.mark.asyncio
    async def test_install_success(self, tmp_path: Path, registry_config: RegistryConfig):
        tarball = _make_tarball(
            "test-packs",
            "v1.0.0",
            "testpack",
            {
                "openpeon.json": SAMPLE_MANIFEST_BYTES,
                "sounds/ready.wav": b"RIFF_FAKE_WAV",
            },
        )
        index_json = _make_registry_json(
            _make_registry_pack(
                "testpack",
                manifest_sha256=SAMPLE_MANIFEST_SHA,
            )
        )
        transport = _mock_transport({
            "index.json": httpx.Response(200, json=index_json),
            "tar.gz": httpx.Response(200, content=tarball),
        })

        client = RegistryClient(registry_config, str(tmp_path))
        client.fetch_index = _make_fetch_index(transport, registry_config)
        # Also patch the actual HTTP download in install
        client._make_http_client = lambda timeout: httpx.AsyncClient(transport=transport)

        result = await _install_with_mock(client, "testpack", transport)
        assert result.success
        assert (tmp_path / "testpack" / "openpeon.json").exists()
        assert (tmp_path / "testpack" / "sounds" / "ready.wav").exists()

    @pytest.mark.asyncio
    async def test_install_sha_mismatch(self, tmp_path: Path, registry_config: RegistryConfig):
        tarball = _make_tarball(
            "test-packs",
            "v1.0.0",
            "testpack",
            {"openpeon.json": b'{"name": "tampered"}'},
        )
        index_json = _make_registry_json(
            _make_registry_pack(
                "testpack",
                manifest_sha256="deadbeef" * 8,
            )
        )
        transport = _mock_transport({
            "index.json": httpx.Response(200, json=index_json),
            "tar.gz": httpx.Response(200, content=tarball),
        })

        client = RegistryClient(registry_config, str(tmp_path))
        client.fetch_index = _make_fetch_index(transport, registry_config)

        result = await _install_with_mock(client, "testpack", transport)
        assert not result.success
        assert "SHA256 mismatch" in result.message
        # Should clean up on failure
        assert not (tmp_path / "testpack").exists()

    @pytest.mark.asyncio
    async def test_install_pack_not_found(self, tmp_path: Path, registry_config: RegistryConfig):
        index_json = _make_registry_json()  # empty registry
        transport = _mock_transport({
            "index.json": httpx.Response(200, json=index_json),
        })

        client = RegistryClient(registry_config, str(tmp_path))
        client.fetch_index = _make_fetch_index(transport, registry_config)

        result = await client.install_pack("nonexistent")
        assert not result.success
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_install_no_manifest_in_archive(
        self, tmp_path: Path, registry_config: RegistryConfig
    ):
        tarball = _make_tarball(
            "test-packs",
            "v1.0.0",
            "testpack",
            {"sounds/ready.wav": b"RIFF"},  # no openpeon.json
        )
        index_json = _make_registry_json(_make_registry_pack("testpack"))
        transport = _mock_transport({
            "index.json": httpx.Response(200, json=index_json),
            "tar.gz": httpx.Response(200, content=tarball),
        })

        client = RegistryClient(registry_config, str(tmp_path))
        client.fetch_index = _make_fetch_index(transport, registry_config)

        result = await _install_with_mock(client, "testpack", transport)
        assert not result.success
        assert "openpeon.json" in result.message


async def _install_with_mock(
    client: RegistryClient, pack_name: str, transport: httpx.MockTransport
) -> InstallResult:
    """Run install_pack with mocked HTTP for the tarball download."""
    import unittest.mock as mock

    original = client._install_pack_inner

    async def patched(name: str) -> InstallResult:
        # Patch httpx.AsyncClient used inside _install_pack_inner
        with mock.patch("peon_relay.registry.httpx.AsyncClient") as mock_cls:
            mock_client = httpx.AsyncClient(transport=transport)
            mock_cls.return_value = mock_client
            # Need to make __aenter__ and __aexit__ work
            mock_cls.return_value.__aenter__ = mock_client.__aenter__
            mock_cls.return_value.__aexit__ = mock_client.__aexit__
            mock_cls.return_value = mock_client
            mock_cls.return_value.__aenter__ = lambda self: mock_client.__aenter__()
            mock_cls.return_value.__aexit__ = lambda self, *a: mock_client.__aexit__(*a)

            # Simpler approach: just patch at context manager level
            pass

        return await original(name)

    # Simpler: monkeypatch httpx at module level
    import peon_relay.registry as reg_mod

    orig_client_cls = httpx.AsyncClient

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    reg_mod.httpx.AsyncClient = MockAsyncClient  # type: ignore
    try:
        return await client.install_pack(pack_name)
    finally:
        reg_mod.httpx.AsyncClient = orig_client_cls  # type: ignore


# ---------------------------------------------------------------------------
# uninstall_pack
# ---------------------------------------------------------------------------


class TestUninstallPack:
    def test_uninstall_existing(self, tmp_path: Path, registry_config: RegistryConfig):
        pack_dir = tmp_path / "mypack"
        pack_dir.mkdir()
        (pack_dir / "openpeon.json").write_text("{}")

        client = RegistryClient(registry_config, str(tmp_path))
        assert client.uninstall_pack("mypack") is True
        assert not pack_dir.exists()

    def test_uninstall_nonexistent(self, tmp_path: Path, registry_config: RegistryConfig):
        client = RegistryClient(registry_config, str(tmp_path))
        assert client.uninstall_pack("nopack") is False


# ---------------------------------------------------------------------------
# installed_packs
# ---------------------------------------------------------------------------


class TestInstalledPacks:
    def test_lists_installed(self, tmp_path: Path, registry_config: RegistryConfig):
        for name in ["pack_a", "pack_b"]:
            d = tmp_path / name
            d.mkdir()
            (d / "openpeon.json").write_text("{}")

        # Also a directory without manifest (should be excluded)
        (tmp_path / "not_a_pack").mkdir()

        client = RegistryClient(registry_config, str(tmp_path))
        assert client.installed_packs() == {"pack_a", "pack_b"}

    def test_empty_dir(self, tmp_path: Path, registry_config: RegistryConfig):
        client = RegistryClient(registry_config, str(tmp_path))
        assert client.installed_packs() == set()
