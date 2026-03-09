from __future__ import annotations

import json
from pathlib import Path

import pytest

from peon_relay.cesp import CESPManager, Pack, load_packs


@pytest.fixture
def sample_pack(tmp_path: Path) -> Pack:
    sounds_dir = tmp_path / "sounds"
    sounds_dir.mkdir()

    files = []
    for name in ["a.wav", "b.wav", "c.wav"]:
        p = sounds_dir / name
        p.write_bytes(b"RIFF")
        files.append(p)

    return Pack(
        name="test",
        display_name="Test Pack",
        base_dir=tmp_path,
        categories={
            "session.start": [files[0], files[1]],
            "task.complete": [files[2]],
        },
        aliases={"task.done": "task.complete"},
    )


class TestPack:
    def test_resolve_direct_category(self, sample_pack: Pack):
        assert sample_pack.resolve_category("session.start") == "session.start"

    def test_resolve_alias(self, sample_pack: Pack):
        assert sample_pack.resolve_category("task.done") == "task.complete"

    def test_resolve_missing_returns_none(self, sample_pack: Pack):
        assert sample_pack.resolve_category("nonexistent") is None

    def test_info(self, sample_pack: Pack):
        info = sample_pack.info(is_active=True)
        assert info.name == "test"
        assert info.sound_count == 3
        assert info.active is True
        assert "session.start" in info.categories


class TestCESPManager:
    def test_pick_sound_returns_path(self, sample_pack: Pack):
        manager = CESPManager(
            packs={"test": sample_pack}, active_pack_name="test"
        )
        result = manager.pick_sound("task.complete")
        assert result is not None
        assert result.name == "c.wav"

    def test_pick_sound_via_alias(self, sample_pack: Pack):
        manager = CESPManager(
            packs={"test": sample_pack}, active_pack_name="test"
        )
        result = manager.pick_sound("task.done")
        assert result is not None
        assert result.name == "c.wav"

    def test_pick_sound_missing_category(self, sample_pack: Pack):
        manager = CESPManager(
            packs={"test": sample_pack}, active_pack_name="test"
        )
        assert manager.pick_sound("nonexistent") is None

    def test_pick_sound_no_repeat(self, sample_pack: Pack):
        manager = CESPManager(
            packs={"test": sample_pack}, active_pack_name="test"
        )
        results = set()
        for _ in range(20):
            r = manager.pick_sound("session.start")
            results.add(r.name)

        # With 2 sounds available, both should be picked over 20 tries
        assert len(results) == 2

    def test_pick_sound_no_consecutive_repeats(self, sample_pack: Pack):
        manager = CESPManager(
            packs={"test": sample_pack}, active_pack_name="test"
        )
        last = None
        for _ in range(20):
            r = manager.pick_sound("session.start")
            if last is not None:
                assert r != last, "Should not play the same sound consecutively"
            last = r

    def test_pick_sound_no_active_pack(self):
        manager = CESPManager(packs={}, active_pack_name="missing")
        assert manager.pick_sound("session.start") is None

    def test_list_packs(self, sample_pack: Pack):
        manager = CESPManager(
            packs={"test": sample_pack}, active_pack_name="test"
        )
        packs = manager.list_packs()
        assert len(packs) == 1
        assert packs[0].active is True


class TestLoadPacks:
    def test_load_from_directory(self, tmp_path: Path):
        pack_dir = tmp_path / "peon"
        pack_dir.mkdir()
        sounds_dir = pack_dir / "sounds"
        sounds_dir.mkdir()

        sound_file = sounds_dir / "ready.wav"
        sound_file.write_bytes(b"RIFF")

        manifest = {
            "cesp_version": "1.0",
            "name": "peon",
            "display_name": "Warcraft Peon",
            "categories": {
                "session.start": {
                    "sounds": [{"file": "ready.wav", "label": "Ready"}]
                },
            },
            "category_aliases": {"task.done": "session.start"},
        }
        (pack_dir / "openpeon.json").write_text(json.dumps(manifest))

        manager = load_packs(str(tmp_path), "peon")
        assert "peon" in manager.packs
        assert manager.active_pack is not None
        result = manager.pick_sound("session.start")
        assert result is not None

    def test_load_nonexistent_dir(self):
        manager = load_packs("/nonexistent/path", "peon")
        assert len(manager.packs) == 0
