"""Unit tests for the NAT-topology primitive.

Schema validation is the bulk of what we can cover at the unit
level — actually standing up the four-container topology requires a
real Docker daemon, which lives in the integration tests (separate
suite, runs only on CI). These tests assert the YAML-side contract
and the pure-function helpers that don't need Docker.
"""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from merobox.commands.bootstrap.config import (
    NatTopologyConfig,
    WorkflowConfig,
)
from merobox.topology.nat import (
    BOOT_NODE_IMAGE_TAG,
    NAT_GATEWAY_IMAGE_TAG,
    NatTopologyState,
    boot_node_bootstrap_multiaddrs,
)

# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_workflow_without_topology_block_keeps_topology_none():
    """Default cluster mode: `topology:` omitted, value is None."""
    cfg = WorkflowConfig.model_validate({"name": "plain", "nodes": {"count": 2}})
    assert cfg.topology is None


def test_nat_topology_default_mode_is_cone():
    """Most workflows want the cheap NAT shape; `cone` is the default."""
    cfg = WorkflowConfig.model_validate(
        {
            "name": "nat-default",
            "topology": {"type": "nat"},
            "nodes": {"count": 2},
        }
    )
    assert isinstance(cfg.topology, NatTopologyConfig)
    assert cfg.topology.nat_mode == "cone"
    assert cfg.topology.boot_node.image is None  # auto-build
    assert cfg.topology.boot_node.keypair is None  # ephemeral


def test_nat_topology_symmetric_mode_accepted():
    """`symmetric` is the strict alternative; explicit opt-in."""
    cfg = WorkflowConfig.model_validate(
        {
            "name": "nat-strict",
            "topology": {"type": "nat", "nat_mode": "symmetric"},
            "nodes": {"count": 2},
        }
    )
    assert cfg.topology.nat_mode == "symmetric"


def test_nat_topology_explicit_boot_node_image_threaded_through():
    """Operators can pin a pre-built boot-node image; merobox shouldn't
    rebuild over them."""
    cfg = WorkflowConfig.model_validate(
        {
            "name": "nat-pinned",
            "topology": {
                "type": "nat",
                "boot_node": {"image": "ghcr.io/example/boot-node:0.8.0"},
            },
            "nodes": {"count": 2},
        }
    )
    assert cfg.topology.boot_node.image == "ghcr.io/example/boot-node:0.8.0"


def test_nat_topology_rejects_unknown_type():
    """Future topology types must land via a new Literal variant, not
    accidentally accepted as `nat`."""
    with pytest.raises(ValidationError):
        WorkflowConfig.model_validate(
            {
                "name": "bad",
                "topology": {"type": "starnet"},
                "nodes": {"count": 2},
            }
        )


def test_nat_topology_rejects_unknown_nat_mode():
    """Bad `nat_mode` should fail validation up-front rather than
    surface as a confusing iptables error at container startup."""
    with pytest.raises(ValidationError):
        WorkflowConfig.model_validate(
            {
                "name": "bad",
                "topology": {"type": "nat", "nat_mode": "double-cone"},
                "nodes": {"count": 2},
            }
        )


def test_nat_topology_serialises_boot_node_keypair_path():
    """Keypair path threads through as a plain string; merobox treats
    the path as opaque (caller mounts the file)."""
    cfg = WorkflowConfig.model_validate(
        {
            "name": "nat-keypair",
            "topology": {
                "type": "nat",
                "boot_node": {"keypair": "./fixtures/boot-key.json"},
            },
            "nodes": {"count": 2},
        }
    )
    assert cfg.topology.boot_node.keypair == "./fixtures/boot-key.json"


# ---------------------------------------------------------------------------
# Default image tags
# ---------------------------------------------------------------------------


def test_default_image_tags_are_local_namespaced():
    """Both bundled images live under the `merobox/` prefix so an
    accidental rebuild can't clobber a third-party image with the
    same short name."""
    assert BOOT_NODE_IMAGE_TAG.startswith("merobox/")
    assert NAT_GATEWAY_IMAGE_TAG.startswith("merobox/")
    # `:local` makes it obvious to operators that these are
    # locally-built / not a published registry image.
    assert BOOT_NODE_IMAGE_TAG.endswith(":local")
    assert NAT_GATEWAY_IMAGE_TAG.endswith(":local")


# ---------------------------------------------------------------------------
# boot_node_bootstrap_multiaddrs
# ---------------------------------------------------------------------------


def _make_state(
    ip: str = "172.20.0.2", peer_id: str = "12D3KooW" + "X" * 44
) -> NatTopologyState:
    """Construct a NatTopologyState with the bare minimum the
    bootstrap-multiaddr formatter actually reads. Docker handles are
    placeholders — none of these tests touch them."""
    return NatTopologyState(
        public_network=MagicMock(),
        lan_network=MagicMock(),
        boot_node_container=MagicMock(),
        boot_node_peer_id=peer_id,
        boot_node_public_ip=ip,
        nat_gateway_container=MagicMock(),
    )


def test_bootstrap_multiaddrs_includes_both_tcp_and_quic():
    """Clients dial both transports; the boot-node listens on both
    on the same port. Returning both lets libp2p pick whichever
    succeeds first."""
    state = _make_state()
    addrs = boot_node_bootstrap_multiaddrs(state)
    assert len(addrs) == 2
    assert any("/tcp/4001/" in a for a in addrs)
    assert any("/udp/4001/quic-v1/" in a for a in addrs)


def test_bootstrap_multiaddrs_uses_state_ip_and_peer_id():
    """The IP and peer id must match what `setup_nat_topology`
    resolved — otherwise clients would dial the wrong destination."""
    state = _make_state(ip="172.99.99.99", peer_id="12D3KooW" + "Z" * 44)
    addrs = boot_node_bootstrap_multiaddrs(state)
    for a in addrs:
        assert "172.99.99.99" in a
        assert "12D3KooW" + "Z" * 44 in a


def test_bootstrap_multiaddrs_are_well_formed_libp2p():
    """Each addr should start with `/ip4/` and end with `/p2p/<peer_id>`
    — that's what merod's libp2p parser accepts in `bootstrap.nodes`."""
    state = _make_state()
    for a in boot_node_bootstrap_multiaddrs(state):
        assert a.startswith("/ip4/")
        assert a.endswith("/p2p/" + state.boot_node_peer_id)
