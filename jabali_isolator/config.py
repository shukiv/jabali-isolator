"""Default paths and resource limits."""

MACHINES_DIR = "/var/lib/machines"
NSPAWN_DIR = "/etc/systemd/nspawn"
SOCKET_DIR = "/run/jabali-fpm"
SERVICE_DROPIN_BASE = "/etc/systemd/system"

DEFAULT_MEMORY = "512M"
DEFAULT_CPU = "100%"

# Directories to bind-mount read-only from the host into every container.
HOST_RO_BINDS = ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/php", "/etc/ssl/certs"]

# Username validation: only allow safe characters (no shell metacharacters).
USERNAME_RE = r"^[a-z_][a-z0-9_.-]{0,31}$"
