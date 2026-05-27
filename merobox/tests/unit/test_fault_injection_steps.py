"""BaseStep-level validation for the network fault-injection step family.

These tests exercise the step constructors directly (rather than the YAML
schema layer), which catches misconfigurations even if a workflow bypasses
schema validation.
"""

import pytest

from merobox.commands.bootstrap.steps.fault import InjectNetworkFaultStep
from merobox.commands.bootstrap.steps.network import (
    ConnectNodeStep,
    DisconnectNodeStep,
)
from merobox.commands.bootstrap.steps.pause import (
    PauseContainerStep,
    UnpauseContainerStep,
)
from merobox.commands.bootstrap.steps.restart import RestartContainerStep


class TestPauseStepValidation:
    def test_construct_minimal(self):
        PauseContainerStep({"type": "pause_container", "container": "node-1"})

    def test_missing_container_rejected(self):
        with pytest.raises(ValueError, match="container"):
            PauseContainerStep({"type": "pause_container"})

    def test_unpause_construct_minimal(self):
        UnpauseContainerStep({"type": "unpause_container", "container": "node-1"})


class TestRestartStepValidation:
    def test_construct_minimal(self):
        RestartContainerStep({"type": "restart_container", "container": "node-1"})

    def test_wait_healthy_must_be_bool(self):
        with pytest.raises(ValueError, match="wait_healthy"):
            RestartContainerStep(
                {
                    "type": "restart_container",
                    "container": "node-1",
                    "wait_healthy": "yes",
                }
            )

    def test_timeout_must_be_positive(self):
        with pytest.raises(ValueError, match="timeout"):
            RestartContainerStep(
                {
                    "type": "restart_container",
                    "container": "node-1",
                    "timeout": -1,
                }
            )


class TestNetworkStepValidation:
    def test_disconnect_minimal(self):
        DisconnectNodeStep({"type": "disconnect_node", "node": "node-1"})

    def test_connect_with_custom_network(self):
        ConnectNodeStep(
            {
                "type": "connect_node",
                "node": "node-1",
                "network": "calimero_internal",
            }
        )

    def test_disconnect_missing_node(self):
        with pytest.raises(ValueError, match="node"):
            DisconnectNodeStep({"type": "disconnect_node"})


class TestInjectFaultStepValidation:
    def test_loss_valid(self):
        InjectNetworkFaultStep(
            {
                "type": "inject_network_fault",
                "container": "node-1",
                "fault": "loss",
                "percent": 30,
                "duration": 5,
            }
        )

    def test_delay_valid(self):
        InjectNetworkFaultStep(
            {
                "type": "inject_network_fault",
                "container": "node-1",
                "fault": "delay",
                "ms": 500,
                "duration": 10,
            }
        )

    def test_partition_fault_rejected_use_disconnect_node(self):
        """fault=partition is intentionally absent — disconnect_node owns it."""
        with pytest.raises(ValueError, match="fault"):
            InjectNetworkFaultStep(
                {
                    "type": "inject_network_fault",
                    "container": "node-1",
                    "fault": "partition",
                    "duration": 5,
                }
            )

    def test_loss_without_percent_rejected(self):
        with pytest.raises(ValueError, match="percent"):
            InjectNetworkFaultStep(
                {
                    "type": "inject_network_fault",
                    "container": "node-1",
                    "fault": "loss",
                    "duration": 5,
                }
            )

    def test_loss_out_of_range_percent_rejected(self):
        with pytest.raises(ValueError, match="percent"):
            InjectNetworkFaultStep(
                {
                    "type": "inject_network_fault",
                    "container": "node-1",
                    "fault": "loss",
                    "percent": 150,
                    "duration": 5,
                }
            )

    def test_delay_without_ms_rejected(self):
        with pytest.raises(ValueError, match="ms"):
            InjectNetworkFaultStep(
                {
                    "type": "inject_network_fault",
                    "container": "node-1",
                    "fault": "delay",
                    "duration": 5,
                }
            )

    def test_duration_must_be_positive(self):
        with pytest.raises(ValueError, match="duration"):
            InjectNetworkFaultStep(
                {
                    "type": "inject_network_fault",
                    "container": "node-1",
                    "fault": "loss",
                    "percent": 10,
                    "duration": 0,
                }
            )

    def test_interface_must_match_linux_naming(self):
        with pytest.raises(ValueError, match="interface"):
            InjectNetworkFaultStep(
                {
                    "type": "inject_network_fault",
                    "container": "node-1",
                    "fault": "loss",
                    "percent": 10,
                    "duration": 5,
                    # Shell metachars / spaces are rejected even though
                    # exec_run passes argv as a list (defense in depth).
                    "interface": "eth0; rm -rf /",
                }
            )


