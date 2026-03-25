"""Configuration loader for context-forge.

Runtime configuration stored in the database is the primary source of truth.
Environment variables and ``context-forge.yml`` are used for bootstrap defaults,
legacy import, and infrastructure-only settings.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class RepoConfig(BaseModel):
    name: str
    type: Literal["local", "github", "gitlab"] = "local"
    path: Optional[str] = None       # for local repos (container path)
    url: Optional[str] = None        # for remote repos
    branch: str = "main"
    language: str = "auto"
    token: Optional[str] = None      # per-repo token override


class MemoryConfig(BaseModel):
    user_id: str = "default"


class EmbeddingsConfig(BaseModel):
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    dims: int = 1536


class IndexingConfig(BaseModel):
    auto: bool = True
    schedule: str = "0 */6 * * *"
    exclude: list[str] = Field(default_factory=lambda: [
        "**/.git/**", "**/node_modules/**", "**/__pycache__/**",
        "**/*.pyc", "**/dist/**", "**/build/**", "**/.next/**",
        "**/coverage/**", "**/*.min.js", "**/*.lock", "**/package-lock.json",
    ])
    max_file_size_kb: int = 500
    chunk_size: int = 400
    chunk_overlap: int = 50


class ForgeConfig(BaseModel):
    repos: list[RepoConfig] = Field(default_factory=list)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)


class Settings(BaseSettings):
    database_url: str = "postgresql://context_forge:changeme@postgres:5432/context_forge"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    embeddings_provider: str = "openai"
    embeddings_model: str = "text-embedding-3-small"
    embeddings_dims: int = 1536
    embeddings_api_key: str = ""        # overrides provider key for embeddings
    embeddings_base_url: str = ""       # custom OpenAI-compatible endpoint
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    github_token: str = ""
    gitlab_token: str = ""
    mcp_port: int = 4000
    api_port: int = 8000
    repos_cache_dir: str = "/data/repos-cache"
    log_level: str = "INFO"
    config_path: str = "/app/context-forge.yml"
    setup_bootstrap_token: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


_settings: Optional[Settings] = None
_forge_config: Optional[ForgeConfig] = None
_runtime_forge_config: Optional[ForgeConfig] = None
_runtime_settings_overrides: dict = {}
RUNTIME_OVERRIDE_FIELDS = (
    "openai_api_key",
    "anthropic_api_key",
    "deepseek_api_key",
    "embeddings_provider",
    "embeddings_model",
    "embeddings_dims",
    "embeddings_api_key",
    "embeddings_base_url",
    "llm_provider",
    "llm_model",
    "github_token",
    "gitlab_token",
)
DEFAULT_RUNTIME_OVERRIDE_VALUES = {
    "openai_api_key": "",
    "anthropic_api_key": "",
    "deepseek_api_key": "",
    "embeddings_provider": "openai",
    "embeddings_model": "text-embedding-3-small",
    "embeddings_dims": 1536,
    "embeddings_api_key": "",
    "embeddings_base_url": "",
    "llm_provider": "openai",
    "llm_model": "gpt-4o-mini",
    "github_token": "",
    "gitlab_token": "",
}


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        # Override embeddings dims from env
        dims_env = os.getenv("EMBEDDINGS_DIMS")
        if dims_env:
            _settings.embeddings_dims = int(dims_env)
    # Apply runtime overrides loaded from DB (if configured).
    for key, value in _runtime_settings_overrides.items():
        if value is None:
            continue
        if hasattr(_settings, key):
            setattr(_settings, key, value)
    return _settings


def get_forge_config() -> ForgeConfig:
    global _forge_config, _runtime_forge_config
    if _runtime_forge_config is not None:
        return _runtime_forge_config
    if _forge_config is None:
        _forge_config = _load_forge_config()
    return _forge_config


def reload_forge_config() -> ForgeConfig:
    """Reload from disk (useful after UI edits)."""
    global _forge_config
    _forge_config = _load_forge_config()
    return _forge_config


def set_forge_config(config: ForgeConfig) -> None:
    """Write bootstrap config to disk and mirror it in memory."""
    global _forge_config, _runtime_forge_config
    _forge_config = config
    _runtime_forge_config = config
    settings = get_settings()
    path = Path(settings.config_path)
    # Write to YAML file
    with path.open("w") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, sort_keys=False)


def set_runtime_config(forge_config: ForgeConfig, settings_overrides: Optional[dict] = None) -> None:
    """Apply runtime configuration loaded from DB."""
    global _runtime_forge_config, _runtime_settings_overrides
    _runtime_forge_config = forge_config
    _runtime_settings_overrides = settings_overrides or {}


def clear_runtime_config() -> None:
    """Clear DB-backed runtime overrides and use file/env config."""
    global _runtime_forge_config, _runtime_settings_overrides
    _runtime_forge_config = None
    _runtime_settings_overrides = {}


def get_runtime_settings_overrides() -> dict[str, object]:
    """Return the currently effective runtime-overridable settings."""
    settings = get_settings()
    return {field: getattr(settings, field) for field in RUNTIME_OVERRIDE_FIELDS}


def get_non_default_runtime_settings_overrides() -> dict[str, object]:
    """Return runtime-overridable settings that differ from built-in defaults."""
    current = get_runtime_settings_overrides()
    return {
        field: value
        for field, value in current.items()
        if value != DEFAULT_RUNTIME_OVERRIDE_VALUES[field]
    }


def has_file_forge_config() -> bool:
    """Return whether the bootstrap YAML file exists."""
    settings = get_settings()
    return Path(settings.config_path).exists()


def has_meaningful_file_forge_config() -> bool:
    """Return whether the bootstrap YAML contains non-default configuration."""
    if not has_file_forge_config():
        return False
    return _load_forge_config() != ForgeConfig()


def _load_forge_config() -> ForgeConfig:
    settings = get_settings()
    path = Path(settings.config_path)
    if not path.exists():
        return ForgeConfig()
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    config = ForgeConfig(**data)
    # Apply token overrides from environment
    settings = get_settings()
    for repo in config.repos:
        if repo.token:
            continue
        if repo.type == "github" and settings.github_token:
            repo.token = settings.github_token
        elif repo.type == "gitlab" and settings.gitlab_token:
            repo.token = settings.gitlab_token
    return config
