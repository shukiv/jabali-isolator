# jabali-isolator

PHP-FPM container isolation via systemd-nspawn. Each hosting user gets a lightweight container with mount/PID isolation and cgroup resource limits.

## Architecture

```
config.py        Constants, validation (USERNAME_RE, validate_memory/cpu)
machine.py       Single source of truth for {user}-php naming + all derived paths
rootfs.py        Minimal rootfs builder (passwd, group, resolv.conf, mount-point dirs)
units.py         .nspawn unit + service drop-in generators, validates inputs before interpolation
container.py     Async orchestrator — create/start/stop/destroy/status/list with per-user fcntl locking
__main__.py      Click CLI entry point (jabali-isolate)
```

Dependency flow: `config` <- `machine` <- `rootfs`/`units` <- `container` <- `__main__`

## Key Design Decisions

- All subprocess calls use list args via `asyncio.create_subprocess_exec` (no shell)
- Username validated with `^[a-z_][a-z0-9_.-]{0,31}$` at every public entry point
- Per-user advisory file lock (`fcntl.flock` on `/run/jabali-isolator/{user}.lock`) prevents concurrent create/destroy/start/stop races
- `_stop_unlocked()` internal helper avoids lock re-acquisition in destroy/restart
- `status()` and `list_all()` are read-only and do NOT lock
- `list_all()` uses `asyncio.gather()` for parallel status checks
- `machine.py` uses late-bound config (`from jabali_isolator import config` then `config.MACHINES_DIR`) so monkeypatch works from a single patch site
- Resource limits and php_version/pool_conf are validated before writing to systemd unit files
- `shutil.rmtree()` paths are canonicalized and verified to stay under expected parent dirs

## Build & Test

```bash
uv sync                    # install deps
uv run pytest -v           # run tests (53 tests)
uv run ruff check .        # lint
uv run ruff format .       # format
```

## Testing Conventions

- Shared fixtures in `tests/conftest.py`: `isolator_dirs` (patches all config paths to tmp_path), `fake_pool`, `fake_user`
- Async tests use `@pytest.mark.asyncio` with `asyncio_mode = "auto"`
- Subprocess calls mocked via `patch("jabali_isolator.container._run", new_callable=AsyncMock)`
- User lookup mocked via `patch("jabali_isolator.rootfs._lookup_user")`

## Git

- Remote: `gitea:shukivaknin/jabali-isolator.git` (SSH alias — port 2222 + proxy)
- Branch: `master`
