"""systemd unit file generators for nspawn containers.

Generates two files per container:
  1. /etc/systemd/nspawn/{user}-php.nspawn     — container filesystem + network config
  2. /etc/systemd/system/systemd-nspawn@{user}-php.service.d/limits.conf — resource limits
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from jabali_isolator.config import DEFAULT_CPU, DEFAULT_MEMORY, HOST_RO_BINDS, SOCKET_DIR
from jabali_isolator.machine import dropin_dir, nspawn_path

logger = logging.getLogger(__name__)


def _validate_nspawn_inputs(user: str, php_version: str, pool_conf: str) -> None:
    """Validate inputs before interpolating into systemd unit files."""
    import re

    from jabali_isolator.config import USERNAME_RE

    if not re.match(USERNAME_RE, user):
        raise ValueError(f"Invalid username for unit file: {user!r}")
    if not re.match(r"^\d+\.\d+$", php_version):
        raise ValueError(f"Invalid PHP version: {php_version!r}")
    if pool_conf and ("\n" in pool_conf or "\r" in pool_conf or not pool_conf.startswith("/")):
        raise ValueError(f"Invalid pool config path: {pool_conf!r}")


def generate_nspawn_unit(user: str, php_version: str = "8.4", pool_conf: str = "") -> str:
    """Return the content of a .nspawn unit file for the given user.

    The container runs PHP-FPM with the user's pool config as its main process.
    """
    _validate_nspawn_inputs(user, php_version, pool_conf)
    ro_lines = "\n".join(f"BindReadOnly={p}" for p in HOST_RO_BINDS)

    # Bind-mount the user's pool config read-only
    if pool_conf:
        pool_bind = f"BindReadOnly={pool_conf}"
    else:
        pool_bind = f"BindReadOnly=/etc/php/{php_version}/fpm/pool.d/{user}.conf"

    # Bind the PHP config directory (read-only)
    php_conf_bind = "BindReadOnly=/etc/php"

    # Bind /run/php so the FPM socket lands where nginx expects it
    socket_bind = f"Bind={SOCKET_DIR}"

    fpm_bin = f"/usr/sbin/php-fpm{php_version}"

    return f"""\
# Managed by jabali-isolator — do not edit manually
[Exec]
Boot=no
ProcessTwo=yes
Parameters={fpm_bin} --nodaemonize --fpm-config /etc/php/{php_version}/fpm/pool.d/{user}.conf

[Files]
{ro_lines}
{php_conf_bind}
Bind=/home/{user}
{socket_bind}
{pool_bind}
TemporaryFileSystem=/tmp:mode=1777

[Network]
VirtualEthernet=no
"""


def generate_service_dropin(memory: str = DEFAULT_MEMORY, cpu: str = DEFAULT_CPU) -> str:
    """Return the content of a systemd service drop-in for resource limits."""
    return f"""\
# Managed by jabali-isolator — do not edit manually
[Service]
MemoryMax={memory}
CPUQuota={cpu}
"""


def write_nspawn_unit(user: str, php_version: str = "8.4", pool_conf: str = "") -> Path:
    """Write the .nspawn unit file.  Returns the file path."""
    path = nspawn_path(user)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_nspawn_unit(user, php_version=php_version, pool_conf=pool_conf))
    os.chmod(path, 0o644)
    logger.info("Wrote nspawn unit: %s", path)
    return path


def write_service_dropin(user: str, memory: str = DEFAULT_MEMORY, cpu: str = DEFAULT_CPU) -> Path:
    """Write the service drop-in for resource limits.  Returns the drop-in path."""
    dropin = dropin_dir(user)
    dropin.mkdir(parents=True, exist_ok=True)
    conf = dropin / "limits.conf"
    conf.write_text(generate_service_dropin(memory, cpu))
    os.chmod(conf, 0o644)
    logger.info("Wrote service drop-in: %s", conf)
    return conf


def remove_unit_files(user: str) -> None:
    """Remove the .nspawn file and service drop-in directory for a user."""
    np = nspawn_path(user)
    if np.exists():
        np.unlink()
        logger.info("Removed %s", np)

    dd = dropin_dir(user)
    if dd.exists():
        from jabali_isolator.config import SERVICE_DROPIN_BASE

        resolved = dd.resolve()
        expected_parent = Path(SERVICE_DROPIN_BASE).resolve()
        if not resolved.is_relative_to(expected_parent):
            raise RuntimeError(f"Dropin path escapes SERVICE_DROPIN_BASE: {dd} -> {resolved}")
        shutil.rmtree(dd)
        logger.info("Removed %s", dd)


def unit_files_exist(user: str) -> bool:
    return nspawn_path(user).exists()
