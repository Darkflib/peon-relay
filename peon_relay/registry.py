from __future__ import annotations

import asyncio
import hashlib
import io
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import structlog
from pydantic import BaseModel

from peon_relay.config import RegistryConfig

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Registry models
# ---------------------------------------------------------------------------


class RegistryAuthor(BaseModel):
    name: str
    github: str = ""


class RegistryPack(BaseModel):
    name: str
    display_name: str
    version: str
    description: str = ""
    author: RegistryAuthor = RegistryAuthor(name="unknown")
    trust_tier: str = "community"
    categories: list[str] = []
    language: str = "en"
    license: str = ""
    sound_count: int = 0
    total_size_bytes: int = 0
    source_repo: str = ""
    source_ref: str = ""
    source_path: str = "."
    manifest_sha256: str = ""
    tags: list[str] = []
    preview_sounds: list[str] = []
    added: str = ""
    updated: str = ""
    quality: str | None = None


class RegistryIndex(BaseModel):
    version: int
    packs: list[RegistryPack]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class InstallResult:
    pack_name: str
    version: str
    success: bool
    message: str


# ---------------------------------------------------------------------------
# Tarball safety
# ---------------------------------------------------------------------------


def _is_safe_tar_member(member: tarfile.TarInfo, target_dir: Path) -> bool:
    member_path = Path(member.name)
    if member_path.is_absolute():
        return False
    try:
        (target_dir / member_path).resolve().relative_to(target_dir.resolve())
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Registry client
# ---------------------------------------------------------------------------