class TestDetectNodeNetwork:
    """detect_node_network picks the right partition target by introspecting
    the container's actual attached networks."""

    @staticmethod
    def _container(networks: dict):
        class FakeContainer:
            def __init__(self):
                self.attrs = {"NetworkSettings": {"Networks": networks}}

            def reload(self):
                pass

        return FakeContainer()

    def test_prefers_merobox_cluster(self):
        from merobox.commands.bootstrap.steps._docker_utils import (
            detect_node_network,
        )

        # bridge present too, but cluster wins.
        c = self._container({"bridge": {}, "merobox-cluster": {}})
        assert detect_node_network(c) == "merobox-cluster"

    def test_single_non_default_network_used(self):
        from merobox.commands.bootstrap.steps._docker_utils import (
            detect_node_network,
        )

        c = self._container({"calimero_web": {}})
        assert detect_node_network(c) == "calimero_web"

    def test_disconnected_container_defaults_to_bridge(self):
        from merobox.commands.bootstrap.steps._docker_utils import (
            detect_node_network,
        )

        # No networks attached → fall back to bridge (the universal Docker
        # default that always exists). connect_node short-circuits this
        # case via the partition-network dynamic value anyway.
        c = self._container({})
        assert detect_node_network(c) == "bridge"

    def test_skips_host_and_none_networks(self):
        from merobox.commands.bootstrap.steps._docker_utils import (
            detect_node_network,
        )

        c = self._container({"host": {}, "none": {}, "bridge": {}})
        assert detect_node_network(c) == "bridge"

    def test_auth_mode_multinetwork_prefers_web_over_internal(self):
        from merobox.commands.bootstrap.steps._docker_utils import (
            detect_node_network,
        )

        # Auth-mode containers are on calimero_web + calimero_internal.
        # calimero_web carries user-facing libp2p/RPC traffic — severing
        # it is what a partition test wants. calimero_internal is the
        # Traefik backend channel and is the WRONG target.
        c = self._container({"calimero_web": {}, "calimero_internal": {}})
        assert detect_node_network(c) == "calimero_web"

    def test_reload_failure_still_returns_a_network(self):
        from merobox.commands.bootstrap.steps._docker_utils import (
            detect_node_network,
        )

        class FlakyContainer:
            def __init__(self):
                self.attrs = {"NetworkSettings": {"Networks": {"merobox-cluster": {}}}}

            def reload(self):
                raise RuntimeError("daemon flake")

        # When container.reload() fails, the function should still pick a
        # network from whatever attrs it has, not crash.
        assert detect_node_network(FlakyContainer()) == "merobox-cluster"


# ---------------------------------------------------------------------------
# RestartContainerStep — pre/post-restart log snapshot
#
# The snapshot fires both before and after `container.restart()` so the
# whole restart cycle is captured (the CI watcher's `docker logs -f`
# follower doesn't survive the stop+start). These tests pin the
# contract:
#
#   - Snapshot writes to `<dir>/<container>.<phase>-<utc>.log`.
#   - `MEROBOX_RESTART_LOG_DIR` env var overrides the default dir;
#     `MEROBOX_PRE_RESTART_LOG_DIR` (legacy) still works as an alias.
#   - Failures at every step (mkdir, container.logs, file write) are
#     logged but never raise — the caller's restart MUST proceed.
#   - container_name is path-traversal-defanged via os.path.basename
#     before being used in the snapshot path.
# ---------------------------------------------------------------------------


def _stub_container(log_bytes=b""):
    """Stub `docker.Container` with a `logs()` method that returns
    fixed bytes/str. Module-level so both pre- and post-restart test
    classes can reuse it without duplicating the stub definition.
    The `reload()` no-op is required by `_snapshot_post_restart_logs`
    which calls it for cache-freshness — without it the post-restart
    tests would fail on an AttributeError before reaching the
    `logs()` call.
    """

    class _StubContainer:
        def logs(self, *, timestamps=False):
            # Implementation passes timestamps=True. The kwarg
            # is accepted-and-ignored to keep the stub honest.
            _ = timestamps
            return log_bytes

        def reload(self):
            # docker-py's Container.reload() refreshes the in-memory
            # attrs from the daemon. The unit-test stub doesn't need
            # to track state; the post-restart path just expects the
            # method to exist and not raise.
            return None

    return _StubContainer()


