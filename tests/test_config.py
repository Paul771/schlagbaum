"""Tests for config/secrets loading (D-02/D-06/D-07)."""

import json
import os

from src.config import load_settings


def test_load_settings_returns_cameras_dict():
    settings = load_settings("config.json")
    assert isinstance(settings["cameras"], dict)
    assert "cam_1" in settings["cameras"]
    assert "cam_2" in settings["cameras"]


def test_login_password_come_from_env(monkeypatch):
    monkeypatch.setenv("PRIVRATNIK_LOGIN", "test_login")
    monkeypatch.setenv("PRIVRATNIK_PASSWORD", "test_password")
    settings = load_settings("config.json")
    assert settings["login"] == "test_login"
    assert settings["password"] == "test_password"


def test_config_json_has_no_password_key():
    with open("config.json", encoding="utf-8") as f:
        raw = json.load(f)
    assert "password" not in raw
    assert "login" not in raw
