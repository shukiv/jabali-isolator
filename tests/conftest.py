"""Shared test fixtures."""

from __future__ import annotations

import pwd
from pathlib import Path

import pytest


@pytest.fixture
def isolator_dirs(tmp_path, monkeypatch):
    """Override all jabali_isolator paths to use tmp_path.

    Returns a dict of the temp directories for assertions.
    """
    import jabali_isolator.config as cfg

    monkeypatch.setattr(cfg, "MACHINES_DIR", str(tmp_path / "machines"))
    monkeypatch.setattr(cfg, "NSPAWN_DIR", str(tmp_path / "nspawn"))
    monkeypatch.setattr(cfg, "SERVICE_DROPIN_BASE", str(tmp_path / "system"))
    monkeypatch.setattr(cfg, "SOCKET_DIR", str(tmp_path / "sockets"))

    return {
        "machines": tmp_path / "machines",
        "nspawn": tmp_path / "nspawn",
        "system": tmp_path / "system",
        "sockets": tmp_path / "sockets",
        "root": tmp_path,
    }


@pytest.fixture
def fake_pool(tmp_path, monkeypatch):
    """Create a fake PHP-FPM pool config for testuser and patch FPM_POOL_PATHS."""
    import jabali_isolator.config as cfg

    pool_dir = tmp_path / "php" / "8.4" / "fpm" / "pool.d"
    pool_dir.mkdir(parents=True)
    (pool_dir / "testuser.conf").write_text("[testuser]\nuser = testuser\n")
    monkeypatch.setattr(cfg, "FPM_POOL_PATHS", [str(tmp_path / "php/*/fpm/pool.d/{user}.conf")])
    return pool_dir


@pytest.fixture
def fake_user():
    """Return a mock passwd entry for testuser."""
    return pwd.struct_passwd(("testuser", "x", 1001, 1001, "", "/home/testuser", "/bin/bash"))
