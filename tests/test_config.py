"""Tests for the config layer's named-map Settings and two-stage validation.

Eight behaviors (CONN-01, CONN-02, SAFE-02):
1. YAML topology + env-var password merges into a valid Settings.cameras entry.
2. A `password:` key in YAML is a named, loud SystemExit (never read from YAML).
3. A missing RMCP_CAMERAS__<name>__PASSWORD env var is a named, loud SystemExit.
4. A mixed-case camera name is a named, loud SystemExit (env var case folding).
5. A missing config file is a named, loud SystemExit naming RMCP_CONFIG_FILE.
6. A phantom-camera env var (password set for a name absent from YAML) never
   leaks the password value — a curated "no camera named" SystemExit instead
   (CR-01 / G1 regression guard).
7. A capitalized/mistyped secret-like YAML key (Password, passwd, secret,
   token) never leaks its plaintext value — curated CONN-02 SystemExit
   (CR-01 regression guard, 01-VERIFICATION gap G1).
8. A non-secret-like extra YAML key goes through the redacted loc/type
   fallback — its value never appears in the message (CR-01).
9. RMCP_READ_ONLY unset -> settings.read_only defaults to False.
10. RMCP_READ_ONLY=true -> settings.read_only is True.
"""

import pytest

from reolink_mcp.config import load_settings


def test_env_override_merges_into_named_map(tmp_config, monkeypatch):
    tmp_config.write_text(
        "cameras:\n"
        "  front_door:\n"
        "    host: 192.168.1.10\n"
        "    username: admin\n"
    )
    monkeypatch.setenv("RMCP_CAMERAS__front_door__PASSWORD", "secret1")

    settings = load_settings()

    camera = settings.cameras["front_door"]
    assert camera.host == "192.168.1.10"
    assert camera.username == "admin"
    assert camera.password.get_secret_value() == "secret1"


def test_password_in_yaml_is_rejected(tmp_config):
    tmp_config.write_text(
        "cameras:\n"
        "  front_door:\n"
        "    host: 192.168.1.10\n"
        "    username: admin\n"
        "    password: hunter2\n"
    )

    with pytest.raises(
        SystemExit, match=r"front_door.*RMCP_CAMERAS__front_door__PASSWORD"
    ):
        load_settings()


def test_missing_password_env_var_is_rejected(tmp_config):
    tmp_config.write_text(
        "cameras:\n"
        "  garage:\n"
        "    host: 192.168.1.11\n"
        "    username: admin\n"
    )

    with pytest.raises(
        SystemExit, match=r"garage.*RMCP_CAMERAS__garage__PASSWORD"
    ):
        load_settings()


def test_mixed_case_camera_name_is_rejected(tmp_config, monkeypatch):
    tmp_config.write_text(
        "cameras:\n"
        "  Front_Door:\n"
        "    host: 192.168.1.10\n"
        "    username: admin\n"
    )
    monkeypatch.setenv("RMCP_CAMERAS__Front_Door__PASSWORD", "secret1")

    with pytest.raises(SystemExit, match=r"Front_Door.*lowercase"):
        load_settings()


def test_missing_config_file_is_rejected(tmp_path, monkeypatch):
    missing_path = tmp_path / "does_not_exist.yaml"
    monkeypatch.setattr("reolink_mcp.config.CONFIG_PATH", missing_path)

    with pytest.raises(SystemExit, match=r"RMCP_CONFIG_FILE"):
        load_settings()


def test_phantom_camera_env_var_never_leaks_password(tmp_config, monkeypatch):
    """CR-01 / G1 regression: an env var password set for a camera name
    absent from YAML (typo, rename, or a WR-06 double-underscore split)
    must never leak its plaintext password value via pydantic's
    ValidationError.input_value, and must raise a curated, self-correcting
    message instead of the redacted-but-generic fallback."""
    tmp_config.write_text(
        "cameras:\n"
        "  front_door:\n"
        "    host: 192.168.1.10\n"
        "    username: admin\n"
    )
    monkeypatch.setenv("RMCP_CAMERAS__front_door__PASSWORD", "secret1")
    secret_value = "SUPER-SECRET-HUNTER2"
    monkeypatch.setenv("RMCP_CAMERAS__typo_cam__PASSWORD", secret_value)

    with pytest.raises(SystemExit) as exc_info:
        load_settings()

    message = str(exc_info.value)
    assert secret_value not in message
    assert "input_value" not in message
    assert "typo_cam" in message
    assert "no camera named" in message


@pytest.mark.parametrize("key", ["Password", "passwd", "secret", "token"])
def test_secret_like_yaml_key_never_leaks_value(tmp_config, key):
    """CR-01 regression (01-VERIFICATION gap G1): a capitalized or mistyped
    secret-like YAML key must never echo its plaintext value into the
    SystemExit message via pydantic's input_value repr, and must raise the
    curated CONN-02 message naming the env var instead."""
    secret_value = "SUPER-SECRET-HUNTER2"
    tmp_config.write_text(
        "cameras:\n"
        "  front_door:\n"
        "    host: 192.168.1.10\n"
        "    username: admin\n"
        f"    {key}: {secret_value}\n"
    )

    with pytest.raises(SystemExit) as exc_info:
        load_settings()

    message = str(exc_info.value)
    assert secret_value not in message
    assert "input_value" not in message
    assert "front_door" in message
    assert "RMCP_CAMERAS__front_door__PASSWORD" in message


def test_unknown_extra_yaml_key_never_leaks_value(tmp_config):
    """CR-01 regression: even a non-secret-like extra key goes through the
    redacted loc/type fallback — its value never appears in the message."""
    tmp_config.write_text(
        "cameras:\n"
        "  front_door:\n"
        "    host: 192.168.1.10\n"
        "    username: admin\n"
        "    location: TOP-SECRET-BUNKER\n"
    )

    with pytest.raises(SystemExit) as exc_info:
        load_settings()

    message = str(exc_info.value)
    assert "TOP-SECRET-BUNKER" not in message
    assert "input_value" not in message
    assert "front_door" in message
    assert "location" in message


def test_read_only_defaults_to_false_when_unset(tmp_config, monkeypatch):
    tmp_config.write_text(
        "cameras:\n"
        "  front_door:\n"
        "    host: 192.168.1.10\n"
        "    username: admin\n"
    )
    monkeypatch.setenv("RMCP_CAMERAS__front_door__PASSWORD", "secret1")
    monkeypatch.delenv("RMCP_READ_ONLY", raising=False)

    settings = load_settings()

    assert settings.read_only is False


def test_read_only_true_when_env_var_set(tmp_config, monkeypatch):
    tmp_config.write_text(
        "cameras:\n"
        "  front_door:\n"
        "    host: 192.168.1.10\n"
        "    username: admin\n"
    )
    monkeypatch.setenv("RMCP_CAMERAS__front_door__PASSWORD", "secret1")
    monkeypatch.setenv("RMCP_READ_ONLY", "true")

    settings = load_settings()

    assert settings.read_only is True
