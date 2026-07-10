"""Camera configuration: YAML topology + env-var-only secrets.

Two-stage validation (Pattern 2, 01-RESEARCH.md):
  1. `validate_yaml_shape()` rejects any `password:` key present in YAML and
     any camera name that isn't lowercase snake_case, before pydantic-settings
     ever runs.
  2. `Settings()` (pydantic-settings) merges the YAML topology with
     `RMCP_CAMERAS__<name>__PASSWORD` env vars into the final `CameraConfig`
     (`password: SecretStr`, required, never sourced from YAML).

Config shape is a **named map** (`dict[str, CameraConfig]`), not a list —
`pydantic-settings`' env/YAML merge (`deep_update`) only recurses when both
sides of a key are `dict`s; a list field can never merge correctly with the
exploded env source. See Pattern 1 in 01-RESEARCH.md.

Config file location: `RMCP_CONFIG_FILE` env var if set, else
`~/.config/reolink-mcp/config.yaml`. Computed once at import time as
`CONFIG_PATH` — a stdio server is launched by the MCP client, not the user's
shell, so the default must never depend on `cwd` (PITFALLS.md Pitfall 10).

Note: `.env` file support (via pydantic-settings' `env_file`) is a **local
dev-loop convenience only** — `uv run` inherits/reads `.env`, but real Claude
Desktop/Code launches spawn the server directly and do not read `.env`. Do
not mistake this for the real client secret-delivery mechanism; document
`RMCP_CAMERAS__<name>__PASSWORD` in the client's own config `env` block.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

_CAMERA_NAME_RE = re.compile(r"[a-z0-9_]+")


def resolve_config_path() -> Path:
    """Resolve the camera config YAML path.

    `RMCP_CONFIG_FILE`, if set, wins. Otherwise defaults to
    `~/.config/reolink-mcp/config.yaml` — never derived from `cwd`, since a
    stdio server is launched by the MCP client, not a user's shell.
    """
    override = os.environ.get("RMCP_CONFIG_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "reolink-mcp" / "config.yaml"


CONFIG_PATH = resolve_config_path()


def load_raw_cameras(path: Path) -> dict[str, dict]:
    """Read and parse the YAML config file, returning its `cameras` mapping."""
    raw = yaml.safe_load(path.read_text())
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SystemExit(
            f"config error: {path} — top-level YAML must be a mapping "
            f"(e.g. 'cameras: {{...}}'), got {type(raw).__name__}"
        )
    cameras = raw.get("cameras", {})
    if not isinstance(cameras, dict):
        raise SystemExit(
            f"config error: {path} — 'cameras' must be a mapping of camera "
            f"name to camera config, got {type(cameras).__name__}"
        )
    return cameras


class CameraYamlEntry(BaseModel):
    """Strict YAML-only shape — no `password` field, by design (Pattern 2)."""

    model_config = ConfigDict(extra="forbid")

    host: str
    username: str


class CameraConfig(BaseModel):
    """Final merged camera shape — password is required and always sourced
    from the environment, never from YAML."""

    host: str
    username: str
    password: SecretStr


class Settings(BaseSettings):
    """Top-level server settings: env-var secrets merged over YAML topology.

    `.env` support is dev-loop convenience only — see module docstring.
    """

    model_config = SettingsConfigDict(
        env_prefix="RMCP_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    cameras: dict[str, CameraConfig]
    read_only: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=CONFIG_PATH),
            dotenv_settings,
            file_secret_settings,
        )


def validate_yaml_shape(raw_cameras: dict[str, dict]) -> None:
    """Validate raw YAML camera entries before pydantic-settings ever runs.

    Rejects: camera names that aren't lowercase snake_case (env var override
    matching requires it — Pitfall C) and any `password` key present in YAML
    (passwords are env-var only — CONN-02).
    """
    for name, entry in raw_cameras.items():
        if not _CAMERA_NAME_RE.fullmatch(name):
            raise SystemExit(
                f"config error: camera name '{name}' must be lowercase "
                f"snake_case (e.g. 'front_door') for "
                f"RMCP_CAMERAS__{name}__PASSWORD to match reliably — "
                f"rename it in your YAML"
            )
        try:
            CameraYamlEntry(**entry)
        except ValidationError as e:
            if "password" in str(e):
                raise SystemExit(
                    f"config error: camera '{name}' has a 'password' key "
                    f"in YAML — passwords are never read from YAML. Remove "
                    f"it and set RMCP_CAMERAS__{name}__PASSWORD instead."
                ) from e
            raise SystemExit(f"config error: camera '{name}' — {e}") from e


def load_settings() -> Settings:
    """Load and validate `Settings`: config-file existence, YAML shape, then
    the full pydantic-settings env+YAML merge.

    Every failure mode raises a named `SystemExit` — never a raw traceback
    or generic error (CONN-01, CONN-02). The `ValidationError` fallback
    below never interpolates the raw error object itself, nor any of its
    per-error field-value details (CR-01 / G1): a phantom-camera env var
    (a password set for a camera name absent from YAML — a typo, a
    rename, or a WR-06 double-underscore split) would otherwise leak its
    plaintext value via pydantic's own field-value repr embedded in
    `str(ValidationError)`.
    """
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"config error: no config file found at {CONFIG_PATH} — create "
            f"it or set RMCP_CONFIG_FILE to point at your camera config YAML"
        )
    raw_cameras = load_raw_cameras(CONFIG_PATH)
    validate_yaml_shape(raw_cameras)
    try:
        return Settings()
    except ValidationError as e:
        for error in e.errors():
            loc = error["loc"]
            if len(loc) >= 2 and loc[-1] == "password" and error["type"] == "missing":
                name = loc[1]
                raise SystemExit(
                    f"config error: camera '{name}' has no password — set "
                    f"RMCP_CAMERAS__{name}__PASSWORD"
                ) from e

        # Phantom-camera detection (CR-01 / G1): an env var password whose
        # camera name has no matching YAML entry produces host/username
        # "missing" errors under cameras.<name>.* — never a "password"
        # error, since the env var itself provided that field. Only
        # reached when the real-camera-missing-password branch above did
        # not already match.
        phantom_names = {
            error["loc"][1]
            for error in e.errors()
            if len(error["loc"]) >= 2 and error["loc"][0] == "cameras"
        } - set(raw_cameras)
        if phantom_names:
            name = sorted(phantom_names)[0]
            raise SystemExit(
                f"config error: no camera named '{name}' in YAML — "
                f"did you misspell RMCP_CAMERAS__{name}__PASSWORD?"
            ) from e

        # Redacted fallback — built only from e.errors()' loc/type, never
        # the raw ValidationError object or any per-error field value.
        details = "; ".join(
            f"{'.'.join(str(p) for p in error['loc'])}: {error['type']}"
            for error in e.errors()
        )
        raise SystemExit(f"config error: {details}") from e
