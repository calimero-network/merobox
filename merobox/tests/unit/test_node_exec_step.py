"""
Unit tests for the node_exec step.

The step's job is to run a `merod` subcommand offline — against a stopped node's
data directory — so the tests centre on the three things that make that safe:
the running-node refusal, reading the image and mount off the container rather
than reconstructing them, and keeping input files inside the mount.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.node_exec import NodeExecStep

CONTAINER_HOME = "/app/data"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _manager(
    tmp_path, running=False, image="merod:local", mounted=True, node="calimero-node-2"
):
    """A manager whose container mounts `tmp_path` at /app/data.

    The node's home is created inside it, because that is what `--home /app/data
    --node <name>` actually reads — a mount with no home in it is the shape that
    used to pass validation and fail inside merod.
    """
    (tmp_path / node).mkdir(parents=True, exist_ok=True)
    manager = MagicMock()
    manager.is_node_running.return_value = running

    container = MagicMock()
    container.attrs = {
        "Config": {"Image": image},
        "Mounts": (
            [{"Destination": CONTAINER_HOME, "Source": str(tmp_path)}]
            if mounted
            else []
        ),
    }
    manager.client.containers.get.return_value = container
    return manager


class TestNodeExecValidation:
    def setup_method(self):
        self.config = {
            "type": "node_exec",
            "name": "Export",
            "node": "calimero-node-2",
            "args": ["account", "export"],
        }

    def test_valid_config_passes(self):
        NodeExecStep(self.config)

    @pytest.mark.parametrize("field", ["node", "args"])
    def test_missing_required_field_raises(self, field):
        config = {**self.config}
        del config[field]
        with pytest.raises(ValueError, match=field):
            NodeExecStep(config)

    def test_args_must_be_a_list_of_strings(self):
        with pytest.raises(ValueError, match="args"):
            NodeExecStep({**self.config, "args": "account export"})

    def test_files_must_be_a_mapping(self):
        with pytest.raises(ValueError, match="files"):
            NodeExecStep({**self.config, "files": ["/app/data/x"]})


class TestNodeExecExecution:
    def setup_method(self):
        self.config = {
            "type": "node_exec",
            "name": "Export",
            "node": "calimero-node-2",
            "args": ["account", "export"],
        }

    def _step(self, manager, config=None):
        return NodeExecStep(config or self.config, manager=manager)

    def test_refuses_while_the_node_is_running(self, tmp_path):
        """RocksDB's lock is exclusive, so this must fail loudly up front.

        Attempting it produces an opaque lock error several layers down, which is
        exactly the kind of failure that gets misread as a node fault.
        """
        manager = _manager(tmp_path, running=True)
        step = self._step(manager)
        assert _run(step.execute({}, {})) is False
        manager.client.containers.create.assert_not_called()

    def test_allow_running_overrides_the_refusal(self, tmp_path):
        manager = _manager(tmp_path, running=True)
        step = self._step(manager, {**self.config, "allow_running": True})
        with patch.object(
            manager.client.containers, "create", return_value=_stub_container()
        ):
            assert _run(step.execute({}, {})) is True

    def test_runs_merod_with_the_home_and_node_flags(self, tmp_path):
        manager = _manager(tmp_path)
        step = self._step(manager)
        stub = _stub_container(stdout="word word word\nAccount root public key: X\n")
        with patch.object(
            manager.client.containers, "create", return_value=stub
        ) as create:
            results = {}
            assert _run(step.execute(results, {})) is True

        kwargs = create.call_args.kwargs
        assert kwargs["image"] == "merod:local"
        # `merod` as argv[0] with the entrypoint cleared — the same shape
        # `run_node` uses, rather than trusting every image's ENTRYPOINT.
        assert kwargs["entrypoint"] == ""
        # root, or the image's `USER user` cannot traverse the 0700 host-owned
        # data directory and merod reports the node as uninitialised.
        assert kwargs["user"] == "root"
        assert kwargs["command"] == [
            "merod",
            "--home",
            CONTAINER_HOME,
            "--node",
            "calimero-node-2",
            "account",
            "export",
        ]
        assert kwargs["volumes"] == {
            str(tmp_path): {"bind": CONTAINER_HOME, "mode": "rw"}
        }
        # `stdout_first_line` is the point of the field: a single-value command
        # prints its value first and advisory text after.
        data = results["exec_calimero-node-2"]
        assert data["stdout_first_line"] == "word word word"
        assert data["exit_code"] == 0

    def test_a_nonzero_exit_fails_the_step(self, tmp_path):
        manager = _manager(tmp_path)
        step = self._step(manager)
        stub = _stub_container(stdout="", stderr="no account root yet", exit_code=1)
        with patch.object(manager.client.containers, "create", return_value=stub):
            assert _run(step.execute({}, {})) is False
        stub.remove.assert_called_once()

    def test_the_one_shot_container_is_always_removed(self, tmp_path):
        """Even when the command fails — a scenario must not leak containers."""
        manager = _manager(tmp_path)
        step = self._step(manager)
        stub = _stub_container(exit_code=2)
        with patch.object(manager.client.containers, "create", return_value=stub):
            _run(step.execute({}, {}))
        stub.remove.assert_called_once_with(force=True)

    def test_writes_input_files_into_the_mount(self, tmp_path):
        """How a command that reads a file gets its input, with no stdin plumbing."""
        manager = _manager(tmp_path)
        config = {
            **self.config,
            "args": ["account", "import", "--from", f"{CONTAINER_HOME}/phrase.txt"],
            "files": {f"{CONTAINER_HOME}/phrase.txt": "{{phrase}}"},
        }
        step = self._step(manager, config)
        with patch.object(
            manager.client.containers, "create", return_value=_stub_container()
        ):
            assert _run(step.execute({}, {"phrase": "word word word"})) is True

        written = (tmp_path / "phrase.txt").read_text()
        assert written == "word word word\n", "a trailing newline is added for the CLI"

    def test_refuses_a_file_outside_the_mount(self, tmp_path):
        manager = _manager(tmp_path)
        config = {**self.config, "files": {"/etc/passwd": "nope"}}
        step = self._step(manager, config)
        assert _run(step.execute({}, {})) is False
        manager.client.containers.create.assert_not_called()

    def test_fails_when_the_node_has_no_data_mount(self, tmp_path):
        manager = _manager(tmp_path, mounted=False)
        step = self._step(manager)
        assert _run(step.execute({}, {})) is False

    def test_fails_for_a_remote_node_with_no_docker_manager(self):
        step = NodeExecStep(self.config, manager=None)
        assert _run(step.execute({}, {})) is False


def _stub_container(stdout="", stderr="", exit_code=0):
    """A docker-py container double that reports fixed logs and exit status."""
    container = MagicMock()
    container.wait.return_value = {"StatusCode": exit_code}

    def logs(stdout=False, stderr=False):  # noqa: A002 - mirrors docker-py's kwargs
        if stdout:
            return _stub_container.out
        return _stub_container.err

    _stub_container.out = stdout.encode()
    _stub_container.err = stderr.encode()
    container.logs.side_effect = logs
    return container


class TestNodeExecExpectedFailure:
    """`expected_failure` is a per-step contract here, so it needs its own cover.

    Without it a scenario asserting "importing without --force is refused" would
    pass whether or not the guard exists.
    """

    def setup_method(self):
        self.config = {
            "type": "node_exec",
            "name": "Import without force",
            "node": "calimero-node-2",
            "args": ["account", "import", "--from", f"{CONTAINER_HOME}/p.txt"],
            "expected_failure": True,
        }

    def test_expected_failure_must_be_a_bool(self):
        with pytest.raises(ValueError, match="expected_failure"):
            NodeExecStep({**self.config, "expected_failure": "yes"})

    def test_a_refused_command_passes_the_step(self, tmp_path):
        manager = _manager(tmp_path)
        step = NodeExecStep(self.config, manager=manager)
        stub = _stub_container(stderr="already has an account root", exit_code=1)
        with patch.object(manager.client.containers, "create", return_value=stub):
            assert _run(step.execute({}, {})) is True

    def test_an_unexpected_success_fails_the_step(self, tmp_path):
        """The half that makes the assertion mean something."""
        manager = _manager(tmp_path)
        step = NodeExecStep(self.config, manager=manager)
        with patch.object(
            manager.client.containers, "create", return_value=_stub_container()
        ):
            assert _run(step.execute({}, {})) is False


def test_node_exec_exports_its_outputs(tmp_path):
    """`outputs: { phrase: stdout_first_line }` must reach dynamic_values.

    Same hole as the account steps had: recording the result is not exporting it,
    and a scenario that captures a recovery phrase this way would otherwise carry
    the literal `{{phrase}}` into the next command.
    """
    manager = _manager(tmp_path)
    step = NodeExecStep(
        {
            "type": "node_exec",
            "name": "Export",
            "node": "calimero-node-2",
            "args": ["account", "export"],
            "outputs": {"phrase": "stdout_first_line", "code": "exit_code"},
        },
        manager=manager,
    )
    stub = _stub_container(stdout="word word word\nAccount root public key: X\n")
    with patch.object(manager.client.containers, "create", return_value=stub):
        dynamic_values = {}
        assert _run(step.execute({}, dynamic_values)) is True

    assert dynamic_values["phrase"] == "word word word"
    assert dynamic_values["code"] == 0


class TestNodeExecAfterTheContainerIsGone:
    """`stop_node` REMOVES the container, so this is the main path, not an edge.

    The first version read the image and bind mount off the container and failed
    the moment it was used for what it exists for: running an offline command
    against a stopped node. `_graceful_stop_containers_batch` stops *and* removes.
    """

    def _step(self, config, manager):
        return NodeExecStep(config, manager=manager)

    def _gone(self, tmp_path, node_images=None):
        """A manager whose container lookup raises, as docker-py does for a
        removed container."""
        manager = MagicMock()
        manager.is_node_running.return_value = False
        manager.client.containers.get.side_effect = RuntimeError("No such container")
        manager.node_images = node_images if node_images is not None else {}
        return manager

    def test_falls_back_to_the_manager_record_and_the_data_convention(self, tmp_path):
        manager = self._gone(tmp_path, {"calimero-node-2": "merod:local"})
        data = tmp_path / "data" / "calimero-node-2"
        (data / "calimero-node-2").mkdir(parents=True)
        step = self._step(
            {
                "type": "node_exec",
                "name": "Export",
                "node": "calimero-node-2",
                "args": ["account", "export"],
                "data_dir": str(data),
            },
            manager,
        )
        with patch.object(
            manager.client.containers, "create", return_value=_stub_container()
        ) as create:
            assert _run(step.execute({}, {})) is True
        assert create.call_args.kwargs["image"] == "merod:local"
        assert create.call_args.kwargs["volumes"] == {
            str(data): {"bind": CONTAINER_HOME, "mode": "rw"}
        }

    def test_explicit_image_wins_when_merobox_has_no_record(self, tmp_path):
        manager = self._gone(tmp_path)
        data = tmp_path / "home"
        (data / "calimero-node-2").mkdir(parents=True)
        step = self._step(
            {
                "type": "node_exec",
                "name": "Export",
                "node": "calimero-node-2",
                "args": ["account", "export"],
                "image": "merod:pinned",
                "data_dir": str(data),
            },
            manager,
        )
        with patch.object(
            manager.client.containers, "create", return_value=_stub_container()
        ) as create:
            assert _run(step.execute({}, {})) is True
        assert create.call_args.kwargs["image"] == "merod:pinned"

    def test_says_what_is_missing_when_the_image_cannot_be_resolved(self, tmp_path):
        """The original failure printed 'node_exec failed: node_exec failed'."""
        manager = self._gone(tmp_path)
        data = tmp_path / "home"
        (data / "calimero-node-2").mkdir(parents=True)
        step = self._step(
            {
                "type": "node_exec",
                "name": "Export",
                "node": "calimero-node-2",
                "args": ["account", "export"],
                "data_dir": str(data),
            },
            manager,
        )
        assert _run(step.execute({}, {})) is False

    def test_a_missing_data_directory_is_reported(self, tmp_path):
        manager = self._gone(tmp_path, {"calimero-node-2": "merod:local"})
        step = self._step(
            {
                "type": "node_exec",
                "name": "Export",
                "node": "calimero-node-2",
                "args": ["account", "export"],
                "data_dir": str(tmp_path / "nope"),
            },
            manager,
        )
        assert _run(step.execute({}, {})) is False


class TestNodeExecUsesTheRecordedDataDir:
    """The manager records each node's absolute config path; trust it over conventions.

    Rebuilding `./data/<node>` assumes the CWD has not moved and no custom data
    dir was used — the manager keeps `node_config_files` precisely because that
    assumption breaks. Reconstructing it is what sent an export at a directory
    that existed and held nothing, failing inside merod with "Node is not
    initialized" and a path the log never printed.
    """

    def test_prefers_the_recorded_config_path_over_the_convention(self, tmp_path):
        elsewhere = tmp_path / "somewhere-else" / "calimero-node-2"
        (elsewhere / "calimero-node-2").mkdir(parents=True)

        manager = MagicMock()
        manager.is_node_running.return_value = False
        manager.client.containers.get.side_effect = RuntimeError("No such container")
        manager.node_images = {"calimero-node-2": "merod:local"}
        manager.node_config_files = {
            "calimero-node-2": str(elsewhere / "calimero-node-2" / "config.toml")
        }

        step = NodeExecStep(
            {
                "type": "node_exec",
                "name": "Export",
                "node": "calimero-node-2",
                "args": ["account", "export"],
            },
            manager=manager,
        )
        with patch.object(
            manager.client.containers, "create", return_value=_stub_container()
        ) as create:
            assert _run(step.execute({}, {})) is True

        assert create.call_args.kwargs["volumes"] == {
            str(elsewhere): {"bind": CONTAINER_HOME, "mode": "rw"}
        }

    def test_a_directory_without_the_node_home_is_rejected_up_front(self, tmp_path):
        """Not merely `isdir(source)`: the home inside it is what merod reads."""
        empty = tmp_path / "data" / "calimero-node-2"
        empty.mkdir(parents=True)

        manager = MagicMock()
        manager.is_node_running.return_value = False
        manager.client.containers.get.side_effect = RuntimeError("No such container")
        manager.node_images = {"calimero-node-2": "merod:local"}
        manager.node_config_files = {}

        step = NodeExecStep(
            {
                "type": "node_exec",
                "name": "Export",
                "node": "calimero-node-2",
                "args": ["account", "export"],
                "data_dir": str(empty),
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False
        manager.client.containers.create.assert_not_called()
