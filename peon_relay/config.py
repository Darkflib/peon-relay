from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 9876


class AudioConfig(BaseModel):
    enabled: bool = True
    pack_dir: str = "sounds"
    active_pack: str = "peon"
    client_packs: dict[str, str] = {}  # client IP -> pack name
    volume: float = 0.7
    mute: bool = False
    disabled_categories: list[str] = []
    debounce_ms: int = 500


class LogConfig(BaseModel):
    enabled: bool = True
    level: str = "INFO"


def _yaml_source() -> dict:
    config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PEON_",
        env_nested_delimiter="__",
    )

    server: ServerConfig = ServerConfig()
    audio: AudioConfig = AudioConfig()
    log: LogConfig = LogConfig()

    @classmethod
    def load(cls) -> Settings:
        yaml_data = _yaml_source()
        return cls(**yaml_data)