class RegistryClient:
    def __init__(self, config: RegistryConfig, pack_dir: str) -> None:
        self._config = config
        self._pack_dir = Path(pack_dir)
        self._cache: dict[str, tuple[float, RegistryIndex]] = {}
        self._install_locks: dict[str, asyncio.Lock] = {}

    # -- fetch & cache -------------------------------------------------------

    async def fetch_index(self, url: str) -> RegistryIndex:
        now = time.monotonic()
        cached = self._cache.get(url)
        if cached is not None:
            ts, index = cached
            if now - ts < self._config.cache_ttl_seconds:
                return index

        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        index = RegistryIndex.model_validate(data)
        self._cache[url] = (now, index)
        logger.debug("registry_fetched", url=url, pack_count=len(index.packs))
        return index

    # -- list / search -------------------------------------------------------

    async def list_available(
        self,
        search: str | None = None,
        category: str | None = None,
        trust_tier: str | None = None,
    ) -> list[RegistryPack]:
        seen: set[str] = set()
        results: list[RegistryPack] = []

        for url in self._config.urls:
            try:
                index = await self.fetch_index(url)
            except Exception:
                logger.warning("registry_fetch_failed", url=url)
                continue

            for pack in index.packs:
                if pack.name in seen:
                    continue
                seen.add(pack.name)

                if trust_tier and pack.trust_tier != trust_tier:
                    continue
                if category and category not in pack.categories:
                    continue
                if search:
                    needle = search.lower()
                    haystack = " ".join(
                        [
                            pack.name,
                            pack.display_name,
                            pack.description,
                            *pack.tags,
                        ]
                    ).lower()
                    if needle not in haystack:
                        continue

                results.append(pack)

        return results

    # -- install -------------------------------------------------------------

    def _get_lock(self, pack_name: str) -> asyncio.Lock:
        if pack_name not in self._install_locks:
            self._install_locks[pack_name] = asyncio.Lock()
        return self._install_locks[pack_name]

    async def install_pack(self, pack_name: str) -> InstallResult:
        lock = self._get_lock(pack_name)
        async with lock:
            return await self._install_pack_inner(pack_name)

    async def _install_pack_inner(self, pack_name: str) -> InstallResult:
        # 1. Find pack in registry
        available = await self.list_available()
        pack_meta = next((p for p in available if p.name == pack_name), None)
        if pack_meta is None:
            return InstallResult(
                pack_name=pack_name,
                version="",
                success=False,
                message=f"Pack '{pack_name}' not found in registry",
            )

        # 2. Build tarball URL
        tarball_url = (
            f"https://github.com/{pack_meta.source_repo}"
            f"/archive/refs/tags/{pack_meta.source_ref}.tar.gz"
        )

        # 3. Download tarball to temp file
        tmp_file = None
        target_dir = self._pack_dir / pack_name
        try:
            async with httpx.AsyncClient(
                timeout=self._config.download_timeout_seconds,
                follow_redirects=True,
            ) as client:
                resp = await client.get(tarball_url)
                resp.raise_for_status()

            tmp_file = tempfile.NamedTemporaryFile(
                suffix=".tar.gz", delete=False
            )
            tmp_file.write(resp.content)
            tmp_file.close()

            # 4. Extract target subdirectory
            self._pack_dir.mkdir(parents=True, exist_ok=True)

            # GitHub tarballs have a top-level dir: {repo_name}-{ref}/
            # where ref has the leading 'v' stripped if present
            repo_name = pack_meta.source_repo.split("/")[-1]
            ref_stripped = pack_meta.source_ref.lstrip("v")
            top_dir_prefix = f"{repo_name}-{ref_stripped}/"

            source_path = pack_meta.source_path.strip("/")
            if source_path and source_path != ".":
                extract_prefix = f"{top_dir_prefix}{source_path}/"
            else:
                extract_prefix = top_dir_prefix

            # Clean up existing pack dir if present
            if target_dir.exists():
                shutil.rmtree(target_dir)
            target_dir.mkdir(parents=True)

            with tarfile.open(tmp_file.name, "r:gz") as tar:
                for member in tar.getmembers():
                    if not member.name.startswith(extract_prefix):
                        continue
                    if not _is_safe_tar_member(member, target_dir):
                        logger.warning(
                            "unsafe_tar_member", member=member.name
                        )
                        continue

                    # Strip the prefix so files land directly in target_dir
                    rel_path = member.name[len(extract_prefix) :]
                    if not rel_path:
                        continue

                    member_copy = tarfile.TarInfo(name=rel_path)
                    member_copy.size = member.size
                    member_copy.mode = member.mode

                    if member.isdir():
                        (target_dir / rel_path).mkdir(
                            parents=True, exist_ok=True
                        )
                    elif member.isfile():
                        file_obj = tar.extractfile(member)
                        if file_obj is None:
                            continue
                        dest = target_dir / rel_path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with open(dest, "wb") as out:
                            shutil.copyfileobj(file_obj, out)

            # 5. Verify manifest SHA256
            manifest_path = target_dir / "openpeon.json"
            if not manifest_path.exists():
                shutil.rmtree(target_dir)
                return InstallResult(
                    pack_name=pack_name,
                    version=pack_meta.version,
                    success=False,
                    message="Extracted archive does not contain openpeon.json",
                )

            if pack_meta.manifest_sha256:
                actual_sha = hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest()
                if actual_sha != pack_meta.manifest_sha256:
                    shutil.rmtree(target_dir)
                    return InstallResult(
                        pack_name=pack_name,
                        version=pack_meta.version,
                        success=False,
                        message=(
                            f"SHA256 mismatch: expected "
                            f"{pack_meta.manifest_sha256}, got {actual_sha}"
                        ),
                    )

            logger.info(
                "pack_installed",
                pack=pack_name,
                version=pack_meta.version,
            )
            return InstallResult(
                pack_name=pack_name,
                version=pack_meta.version,
                success=True,
                message="Pack installed successfully",
            )

        except httpx.HTTPStatusError as exc:
            return InstallResult(
                pack_name=pack_name,
                version=pack_meta.version,
                success=False,
                message=f"Download failed: HTTP {exc.response.status_code}",
            )
        except Exception as exc:
            # Clean up partial install
            if target_dir.exists():
                shutil.rmtree(target_dir)
            return InstallResult(
                pack_name=pack_name,
                version=pack_meta.version,
                success=False,
                message=f"Install failed: {exc}",
            )
        finally:
            if tmp_file is not None:
                Path(tmp_file.name).unlink(missing_ok=True)

    # -- uninstall -----------------------------------------------------------

    def uninstall_pack(self, pack_name: str) -> bool:
        pack_path = self._pack_dir / pack_name
        if not pack_path.is_dir():
            return False
        shutil.rmtree(pack_path)
        logger.info("pack_uninstalled", pack=pack_name)
        return True

    # -- helpers -------------------------------------------------------------

    def installed_packs(self) -> set[str]:
        if not self._pack_dir.is_dir():
            return set()
        return {
            d.name
            for d in self._pack_dir.iterdir()
            if d.is_dir() and (d / "openpeon.json").exists()
        }
