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
