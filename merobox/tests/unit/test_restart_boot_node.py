"""restart_container on a topology boot-node.

The boot-node is the `boot-node` binary, not a merod: it has no admin API,
so the step must wait on its own listen log line instead of polling
`/admin-api/health` (which would hit whatever merod owns the default port).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from merobox.commands.bootstrap.steps import restart as restart_mod
from merobox.commands.bootstrap.steps.restart import RestartContainerStep

LISTEN_LINE = b'INFO boot_node: Listening on "/ip4/172.30.0.2/tcp/4001"\n'


class _FakeContainer:
    """`docker restart` appends to the same log file, so old lines survive."""

    def __init__(self, labels, log, lines_added_by_restart=b""):
        self.labels = labels
        self._log = log
        self._added = lines_added_by_restart

    def logs(self, **_kwargs):
        return self._log

    def restart(self):
        self._log += self._added

    def reload(self):
        return None


def _step(timeout=2):
    return RestartContainerStep(
        {"type": "restart_container", "container": "wf-boot-node", "timeout": timeout}
    )


@pytest.fixture
def run_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("MEROBOX_RESTART_LOG_DIR", str(tmp_path))

    async def run(container, timeout=2):
        monkeypatch.setattr(restart_mod, "is_binary_mode", lambda _m: False)
        monkeypatch.setattr(restart_mod, "resolve_container", lambda _m, _n: container)
        step = _step(timeout)
        step.manager = SimpleNamespace()
        return await step.execute({}, {})

    return run


@pytest.fixture
def no_admin_health(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("boot-node has no admin API to poll")

    monkeypatch.setattr(restart_mod, "get_node_rpc_url", boom)


@pytest.mark.asyncio
async def test_boot_node_restart_waits_for_its_new_listen_line(
    run_restart, no_admin_health
):
    container = _FakeContainer({"merobox.role": "boot-node"}, LISTEN_LINE, LISTEN_LINE)
    assert await run_restart(container) is True


@pytest.mark.asyncio
async def test_boot_node_restart_ignores_listen_lines_from_before_the_restart(
    run_restart, no_admin_health
):
    container = _FakeContainer({"merobox.role": "boot-node"}, LISTEN_LINE)
    assert await run_restart(container, timeout=1) is False


@pytest.mark.asyncio
async def test_merod_restart_still_polls_admin_health(run_restart, monkeypatch):
    wait = AsyncMock(return_value=True)
    monkeypatch.setattr(RestartContainerStep, "_wait_healthy", wait)
    container = _FakeContainer({}, b"")
    container.logs = MagicMock(return_value=b"")

    assert await run_restart(container) is True
    wait.assert_awaited_once()
