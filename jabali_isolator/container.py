"""NSpawnManager — create, start, stop, destroy nspawn containers for PHP-FPM isolation."""

from __future__ import annotations

import asyncio
import glob as glob_mod
import logging
import re
import shutil
from pathlib import Path

from jabali_isolator import config as _cfg
from jabali_isolator.config import validate_cpu, validate_memory
from jabali_isolator.machine import machine_name, rootfs_dir, service_name
from jabali_isolator.rootfs import create_rootfs, destroy_rootfs, rootfs_exists
from jabali_isolator.units import remove_unit_files, unit_files_exist, write_nspawn_unit, write_service_dropin

logger = logging.getLogger(__name__)


class IsolatorError(Exception):
    """Raised when a container operation fails."""


def _validate_user(user: str) -> None:
    """Validate username to prevent command injection."""
    if not re.match(_cfg.USERNAME_RE, user):
        raise IsolatorError(f"Invalid username: {user!r}")


async def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess safely (list args, no shell) and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise IsolatorError(f"Command timed out: {' '.join(cmd)}")

    return proc.returncode or 0, stdout.decode(errors="replace").strip(), stderr.decode(errors="replace").strip()


def _machine_name(user: str) -> str:
    return machine_name(user)


def is_available() -> bool:
    """Check if systemd-nspawn is installed."""
    return shutil.which("systemd-nspawn") is not None


def _find_pool_config(user: str) -> tuple[str, str]:
    """Find the user's PHP-FPM pool config and PHP version.

    Returns (pool_conf_path, php_version).  Raises IsolatorError if not found.
    """
    for pattern in _cfg.FPM_POOL_PATHS:
        for conf in glob_mod.glob(pattern.format(user=user)):
            # Extract PHP version from path (e.g., /etc/php/8.4/fpm/pool.d/user.conf)
            match = re.search(r"/php/(\d+\.\d+)/", conf)
            php_version = match.group(1) if match else "8.4"
            return conf, php_version
    raise IsolatorError(f"No PHP-FPM pool config found for {user!r}")


async def create(
    user: str,
    memory: str = _cfg.DEFAULT_MEMORY,
    cpu: str = _cfg.DEFAULT_CPU,
) -> dict:
    """Create a container for the given user.

    Finds the user's PHP-FPM pool config, builds the rootfs, writes systemd
    unit files.  Does NOT start the container — call start() after.

    Returns a dict with creation details.
    """
    _validate_user(user)

    try:
        validate_memory(memory)
        validate_cpu(cpu)
    except ValueError as e:
        raise IsolatorError(str(e))

    if not is_available():
        raise IsolatorError("systemd-nspawn is not installed")

    # Find pool config and PHP version
    pool_conf, php_version = _find_pool_config(user)

    # Build rootfs (raises KeyError if user doesn't exist)
    try:
        rootfs_path = create_rootfs(user)
    except KeyError:
        raise IsolatorError(f"User {user!r} does not exist on this system")

    # Write unit files — container will run PHP-FPM with this user's pool
    write_nspawn_unit(user, php_version=php_version, pool_conf=pool_conf)
    write_service_dropin(user, memory=memory, cpu=cpu)

    # Reload systemd to pick up new units
    rc, _, err = await _run(["systemctl", "daemon-reload"])
    if rc != 0:
        logger.warning("systemctl daemon-reload failed: %s", err)

    # Enable auto-start on boot
    rc, _, err = await _run(["systemctl", "enable", service_name(user)])
    if rc != 0:
        logger.warning("systemctl enable failed: %s", err)

    logger.info("Created container for %s (PHP %s, pool: %s)", user, php_version, pool_conf)
    return {
        "user": user,
        "rootfs": str(rootfs_path),
        "php_version": php_version,
        "pool_conf": pool_conf,
        "memory": memory,
        "cpu": cpu,
    }


async def destroy(user: str) -> bool:
    """Destroy a container: stop if running, remove rootfs and unit files.

    Returns True if anything was removed, False if nothing existed.
    """
    _validate_user(user)

    existed = False

    # Stop first if running
    st = await status(user)
    if st.get("state") == "running":
        await stop(user)

    # Disable auto-start
    machine = _machine_name(user)
    rc, _, err = await _run(["systemctl", "disable", service_name(user)])
    if rc != 0:
        logger.warning("systemctl disable failed: %s", err)

    # Remove unit files
    if unit_files_exist(user):
        remove_unit_files(user)
        existed = True

    # Remove rootfs
    if destroy_rootfs(user):
        existed = True

    if existed:
        await _run(["systemctl", "daemon-reload"])
        logger.info("Destroyed container for %s", user)

    return existed


async def start(user: str) -> bool:
    """Start a container.  Returns True on success."""
    _validate_user(user)
    machine = _machine_name(user)

    if not rootfs_exists(user):
        raise IsolatorError(f"Container for {user!r} does not exist — run create first")

    rc, out, err = await _run(["machinectl", "start", machine])
    if rc != 0:
        raise IsolatorError(f"Failed to start {machine}: {err}")

    logger.info("Started container %s", machine)
    return True


async def stop(user: str) -> bool:
    """Stop a running container.  Returns True on success."""
    _validate_user(user)
    machine = _machine_name(user)

    rc, out, err = await _run(["machinectl", "stop", machine])
    if rc != 0:
        # Not running is not an error
        if "not running" in err.lower() or "not found" in err.lower():
            return True
        raise IsolatorError(f"Failed to stop {machine}: {err}")

    logger.info("Stopped container %s", machine)
    return True


async def restart(user: str) -> bool:
    """Restart a container."""
    await stop(user)
    return await start(user)


async def status(user: str) -> dict:
    """Get container status.

    Returns a dict with keys: user, machine, state, exists.
    State is "running", "stopped", or "missing".
    """
    _validate_user(user)
    machine = _machine_name(user)
    exists = rootfs_exists(user)

    if not exists:
        return {"user": user, "machine": machine, "state": "missing", "exists": False, "enabled": False}

    (rc, out, err), (rc_en, out_en, _) = await asyncio.gather(
        _run(["machinectl", "show", machine, "--property=State"], timeout=10),
        _run(["systemctl", "is-enabled", service_name(user)], timeout=10),
    )
    enabled = rc_en == 0 and out_en.strip().startswith("enabled")

    if rc != 0:
        return {"user": user, "machine": machine, "state": "stopped", "exists": True, "enabled": enabled}

    # Parse "State=running" or "State=closing" etc.
    state = "stopped"
    for line in out.splitlines():
        if line.startswith("State="):
            state = line.split("=", 1)[1].strip()
            break

    return {"user": user, "machine": machine, "state": state, "exists": True, "enabled": enabled}


async def list_all() -> list[dict]:
    """List all jabali-isolator managed containers.

    Scans /var/lib/machines/*{SUFFIX}/ directories.
    """
    from jabali_isolator.machine import SUFFIX
    from jabali_isolator import config

    machines = Path(config.MACHINES_DIR)
    if not machines.is_dir():
        return []

    containers = []
    for entry in sorted(machines.iterdir()):
        if entry.is_dir() and entry.name.endswith(SUFFIX):
            user = entry.name.removesuffix(SUFFIX)
            info = await status(user)
            containers.append(info)

    return containers
