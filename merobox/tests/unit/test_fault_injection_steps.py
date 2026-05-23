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
