"""Unit tests for pure utility functions across the codebase."""

from __future__ import annotations

import random

import pytest

# ---------------------------------------------------------------------------
# cache/redis_cache.py — pure utility functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cache_tier_enum():
    from aegis.cache.redis_cache import CacheTier

    assert CacheTier.HOT == "hot"
    assert CacheTier.WARM == "warm"
    assert CacheTier.COLD == "cold"


@pytest.mark.unit
def test_ttl_of():
    from aegis.cache.redis_cache import CacheTier, ttl_of

    hot = ttl_of(CacheTier.HOT)
    warm = ttl_of(CacheTier.WARM)
    cold = ttl_of(CacheTier.COLD)

    assert isinstance(hot, int) and hot > 0
    assert isinstance(warm, int) and warm > 0
    assert isinstance(cold, int) and cold > 0
    assert hot < warm < cold


@pytest.mark.unit
def test_priority_enum():
    from aegis.cache.redis_cache import Priority

    assert Priority.P0 == "P0"
    assert Priority.P1 == "P1"
    assert Priority.P2 == "P2"
    assert Priority.P3 == "P3"


@pytest.mark.unit
def test_band_of_returns_tuple():
    from aegis.cache.redis_cache import Priority, band_of

    for p in Priority:
        lo, hi = band_of(p)
        assert isinstance(lo, float)
        assert isinstance(hi, float)
        assert lo <= hi


@pytest.mark.unit
def test_band_of_p0_lowest_score():
    from aegis.cache.redis_cache import Priority, band_of

    p0_lo, _ = band_of(Priority.P0)
    p1_lo, _ = band_of(Priority.P1)
    assert p0_lo < p1_lo


@pytest.mark.unit
def test_redis_config_from_env():
    from aegis.cache.redis_cache import RedisConfig

    cfg = RedisConfig.from_env({"AEGIS_REDIS_URL": "redis://myhost:6380/2"})
    assert "myhost" in cfg.url
    assert "6380" in cfg.url


@pytest.mark.unit
def test_redis_config_from_env_fallback():
    from aegis.cache.redis_cache import RedisConfig

    cfg = RedisConfig.from_env({})
    assert "localhost" in cfg.url


@pytest.mark.unit
def test_default_encode_bytes_passthrough():
    from aegis.cache.redis_cache import _default_encode

    data = b"already bytes"
    assert _default_encode(data) is data


@pytest.mark.unit
def test_default_encode_dict():
    import orjson

    from aegis.cache.redis_cache import _default_encode

    result = _default_encode({"key": "value", "n": 42})
    decoded = orjson.loads(result)
    assert decoded["key"] == "value"
    assert decoded["n"] == 42


@pytest.mark.unit
def test_default_decode_json():
    import orjson

    from aegis.cache.redis_cache import _default_decode

    data = orjson.dumps({"x": 1})
    result = _default_decode(data)
    assert result == {"x": 1}


@pytest.mark.unit
def test_default_decode_invalid_json_returns_bytes():
    from aegis.cache.redis_cache import _default_decode

    bad = b"not json at all !!!"
    result = _default_decode(bad)
    assert isinstance(result, bytes)


@pytest.mark.unit
def test_redis_cache_constructor_requires_url_or_config():
    from aegis.cache.redis_cache import RedisCache

    with pytest.raises(ValueError, match="url"):
        RedisCache()  # no config, no url


@pytest.mark.unit
def test_redis_cache_constructor_with_url():
    from aegis.cache.redis_cache import RedisCache

    cache = RedisCache(url="redis://localhost:6379/0")
    assert cache._config.url == "redis://localhost:6379/0"
    assert cache._client is None  # lazy


# ---------------------------------------------------------------------------
# db/pool.py — pure utility functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pg_config_defaults():
    from aegis.db.pool import PgConfig

    cfg = PgConfig(dsn="postgresql://localhost/test")
    assert cfg.dsn == "postgresql://localhost/test"
    assert cfg.min_size >= 1
    assert cfg.max_size >= cfg.min_size


@pytest.mark.unit
def test_pg_config_from_env_with_dsn():
    from aegis.db.pool import PgConfig

    cfg = PgConfig.from_env({"AEGIS_PG_DSN": "postgresql://user:pw@db/mydb"})
    assert cfg.dsn == "postgresql://user:pw@db/mydb"


@pytest.mark.unit
def test_pg_config_from_env_parts():
    from aegis.db.pool import PgConfig

    cfg = PgConfig.from_env(
        {
            "POSTGRES_HOST": "pg.example.com",
            "POSTGRES_PORT": "5433",
            "POSTGRES_USER": "admin",
            "POSTGRES_PASSWORD": "secret",
            "POSTGRES_DB": "prod",
        }
    )
    assert "pg.example.com" in cfg.dsn
    assert "5433" in cfg.dsn
    assert "admin" in cfg.dsn


@pytest.mark.unit
def test_pg_pool_constructor_requires_dsn_or_config():
    from aegis.db.pool import PgPool

    with pytest.raises(ValueError):
        PgPool()


@pytest.mark.unit
def test_pg_pool_constructor_with_dsn():
    from aegis.db.pool import PgPool

    pool = PgPool(dsn="postgresql://localhost/test")
    assert pool._pool is None  # lazy — not connected yet


@pytest.mark.unit
def test_pg_pool_constructor_with_config():
    from aegis.db.pool import PgConfig, PgPool

    cfg = PgConfig(dsn="postgresql://localhost/test", min_size=2, max_size=10)
    pool = PgPool(config=cfg)
    assert pool._config.min_size == 2
    assert pool._config.max_size == 10


