"""Tests for container manager."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jabali_isolator.container import (
    IsolatorError,
    _validate_user,
    create,
    destroy,
    is_available,
    list_all,
    restart,
    start,
    status,
    stop,
)


class TestValidateUser:
    def test_valid_usernames(self):
        for name in ["user1", "test_user", "john.doe", "a-b", "user123"]:
            _validate_user(name)  # should not raise

    def test_rejects_empty(self):
        with pytest.raises(IsolatorError):
            _validate_user("")

    def test_rejects_shell_metacharacters(self):
        for bad in ["user;rm", "user$(cmd)", "user`cmd`", "../etc", "user name", "USER"]:
            with pytest.raises(IsolatorError):
                _validate_user(bad)

    def test_rejects_starting_with_number(self):
        with pytest.raises(IsolatorError):
            _validate_user("1user")


class TestIsAvailable:
    def test_true_when_installed(self):
        with patch("jabali_isolator.container.shutil.which", return_value="/usr/bin/systemd-nspawn"):
            assert is_available() is True

    def test_false_when_missing(self):
        with patch("jabali_isolator.container.shutil.which", return_value=None):
            assert is_available() is False


class TestCreate:
    @pytest.mark.asyncio
    async def test_creates_container(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path / "machines"))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path / "machines"))
        monkeypatch.setattr("jabali_isolator.units.NSPAWN_DIR", str(tmp_path / "nspawn"))
        monkeypatch.setattr("jabali_isolator.units.SERVICE_DROPIN_BASE", str(tmp_path / "system"))

        import pwd
        fake_pw = pwd.struct_passwd(("testuser", "x", 1001, 1001, "", "/home/testuser", "/bin/bash"))

        # Create a fake pool config for _find_pool_config to discover
        pool_dir = tmp_path / "php" / "8.4" / "fpm" / "pool.d"
        pool_dir.mkdir(parents=True)
        (pool_dir / "testuser.conf").write_text("[testuser]\nuser = testuser\n")
        monkeypatch.setattr("jabali_isolator.container.FPM_POOL_PATHS", [str(tmp_path / "php/*/fpm/pool.d/{user}.conf")])

        with patch("jabali_isolator.container.is_available", return_value=True), \
             patch("jabali_isolator.rootfs._lookup_user", return_value=fake_pw), \
             patch("jabali_isolator.container._run", new_callable=AsyncMock, return_value=(0, "", "")) as mock_run:
            result = await create("testuser", memory="256M", cpu="50%")

        assert result["user"] == "testuser"
        assert result["memory"] == "256M"
        assert result["cpu"] == "50%"
        assert result["php_version"] == "8.4"
        enable_calls = [c for c in mock_run.call_args_list if "enable" in c[0][0]]
        assert len(enable_calls) == 1
        assert (tmp_path / "machines" / "testuser-php" / "etc" / "passwd").is_file()
        assert (tmp_path / "nspawn" / "testuser-php.nspawn").is_file()
        # Verify the nspawn unit contains PHP-FPM command
        nspawn_content = (tmp_path / "nspawn" / "testuser-php.nspawn").read_text()
        assert "php-fpm8.4" in nspawn_content
        assert "--nodaemonize" in nspawn_content

    @pytest.mark.asyncio
    async def test_fails_without_nspawn(self):
        with patch("jabali_isolator.container.is_available", return_value=False):
            with pytest.raises(IsolatorError, match="not installed"):
                await create("testuser")

    @pytest.mark.asyncio
    async def test_fails_for_nonexistent_user(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path))
        # Create fake pool so _find_pool_config passes
        pool_dir = tmp_path / "php" / "8.4" / "fpm" / "pool.d"
        pool_dir.mkdir(parents=True)
        (pool_dir / "nope.conf").write_text("[nope]\nuser = nope\n")
        monkeypatch.setattr("jabali_isolator.container.FPM_POOL_PATHS", [str(tmp_path / "php/*/fpm/pool.d/{user}.conf")])

        with patch("jabali_isolator.container.is_available", return_value=True), \
             patch("jabali_isolator.rootfs._lookup_user", side_effect=KeyError("nope")):
            with pytest.raises(IsolatorError, match="does not exist"):
                await create("nope")

    @pytest.mark.asyncio
    async def test_fails_without_pool_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.FPM_POOL_PATHS", [str(tmp_path / "nonexistent/*/pool.d/{user}.conf")])

        with patch("jabali_isolator.container.is_available", return_value=True):
            with pytest.raises(IsolatorError, match="No PHP-FPM pool config found"):
                await create("testuser")

    @pytest.mark.asyncio
    async def test_succeeds_when_enable_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path / "machines"))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path / "machines"))
        monkeypatch.setattr("jabali_isolator.units.NSPAWN_DIR", str(tmp_path / "nspawn"))
        monkeypatch.setattr("jabali_isolator.units.SERVICE_DROPIN_BASE", str(tmp_path / "system"))

        pool_dir = tmp_path / "php" / "8.4" / "fpm" / "pool.d"
        pool_dir.mkdir(parents=True)
        (pool_dir / "testuser.conf").write_text("[testuser]\nuser = testuser\n")
        monkeypatch.setattr("jabali_isolator.container.FPM_POOL_PATHS", [str(tmp_path / "php/*/fpm/pool.d/{user}.conf")])

        import pwd
        fake_pw = pwd.struct_passwd(("testuser", "x", 1001, 1001, "", "/home/testuser", "/bin/bash"))

        with patch("jabali_isolator.container.is_available", return_value=True), \
             patch("jabali_isolator.rootfs._lookup_user", return_value=fake_pw), \
             patch("jabali_isolator.container._run", new_callable=AsyncMock, side_effect=[
                 (0, "", ""),                        # daemon-reload
                 (1, "", "Failed to enable unit"),   # enable fails
             ]):
            result = await create("testuser")

        assert result["user"] == "testuser"

    @pytest.mark.asyncio
    async def test_rejects_invalid_username(self):
        with pytest.raises(IsolatorError, match="Invalid username"):
            await create("bad;user")


class TestDestroy:
    def _setup_pool(self, tmp_path, monkeypatch):
        pool_dir = tmp_path / "php" / "8.4" / "fpm" / "pool.d"
        pool_dir.mkdir(parents=True, exist_ok=True)
        (pool_dir / "testuser.conf").write_text("[testuser]\nuser = testuser\n")
        monkeypatch.setattr("jabali_isolator.container.FPM_POOL_PATHS", [str(tmp_path / "php/*/fpm/pool.d/{user}.conf")])

    @pytest.mark.asyncio
    async def test_destroys_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path / "machines"))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path / "machines"))
        monkeypatch.setattr("jabali_isolator.units.NSPAWN_DIR", str(tmp_path / "nspawn"))
        monkeypatch.setattr("jabali_isolator.units.SERVICE_DROPIN_BASE", str(tmp_path / "system"))
        self._setup_pool(tmp_path, monkeypatch)

        import pwd
        fake_pw = pwd.struct_passwd(("testuser", "x", 1001, 1001, "", "/home/testuser", "/bin/bash"))

        with patch("jabali_isolator.container.is_available", return_value=True), \
             patch("jabali_isolator.rootfs._lookup_user", return_value=fake_pw), \
             patch("jabali_isolator.container._run", new_callable=AsyncMock, return_value=(0, "", "")):
            await create("testuser")

        # Destroy
        with patch("jabali_isolator.container._run", new_callable=AsyncMock, return_value=(0, "", "")) as mock_run:
            removed = await destroy("testuser")

        assert removed is True
        disable_calls = [c for c in mock_run.call_args_list if "disable" in c[0][0]]
        assert len(disable_calls) == 1
        assert not (tmp_path / "machines" / "testuser-php").exists()
        assert not (tmp_path / "nspawn" / "testuser-php.nspawn").exists()

    @pytest.mark.asyncio
    async def test_succeeds_when_disable_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path / "machines"))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path / "machines"))
        monkeypatch.setattr("jabali_isolator.units.NSPAWN_DIR", str(tmp_path / "nspawn"))
        monkeypatch.setattr("jabali_isolator.units.SERVICE_DROPIN_BASE", str(tmp_path / "system"))
        self._setup_pool(tmp_path, monkeypatch)

        import pwd
        fake_pw = pwd.struct_passwd(("testuser", "x", 1001, 1001, "", "/home/testuser", "/bin/bash"))

        with patch("jabali_isolator.container.is_available", return_value=True), \
             patch("jabali_isolator.rootfs._lookup_user", return_value=fake_pw), \
             patch("jabali_isolator.container._run", new_callable=AsyncMock, return_value=(0, "", "")):
            await create("testuser")

        # Destroy — disable fails but destroy still succeeds
        with patch("jabali_isolator.container._run", new_callable=AsyncMock, side_effect=[
            (1, "", ""),                         # machinectl show (status check) -> stopped
            (1, "disabled", ""),                 # is-enabled -> disabled
            (1, "", "Failed to disable unit"),   # disable fails
            (0, "", ""),                         # daemon-reload
        ]):
            removed = await destroy("testuser")

        assert removed is True
        assert not (tmp_path / "machines" / "testuser-php").exists()

    @pytest.mark.asyncio
    async def test_returns_false_when_nothing_to_destroy(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path))
        monkeypatch.setattr("jabali_isolator.units.NSPAWN_DIR", str(tmp_path / "nspawn"))
        monkeypatch.setattr("jabali_isolator.units.SERVICE_DROPIN_BASE", str(tmp_path / "system"))

        with patch("jabali_isolator.container._run", new_callable=AsyncMock, return_value=(1, "", "not found")):
            removed = await destroy("nonexistent")

        assert removed is False


class TestStart:
    @pytest.mark.asyncio
    async def test_starts_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path))
        (tmp_path / "testuser-php").mkdir()

        with patch("jabali_isolator.container._run", new_callable=AsyncMock, return_value=(0, "", "")):
            assert await start("testuser") is True

    @pytest.mark.asyncio
    async def test_fails_when_no_rootfs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path))

        with pytest.raises(IsolatorError, match="does not exist"):
            await start("nonexistent")


class TestStop:
    @pytest.mark.asyncio
    async def test_stops_running(self):
        with patch("jabali_isolator.container._run", new_callable=AsyncMock, return_value=(0, "", "")):
            assert await stop("testuser") is True

    @pytest.mark.asyncio
    async def test_tolerates_not_running(self):
        with patch("jabali_isolator.container._run", new_callable=AsyncMock, return_value=(1, "", "not running")):
            assert await stop("testuser") is True


class TestStatus:
    @pytest.mark.asyncio
    async def test_running(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path))
        (tmp_path / "testuser-php").mkdir()

        with patch("jabali_isolator.container._run", new_callable=AsyncMock, side_effect=[
            (0, "State=running", ""),  # machinectl show
            (0, "enabled", ""),        # systemctl is-enabled
        ]):
            info = await status("testuser")

        assert info["state"] == "running"
        assert info["exists"] is True
        assert info["enabled"] is True

    @pytest.mark.asyncio
    async def test_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path))

        info = await status("nonexistent")
        assert info["state"] == "missing"
        assert info["exists"] is False
        assert info["enabled"] is False

    @pytest.mark.asyncio
    async def test_stopped_and_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path))
        (tmp_path / "testuser-php").mkdir()

        with patch("jabali_isolator.container._run", new_callable=AsyncMock, side_effect=[
            (1, "", ""),           # machinectl show fails -> stopped
            (1, "disabled", ""),   # is-enabled -> disabled
        ]):
            info = await status("testuser")

        assert info["state"] == "stopped"
        assert info["enabled"] is False

    @pytest.mark.asyncio
    async def test_stopped_and_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path))
        (tmp_path / "testuser-php").mkdir()

        with patch("jabali_isolator.container._run", new_callable=AsyncMock, side_effect=[
            (1, "", ""),           # machinectl show fails -> stopped
            (0, "enabled", ""),    # is-enabled -> enabled
        ]):
            info = await status("testuser")

        assert info["state"] == "stopped"
        assert info["enabled"] is True


class TestListAll:
    @pytest.mark.asyncio
    async def test_lists_containers(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path))
        monkeypatch.setattr("jabali_isolator.rootfs.MACHINES_DIR", str(tmp_path))
        (tmp_path / "alice-php").mkdir()
        (tmp_path / "bob-php").mkdir()
        (tmp_path / "not-a-container").mkdir()  # should be ignored

        with patch("jabali_isolator.container._run", new_callable=AsyncMock, return_value=(1, "", "not found")):
            containers = await list_all()

        assert len(containers) == 2
        users = [c["user"] for c in containers]
        assert "alice" in users
        assert "bob" in users
        for c in containers:
            assert "enabled" in c

    @pytest.mark.asyncio
    async def test_empty_when_no_machines_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("jabali_isolator.container.MACHINES_DIR", str(tmp_path / "nonexistent"))
        containers = await list_all()
        assert containers == []
