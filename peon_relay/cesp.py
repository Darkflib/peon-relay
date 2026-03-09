from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger()


@dataclass
class PackInfo:
    name: str
    display_name: str
    categories: list[str]
    sound_count: int
    active: bool


@dataclass
class Pack:
    name: str
    display_name: str
    base_dir: Path
    categories: dict[str, list[Path]]
    aliases: dict[str, str]

    def resolve_category(self, category: str) -> str | None:
        if category in self.categories:
            return category
        mapped = self.aliases.get(category)
        if mapped and mapped in self.categories:
            return mapped
        return None

    def info(self, is_active: bool) -> PackInfo:
        return PackInfo(
            name=self.name,
            display_name=self.display_name,
            categories=list(self.categories.keys()),
            sound_count=sum(len(s) for s in self.categories.values()),
            active=is_active,
        )


@dataclass
class CESPManager:
    packs: dict[str, Pack] = field(default_factory=dict)
    active_pack_name: str = ""
    _last_played: dict[str, Path] = field(default_factory=dict)

    @property
    def active_pack(self) -> Pack | None:
        return self.packs.get(self.active_pack_name)

    def pick_sound(
        self, category: str, pack_name: str | None = None
    ) -> Path | None:
        if pack_name:
            pack = self.packs.get(pack_name)
        else:
            pack = self.active_pack
        if pack is None:
            return None

        resolved = pack.resolve_category(category)
        if resolved is None:
            return None

        sounds = pack.categories[resolved]
        if not sounds:
            return None

        key = f"{pack.name}:{resolved}"
        last = self._last_played.get(key)

        if len(sounds) > 1 and last is not None:
            candidates = [s for s in sounds if s != last]
        else:
            candidates = sounds

        chosen = random.choice(candidates)
        self._last_played[key] = chosen
        return chosen

    def list_packs(self) -> list[PackInfo]:
        return [
            pack.info(is_active=(name == self.active_pack_name))
            for name, pack in self.packs.items()
        ]


def _resolve_sound_path(base_dir: Path, file_path: str) -> Path:
    if "/" not in file_path and "\\" not in file_path:
        return base_dir / "sounds" / file_path
    return base_dir / file_path


def load_packs(pack_dir: str, active_pack: str) -> CESPManager:
    pack_path = Path(pack_dir)
    manager = CESPManager(active_pack_name=active_pack)

    if not pack_path.is_dir():
        logger.warning("pack_dir_not_found", path=str(pack_path))
        return manager

    for manifest_path in pack_path.glob("*/openpeon.json"):
        try:
            with open(manifest_path) as f:
                data = json.load(f)

            base_dir = manifest_path.parent
            name = data.get("name", base_dir.name)
            display_name = data.get("display_name", name)
            cesp_version = data.get("cesp_version", "unknown")

            if cesp_version != "1.0":
                logger.warning(
                    "unknown_cesp_version",
                    pack=name,
                    version=cesp_version,
                )

            categories: dict[str, list[Path]] = {}
            for cat_name, cat_data in data.get("categories", {}).items():
                sounds = []
                for entry in cat_data.get("sounds", []):
                    sound_file = entry.get("file", "")
                    resolved = _resolve_sound_path(base_dir, sound_file)
                    if resolved.exists():
                        sounds.append(resolved.resolve())
                    else:
                        logger.warning(
                            "sound_file_missing",
                            pack=name,
                            category=cat_name,
                            file=str(resolved),
                        )
                categories[cat_name] = sounds

            aliases = data.get("category_aliases", {})

            pack = Pack(
                name=name,
                display_name=display_name,
                base_dir=base_dir.resolve(),
                categories=categories,
                aliases=aliases,
            )
            manager.packs[name] = pack
            logger.info(
                "pack_loaded",
                pack=name,
                categories=len(categories),
                sounds=sum(len(s) for s in categories.values()),
            )

        except Exception:
            logger.exception("pack_load_error", path=str(manifest_path))

    return manager