# ---------------------------------------------------------------------------
# scrape/stealth.py — StealthProfile.random()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stealth_profile_random_desktop():
    from aegis.scrape.stealth import StealthProfile

    profile = StealthProfile.random(mobile=False)
    assert profile.user_agent
    assert profile.platform
    assert profile.screen_width > 0
    assert profile.screen_height > 0
    assert profile.hardware_concurrency > 0
    assert profile.max_touch_points == 0  # desktop


@pytest.mark.unit
def test_stealth_profile_random_mobile():
    from aegis.scrape.stealth import StealthProfile

    profile = StealthProfile.random(mobile=True)
    assert profile.is_mobile is True
    assert profile.max_touch_points > 0


@pytest.mark.unit
def test_stealth_profile_random_reproducible_with_seed():
    from aegis.scrape.stealth import StealthProfile

    rng = random.Random(42)
    p1 = StealthProfile.random(rng=rng)

    rng = random.Random(42)
    p2 = StealthProfile.random(rng=rng)

    assert p1.user_agent == p2.user_agent
    assert p1.platform == p2.platform


@pytest.mark.unit
def test_stealth_profile_http_headers():
    from aegis.scrape.stealth import StealthProfile

    profile = StealthProfile.random()
    headers = profile.http_headers()
    assert "User-Agent" in headers
    assert "Accept-Language" in headers
    assert "Accept" in headers


@pytest.mark.unit
def test_stealth_profile_canvas_seeds_differ():
    from aegis.scrape.stealth import StealthProfile

    p1 = StealthProfile.random()
    p2 = StealthProfile.random()
    # Very unlikely to be equal (32-bit random)
    assert (
        p1.canvas_noise_seed != p2.canvas_noise_seed or p1.audio_noise_seed != p2.audio_noise_seed
    )


@pytest.mark.unit
def test_build_stealth_script():
    from aegis.scrape.stealth import StealthProfile, build_stealth_script

    profile = StealthProfile.random()
    script = build_stealth_script(profile)
    assert isinstance(script, str)
    assert len(script) > 100  # non-trivial JS
    assert profile.user_agent in script or "userAgent" in script


@pytest.mark.unit
def test_languages_array():
    from aegis.scrape.stealth import _languages_array

    result = _languages_array("en-US,en;q=0.9,fr;q=0.8")
    assert result == ["en-US", "en", "fr"]


@pytest.mark.unit
def test_languages_array_single():
    from aegis.scrape.stealth import _languages_array

    result = _languages_array("en")
    assert result == ["en"]


# ---------------------------------------------------------------------------
# scrape/cloudflare.py — looks_like_challenge()
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_looks_like_challenge_false_for_normal_200():
    from aegis.scrape.cloudflare import looks_like_challenge

    assert not looks_like_challenge(status_code=200, body="<html>Products</html>", headers={})


@pytest.mark.unit
def test_looks_like_challenge_body_marker():
    from aegis.scrape.cloudflare import looks_like_challenge

    assert looks_like_challenge(body="Please wait... DDoS protection by Cloudflare")


@pytest.mark.unit
def test_looks_like_challenge_cf_ray_header():
    from aegis.scrape.cloudflare import looks_like_challenge

    assert looks_like_challenge(
        status_code=503,
        body=None,
        headers={"cf-ray": "abc123", "server": "cloudflare"},
    )


@pytest.mark.unit
def test_looks_like_challenge_cf_server_header():
    from aegis.scrape.cloudflare import looks_like_challenge

    assert looks_like_challenge(
        status_code=403,
        body=None,
        headers={"server": "cloudflare"},
    )


@pytest.mark.unit
def test_looks_like_challenge_cf_mitigated():
    from aegis.scrape.cloudflare import looks_like_challenge

    assert looks_like_challenge(
        status_code=403,
        body=None,
        headers={"cf-mitigated": "challenge"},
    )


@pytest.mark.unit
def test_looks_like_challenge_cloudflare_in_body():
    from aegis.scrape.cloudflare import looks_like_challenge

    assert looks_like_challenge(
        status_code=403,
        body="Attention Required! | Cloudflare",
        headers={},
    )


@pytest.mark.unit
def test_looks_like_challenge_no_evidence():
    from aegis.scrape.cloudflare import looks_like_challenge

    # 503 with no CF evidence is NOT a challenge
    assert not looks_like_challenge(status_code=503, body=None, headers={})


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_schema_version_is_string():
    from aegis.constants import SCHEMA_VERSION

    assert isinstance(SCHEMA_VERSION, str)
    assert len(SCHEMA_VERSION) > 0


@pytest.mark.unit
def test_priority_boundaries_ordering():
    from aegis.constants import (
        PRIORITY_P0_BOUNDARY,
        PRIORITY_P1_BOUNDARY,
        PRIORITY_P2_BOUNDARY,
        PRIORITY_P3_BOUNDARY,
    )

    assert PRIORITY_P0_BOUNDARY < PRIORITY_P1_BOUNDARY
    assert PRIORITY_P1_BOUNDARY < PRIORITY_P2_BOUNDARY
    assert PRIORITY_P2_BOUNDARY < PRIORITY_P3_BOUNDARY


@pytest.mark.unit
def test_cache_ttl_ordering():
    from aegis.constants import (
        CACHE_TTL_COLD_SECONDS,
        CACHE_TTL_HOT_SECONDS,
        CACHE_TTL_WARM_SECONDS,
    )

    assert CACHE_TTL_HOT_SECONDS < CACHE_TTL_WARM_SECONDS < CACHE_TTL_COLD_SECONDS