class TestRestartPreRestartLogSnapshot:
    @staticmethod
    def _container_with_logs(log_bytes):
        # Thin wrapper around the module-level `_stub_container` for
        # readability; preserved here so test bodies still read
        # `self._container_with_logs(...)`.
        return _stub_container(log_bytes)

    def test_snapshot_writes_expected_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEROBOX_PRE_RESTART_LOG_DIR", str(tmp_path))
        RestartContainerStep._snapshot_pre_restart_logs(
            self._container_with_logs(b"hello\nworld\n"),
            "sync-resil-node-1",
        )
        files = list(tmp_path.iterdir())
        assert len(files) == 1, f"expected exactly one snapshot file, got {files}"
        name = files[0].name
        assert name.startswith("sync-resil-node-1.pre-restart-"), name
        assert name.endswith(".log"), name
        # UTC timestamp is the 8-char date + T + 6-char time + 6-char micros + Z.
        # Just check it's present (any digit-heavy run between the prefix and `.log`).
        ts = name[len("sync-resil-node-1.pre-restart-") : -len(".log")]
        assert ts.endswith("Z") and "T" in ts, f"unexpected timestamp shape: {ts!r}"
        # File contents match what container.logs returned.
        assert files[0].read_text() == "hello\nworld\n"

    def test_snapshot_handles_str_logs(self, tmp_path, monkeypatch):
        # docker-py normally returns bytes from container.logs(), but
        # the helper has a `str` fallback path to defang any wrapper
        # that decoded it before returning. Pin that path so a future
        # type change doesn't regress to a TypeError.
        monkeypatch.setenv("MEROBOX_PRE_RESTART_LOG_DIR", str(tmp_path))
        RestartContainerStep._snapshot_pre_restart_logs(
            self._container_with_logs("already-decoded\n"),
            "n1",
        )
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == "already-decoded\n"

    def test_snapshot_returns_silently_when_container_logs_fail(
        self, tmp_path, monkeypatch
    ):
        # The whole point is to never block a restart. If `container.logs()`
        # raises (daemon hiccup, container in transient state, anything),
        # the snapshot must catch and log — not propagate.
        monkeypatch.setenv("MEROBOX_PRE_RESTART_LOG_DIR", str(tmp_path))

        class _BrokenContainer:
            def logs(self, *, timestamps=False):
                _ = timestamps
                raise RuntimeError("daemon flake")

        # Must NOT raise.
        RestartContainerStep._snapshot_pre_restart_logs(_BrokenContainer(), "n1")
        # No file produced.
        assert list(tmp_path.iterdir()) == []

    def test_snapshot_returns_silently_when_mkdir_fails(self, tmp_path, monkeypatch):
        # If the snapshot dir can't be created (read-only mount, no
        # permission, etc.), we must NOT raise. Point the env at a
        # path under a file (not a directory) so os.makedirs raises
        # NotADirectoryError, which the helper catches.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        bad_dir = blocker / "logs"
        monkeypatch.setenv("MEROBOX_PRE_RESTART_LOG_DIR", str(bad_dir))

        # Must NOT raise.
        RestartContainerStep._snapshot_pre_restart_logs(
            self._container_with_logs(b"unreachable\n"),
            "n1",
        )

    def test_snapshot_uses_default_dir_when_env_unset(self, tmp_path, monkeypatch):
        # No env var → defaults to ./docker-logs/ in CWD.
        monkeypatch.delenv("MEROBOX_PRE_RESTART_LOG_DIR", raising=False)
        monkeypatch.chdir(tmp_path)

        RestartContainerStep._snapshot_pre_restart_logs(
            self._container_with_logs(b"default-dir\n"),
            "n1",
        )

        default_dir = tmp_path / "docker-logs"
        assert default_dir.is_dir(), "default docker-logs/ should be created"
        files = list(default_dir.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == "default-dir\n"

    def test_snapshot_distinct_files_for_repeated_restarts(self, tmp_path, monkeypatch):
        # Two snapshots of the same container within the same workflow
        # must land in distinct files so the second doesn't clobber the
        # first. Achieved by the microsecond timestamp suffix.
        import time as _time

        monkeypatch.setenv("MEROBOX_PRE_RESTART_LOG_DIR", str(tmp_path))
        RestartContainerStep._snapshot_pre_restart_logs(
            self._container_with_logs(b"first\n"), "n1"
        )
        # Sleep ~1ms so the µs-resolution timestamp definitely advances.
        # Without this the two snapshots could land in the same µs on a
        # fast machine and clobber.
        _time.sleep(0.002)
        RestartContainerStep._snapshot_pre_restart_logs(
            self._container_with_logs(b"second\n"), "n1"
        )

        files = sorted(tmp_path.iterdir())
        assert len(files) == 2, files
        contents = sorted(f.read_text() for f in files)
        assert contents == ["first\n", "second\n"]


# ---------------------------------------------------------------------------
# RestartContainerStep — post-restart log snapshot
#
# After `container.restart()` and (optionally) wait_healthy, the step ALSO
# snapshots the newly-restarted container's logs. The CI watcher's
# `docker logs -f` follower doesn't follow across the stop+start cycle:
# the pre-restart container's log stream closes during the stop phase,
# the follower exits, and the watcher loop treats the name as
# already-tracked so it never reattaches. Without this second snapshot,
# the post-restart incarnation's logs are lost entirely.
# ---------------------------------------------------------------------------


class TestRestartPostRestartLogSnapshot:
    @staticmethod
    def _container_with_logs(log_bytes):
        # Thin wrapper around the module-level `_stub_container`
        # so this class's test bodies stay readable.
        return _stub_container(log_bytes)

    def test_pre_and_post_snapshots_land_in_distinct_files(self, tmp_path, monkeypatch):
        # Both phases write to the same directory. The UTC microsecond
        # timestamp in the filename guarantees they don't collide even
        # if called back-to-back within the same `docker restart` cycle.
        import time as _time

        monkeypatch.setenv("MEROBOX_PRE_RESTART_LOG_DIR", str(tmp_path))
        c = self._container_with_logs(b"hello\n")

        RestartContainerStep._snapshot_pre_restart_logs(c, "n1")
        _time.sleep(0.002)  # advance the µs timestamp
        RestartContainerStep._snapshot_post_restart_logs(c, "n1")

        files = sorted(p.name for p in tmp_path.iterdir())
        assert len(files) == 2, files
        pre = [f for f in files if ".pre-restart-" in f]
        post = [f for f in files if ".post-restart-" in f]
        assert len(pre) == 1 and pre[0].endswith(".log")
        assert len(post) == 1 and post[0].endswith(".log")
        # Filename format: `<container>.<phase>-<utc-ts>.log`
        assert pre[0].startswith("n1.pre-restart-")
        assert post[0].startswith("n1.post-restart-")

    def test_post_restart_snapshot_filename_carries_post_marker(
        self, tmp_path, monkeypatch
    ):
        # Pins the exact phase marker in the filename so downstream
        # tooling (CI artifact globs, log analyzers) can distinguish
        # the two phases by name alone.
        monkeypatch.setenv("MEROBOX_PRE_RESTART_LOG_DIR", str(tmp_path))
        RestartContainerStep._snapshot_post_restart_logs(
            self._container_with_logs(b"post-startup\n"),
            "sync-resil-node-1",
        )
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        name = files[0].name
        # Spelled exactly `.post-restart-` between the container name
        # and the timestamp — matches the pre-restart counterpart's
        # `.pre-restart-` convention.
        assert name.startswith("sync-resil-node-1.post-restart-")
        assert name.endswith(".log")
        assert files[0].read_text() == "post-startup\n"

    def test_post_restart_swallows_logs_failure(self, tmp_path, monkeypatch):
        # The whole point of best-effort: even if container.logs()
        # raises, we must not propagate. The restart already happened;
        # losing the snapshot is bad but losing the workflow run is
        # worse.
        monkeypatch.setenv("MEROBOX_PRE_RESTART_LOG_DIR", str(tmp_path))

        class _BrokenContainer:
            def logs(self, *, timestamps=False):
                _ = timestamps
                raise RuntimeError("daemon flake post-restart")

        # Must NOT raise.
        RestartContainerStep._snapshot_post_restart_logs(_BrokenContainer(), "n1")
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Env-var canonical + legacy alias precedence + path-traversal sanitization
# + post-restart reload — review-comment fixes on merobox#260.
# ---------------------------------------------------------------------------


class TestRestartLogSnapshotReviewFixes:
    def test_canonical_env_var_takes_precedence_over_legacy(
        self, tmp_path, monkeypatch
    ):
        # Both env vars set; the canonical `MEROBOX_RESTART_LOG_DIR`
        # must win. This is the path forward for callers updating
        # from the pre-0.6.23 name.
        canonical_dir = tmp_path / "canonical"
        legacy_dir = tmp_path / "legacy"
        monkeypatch.setenv("MEROBOX_RESTART_LOG_DIR", str(canonical_dir))
        monkeypatch.setenv("MEROBOX_PRE_RESTART_LOG_DIR", str(legacy_dir))
        RestartContainerStep._snapshot_pre_restart_logs(
            _stub_container(b"canonical-wins\n"), "n1"
        )
        # Canonical dir got the file; legacy dir was never written to.
        assert list(canonical_dir.iterdir()), "canonical dir must receive the snapshot"
        assert not legacy_dir.exists() or not list(
            legacy_dir.iterdir()
        ), "legacy dir must NOT receive a file when both env vars are set"

    def test_legacy_env_var_still_works_as_alias(self, tmp_path, monkeypatch):
        # Anyone who set `MEROBOX_PRE_RESTART_LOG_DIR` in their CI
        # before 0.6.23 shouldn't have to update simultaneously
        # with merobox.  Only the legacy name set → snapshot lands
        # in the legacy-named dir.
        monkeypatch.delenv("MEROBOX_RESTART_LOG_DIR", raising=False)
        monkeypatch.setenv("MEROBOX_PRE_RESTART_LOG_DIR", str(tmp_path))
        RestartContainerStep._snapshot_pre_restart_logs(
            _stub_container(b"legacy-works\n"), "n1"
        )
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == "legacy-works\n"

    def test_post_restart_calls_reload_for_cache_freshness(self, tmp_path, monkeypatch):
        # docker-py caches container attrs on the Python-side object.
        # `_snapshot_post_restart_logs` must call `container.reload()`
        # before reading logs so a stale handle from before
        # `docker restart` doesn't surface the wrong incarnation's
        # data. A stub that records calls confirms.
        monkeypatch.setenv("MEROBOX_RESTART_LOG_DIR", str(tmp_path))
        calls: list[str] = []

        class _RecordingContainer:
            def reload(self):
                calls.append("reload")

            def logs(self, *, timestamps=False):
                _ = timestamps
                calls.append("logs")
                return b"post-restart\n"

        RestartContainerStep._snapshot_post_restart_logs(_RecordingContainer(), "n1")
        # Order matters: reload BEFORE logs. If logs were read
        # first, the post-restart snapshot could return stale
        # pre-restart data on a docker-py version where logs()
        # uses cached attrs.
        assert calls == ["reload", "logs"], f"expected reload-then-logs, got {calls}"

    def test_post_restart_continues_when_reload_fails(self, tmp_path, monkeypatch):
        # The reload is cheap insurance, not a hard requirement.
        # docker-py's `logs()` re-queries on each call, so even a
        # stale Python handle gives correct results. If reload
        # raises (transient daemon issue), the snapshot must still
        # proceed.
        monkeypatch.setenv("MEROBOX_RESTART_LOG_DIR", str(tmp_path))

        class _FlakyReloadContainer:
            def reload(self):
                raise RuntimeError("daemon flake on reload")

            def logs(self, *, timestamps=False):
                _ = timestamps
                return b"despite-reload-flake\n"

        # Must NOT raise.
        RestartContainerStep._snapshot_post_restart_logs(_FlakyReloadContainer(), "n1")
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == "despite-reload-flake\n"

    def test_container_name_path_traversal_is_neutralized(self, tmp_path, monkeypatch):
        # If the workflow's `container:` field resolves (through
        # dynamic-value substitution) to a path-traversal-shaped
        # string, the snapshot filename construction must NOT let
        # the write escape the log dir. `os.path.basename` reduces
        # the name to a single filesystem component.
        monkeypatch.setenv("MEROBOX_RESTART_LOG_DIR", str(tmp_path))
        # `../../etc/evil` should collapse to just `evil` before
        # being used in the filename.
        RestartContainerStep._snapshot_pre_restart_logs(
            _stub_container(b"sanitized\n"), "../../etc/evil"
        )
        files = list(tmp_path.iterdir())
        assert len(files) == 1, f"expected a single file in {tmp_path}, got {files}"
        # File lives inside log_dir, no escape.
        assert files[0].parent == tmp_path
        # Basename is `evil`, not `../../etc/evil`.
        assert files[0].name.startswith("evil.pre-restart-")

    def test_empty_basename_falls_back_to_unnamed(self, tmp_path, monkeypatch):
        # `os.path.basename("/")` returns "" (and similar edge
        # cases). The snapshot mustn't try to write an
        # extension-only filename like `.pre-restart-….log`; the
        # fallback `unnamed` keeps the file readable.
        monkeypatch.setenv("MEROBOX_RESTART_LOG_DIR", str(tmp_path))
        RestartContainerStep._snapshot_pre_restart_logs(
            _stub_container(b"unnamed-fallback\n"), "/"
        )
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name.startswith("unnamed.pre-restart-")
