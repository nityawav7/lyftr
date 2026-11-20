"""Unit tests for pure pieces: header parsing, key pool ordering, config parsing."""

from __future__ import annotations

import pytest

from app.config import sqlite_path_from_url
from app.llm.key_pool import KeyPool
from app.llm.simulation import parse_failure_header


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("key_1=429", {"key_1": 429, "key_2": None}),
        ("key_1=429,key_2=500", {"key_1": 429, "key_2": 500}),
        (" KEY_1 = 502 , key_2=503 ", {"key_1": 502, "key_2": 503}),
        ("*=429", {"key_1": 429, "key_2": 429}),
        ("*=500,key_2=429", {"key_1": 500, "key_2": 429}),
        ("key_1=404", {"key_1": None, "key_2": None}),
        ("", {"key_1": None, "key_2": None}),
        (None, {"key_1": None, "key_2": None}),
    ],
)
def test_parse_failure_header(raw, expected):
    plan = parse_failure_header(raw)
    assert {k: plan.status_for(k) for k in expected} == expected


def test_parse_failure_header_reports_ignored_parts():
    plan = parse_failure_header("key_1=429,nonsense,key_2=999")
    assert plan.active
    assert plan.ignored == ("nonsense", "key_2=999")


def test_key_pool_ids_and_order():
    pool = KeyPool(["a", "b", "c"], ["m1", "m1", "m2"])
    assert [k.key_id for k in pool.attempt_order()] == ["key_1", "key_2", "key_3"]
    assert pool.distinct_models() == ["m1", "m2"]


def test_key_pool_deprioritises_cooling_keys():
    pool = KeyPool(["a", "b", "c"], ["m"] * 3, rate_limit_cooldown=60)
    k1 = pool.keys[0]
    pool.record_failure(k1, 429, "rate limited")
    assert [k.key_id for k in pool.attempt_order()] == ["key_2", "key_3", "key_1"]
    pool.record_success(k1)
    assert [k.key_id for k in pool.attempt_order()] == ["key_1", "key_2", "key_3"]


def test_key_pool_respects_retry_after():
    pool = KeyPool(["a", "b"], ["m"] * 2, rate_limit_cooldown=1)
    pool.record_failure(pool.keys[0], 429, "rate limited", retry_after=120)
    assert pool.snapshot()[0]["cooldown_remaining_s"] >= 119


def test_key_repr_hides_secret():
    pool = KeyPool(["super-secret-value"], ["m"])
    assert "super-secret-value" not in repr(pool.keys[0])


@pytest.mark.parametrize(
    ("url", "path"),
    [
        ("sqlite:////data/app.db", "/data/app.db"),
        ("sqlite:///./data/app.db", "./data/app.db"),
        ("sqlite:///:memory:", ":memory:"),
    ],
)
def test_sqlite_url_parsing(url, path):
    assert sqlite_path_from_url(url) == path


def test_non_sqlite_url_is_rejected():
    with pytest.raises(ValueError):
        sqlite_path_from_url("postgresql://localhost/db")


def test_settings_tolerate_windows_line_endings(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("LLM_API_KEYS", "a,b\r")
    monkeypatch.setenv("DATABASE_URL", "sqlite:////data/app.db\r")
    monkeypatch.setenv("LOG_LEVEL", "info\r")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true\r")
    monkeypatch.setenv("CACHE_TYPE", "DISK\r")
    s = Settings.from_env()
    assert s.api_keys == ["a", "b"]
    assert s.database_path == "/data/app.db"
    assert s.log_level == "INFO"
    assert s.enable_failure_simulation is True
    assert s.cache_type == "disk"
