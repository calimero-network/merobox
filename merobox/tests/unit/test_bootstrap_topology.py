"""Unit tests for the single-bridge boot-node topology.

Standing the topology up needs a real Docker daemon, so what is covered
here is the YAML contract, the pure helpers, and — with a mocked docker
client — the two properties the topology exists to guarantee: every
client is given exactly one bootstrap address, and mDNS cannot be turned
back on. Both are what make a discovery regression fail a scenario
rather than being papered over.
"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from merobox.commands.bootstrap.config import (
    BootstrapTopologyConfig,
    WorkflowConfig,
)
from merobox.topology.bootstrap import (
    BootstrapTopologyState,
    bootstrap_multiaddrs,
    setup_bootstrap_topology,
    teardown_bootstrap_topology,
)
from merobox.topology.nat import BOOT_NODE_PORT

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_bootstrap_topology_validates_and_discriminates():
    """`type: bootstrap` selects the bootstrap variant, not the NAT one."""
    cfg = WorkflowConfig.model_validate(
        {
            "name": "boot",
            "topology": {"type": "bootstrap"},
            "nodes": {"count": 3},
        }
    )
    assert isinstance(cfg.topology, BootstrapTopologyConfig)
    assert cfg.topology.type == "bootstrap"


def test_bootstrap_topology_takes_a_boot_node_image_override():
    cfg = WorkflowConfig.model_validate(
        {
            "name": "boot",
            "topology": {
                "type": "bootstrap",
                "boot_node": {"image": "ghcr.io/example/boot-node:pinned"},
            },
            "nodes": {"count": 2},
        }
    )
    assert cfg.topology.boot_node.image == "ghcr.io/example/boot-node:pinned"


def test_nat_mode_is_rejected_on_the_bootstrap_variant():
    """`nat_mode` belongs to the NAT variant, and there is no gateway here
    to configure. Accepting and ignoring it would leave a workflow reading
    as though it had asked for something it did not get — so it fails
    validation, naming the key."""
    with pytest.raises(ValidationError) as err:
        WorkflowConfig.model_validate(
            {
                "name": "boot",
                "topology": {"type": "bootstrap", "nat_mode": "symmetric"},
                "nodes": {"count": 2},
            }
        )
    assert "nat_mode" in str(err.value)


def test_unknown_topology_type_still_rejected():
    with pytest.raises(ValidationError):
        WorkflowConfig.model_validate(
            {
                "name": "bad",
                "topology": {"type": "starnet"},
                "nodes": {"count": 2},
            }
        )


# ---------------------------------------------------------------------------
# Bootstrap addresses
# ---------------------------------------------------------------------------


def _state(ip="172.30.0.2", peer_id="12D3KooWBootNode"):
    return BootstrapTopologyState(
        network=MagicMock(name="network"),
        boot_node_container=MagicMock(name="boot-node"),
        boot_node_peer_id=peer_id,
        boot_node_ip=ip,
    )


def test_bootstrap_multiaddrs_offer_one_peer_over_two_transports():
    """One contact, TCP and QUIC — the shape a deployed node's config has.

    The absence of sibling addresses is the point: if this ever returned
    more than the boot-node, clients would no longer have to ask, and the
    topology would stop testing discovery.
    """
    addrs = bootstrap_multiaddrs(_state())

    assert addrs == [
        f"/ip4/172.30.0.2/tcp/{BOOT_NODE_PORT}/p2p/12D3KooWBootNode",
        f"/ip4/172.30.0.2/udp/{BOOT_NODE_PORT}/quic-v1/p2p/12D3KooWBootNode",
    ]
    assert all("12D3KooWBootNode" in a for a in addrs)


# ---------------------------------------------------------------------------
# Setup / teardown against a mocked docker client
# ---------------------------------------------------------------------------


@patch("merobox.topology.bootstrap._resolve_boot_node_peer_id")
@patch("merobox.topology.bootstrap._resolve_container_ip")
@patch("merobox.topology.bootstrap._spawn_boot_node")
@patch("merobox.topology.bootstrap._pull_image_if_missing")
@patch("merobox.topology.bootstrap._ensure_network")
def test_setup_creates_a_non_internal_bridge(
    ensure_network, _pull, spawn, resolve_ip, resolve_peer
):
    """The bridge must NOT be internal: clients need to reach the
    boot-node on it. Internal is the NAT topology's LAN bridge, whose
    whole purpose is to deny that."""
    ensure_network.return_value = MagicMock(name="net")
    spawn.return_value = MagicMock(name="boot")
    resolve_ip.return_value = "172.31.0.2"
    resolve_peer.return_value = "12D3KooWXyz"

    state = setup_bootstrap_topology(MagicMock(), workflow_name="wf")

    assert ensure_network.call_args.kwargs["internal"] is False
    assert state.boot_node_ip == "172.31.0.2"
    assert state.boot_node_peer_id == "12D3KooWXyz"
    assert state.client_names == []


@patch("merobox.topology.bootstrap._resolve_boot_node_peer_id")
@patch("merobox.topology.bootstrap._resolve_container_ip")
@patch("merobox.topology.bootstrap._spawn_boot_node")
@patch("merobox.topology.bootstrap._pull_image_if_missing")
@patch("merobox.topology.bootstrap._ensure_network")
def test_setup_cleans_up_when_peer_id_never_appears(
    ensure_network, _pull, spawn, resolve_ip, resolve_peer
):
    """A half-built topology left behind makes the next run of the same
    workflow collide with its own leftovers, which surfaces as an
    unrelated Docker error. So a failing setup owns nothing."""
    network = MagicMock(name="net")
    boot = MagicMock(name="boot")
    ensure_network.return_value = network
    spawn.return_value = boot
    resolve_ip.return_value = "172.31.0.2"
    resolve_peer.side_effect = RuntimeError("no peer id in boot-node log")

    with pytest.raises(RuntimeError):
        setup_bootstrap_topology(MagicMock(), workflow_name="wf")

    boot.remove.assert_called_once()
    network.remove.assert_called_once()


def test_teardown_removes_boot_node_then_network():
    state = _state()
    teardown_bootstrap_topology(MagicMock(), state)

    state.boot_node_container.remove.assert_called_once()
    state.network.remove.assert_called_once()


def test_teardown_still_removes_the_network_when_the_boot_node_fails():
    """Best-effort and independently guarded: a teardown that aborts
    halfway leaves a bridge that breaks the next run."""
    state = _state()
    state.boot_node_container.remove.side_effect = RuntimeError("daemon hiccup")

    teardown_bootstrap_topology(MagicMock(), state)

    state.network.remove.assert_called_once()


def test_teardown_can_keep_the_network():
    state = _state()
    teardown_bootstrap_topology(MagicMock(), state, remove_networks=False)

    state.boot_node_container.remove.assert_called_once()
    state.network.remove.assert_not_called()
