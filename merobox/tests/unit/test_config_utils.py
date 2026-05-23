from pathlib import Path
from unittest.mock import mock_open, patch

from merobox.commands.config_utils import (
    apply_bootstrap_nodes,
    apply_e2e_defaults,
    build_sibling_bootstrap_addrs,
    read_bootstrap_nodes,
    read_peer_id,
    set_nested_config,
)


def test_set_nested_config():
    """Test set_nested_config sets nested values correctly."""
    config = {}
    set_nested_config(config, "bootstrap.nodes", ["addr1", "addr2"], log=False)
    assert config["bootstrap"]["nodes"] == ["addr1", "addr2"]

    # Test deeply nested
    set_nested_config(config, "context.config.near.network", "local", log=False)
    assert config["context"]["config"]["near"]["network"] == "local"


def test_set_nested_config_existing_keys():
    """Test set_nested_config preserves existing keys."""
    config = {"bootstrap": {"timeout": 30}}
    set_nested_config(config, "bootstrap.nodes", ["addr1"], log=False)

    assert config["bootstrap"]["timeout"] == 30
    assert config["bootstrap"]["nodes"] == ["addr1"]


def test_apply_bootstrap_nodes():
    """Test apply_bootstrap_nodes applies config correctly."""
    initial_config = {"discovery": {"mdns": True}}
    bootstrap_nodes = ["/ip4/127.0.0.1/tcp/2428", "/ip4/127.0.0.1/tcp/2429"]

    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = initial_config.copy()

        with patch("builtins.open", mock_open()):
            with (
                patch("pathlib.Path.exists", return_value=True),
                patch("pathlib.Path.chmod"),
                patch("pathlib.Path.stat"),
            ):
                result = apply_bootstrap_nodes(
                    Path("/tmp/config.toml"), "node1", bootstrap_nodes
                )

                assert result is True

                # Check what was passed to dump
                args, _ = mock_toml.dump.call_args
                config_dict = args[0]

                assert config_dict["bootstrap"]["nodes"] == bootstrap_nodes


def test_apply_bootstrap_nodes_file_not_found():
    """Test apply_bootstrap_nodes handles missing file."""
    with patch("pathlib.Path.exists", return_value=False):
        result = apply_bootstrap_nodes(Path("/missing/config.toml"), "node1", [])
        assert result is False


def test_apply_e2e_defaults():
    """Test apply_e2e_defaults applies correct config."""
    initial_config = {}

    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = initial_config.copy()

        with patch("builtins.open", mock_open()):
            with (
                patch("pathlib.Path.exists", return_value=True),
                patch("pathlib.Path.chmod"),
                patch("pathlib.Path.stat"),
            ):
                result = apply_e2e_defaults(
                    Path("/tmp/config.toml"),
                    "node1",
                    workflow_id="test-123",
                )

                assert result is True

                # Check what was passed to dump
                args, _ = mock_toml.dump.call_args
                config_dict = args[0]

                # Verify e2e defaults are applied (bootstrap, discovery, sync only)
                assert config_dict["bootstrap"]["nodes"] == []
                assert (
                    config_dict["discovery"]["rendezvous"]["namespace"]
                    == "calimero/merobox-tests/test-123"
                )
                assert config_dict["discovery"]["mdns"] is True
                assert config_dict["sync"]["timeout_ms"] == 30000
                assert config_dict["sync"]["interval_ms"] == 500
                assert config_dict["sync"]["frequency_ms"] == 1000


def test_apply_e2e_defaults_generates_workflow_id():
    """Test apply_e2e_defaults generates workflow_id when not provided."""
    initial_config = {}

    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = initial_config.copy()

        with patch("builtins.open", mock_open()):
            with (
                patch("pathlib.Path.exists", return_value=True),
                patch("pathlib.Path.chmod"),
                patch("pathlib.Path.stat"),
            ):
                result = apply_e2e_defaults(
                    Path("/tmp/config.toml"),
                    "node1",
                    workflow_id=None,  # Should generate one
                )

                assert result is True

                # Check what was passed to dump
                args, _ = mock_toml.dump.call_args
                config_dict = args[0]

                # Verify a workflow ID was generated in the namespace
                namespace = config_dict["discovery"]["rendezvous"]["namespace"]
                assert namespace.startswith("calimero/merobox-tests/")
                assert len(namespace.split("/")[-1]) == 8  # UUID[:8]


def test_apply_e2e_defaults_file_not_found():
    """Test apply_e2e_defaults handles missing file."""
    with patch("pathlib.Path.exists", return_value=False):
        result = apply_e2e_defaults(Path("/missing/config.toml"), "node1")
        assert result is False


def test_apply_e2e_defaults_clears_existing_bootstrap_by_default():
    """The default (preserve_default_bootstrap=False) clears a pre-existing
    boot-node list — confirming the e2e-isolation guarantee. The
    `test_apply_e2e_defaults` case above starts from an empty config and
    therefore doesn't exercise the *clearing* behaviour against a
    populated list; this test does."""
    # Synthetic multiaddr — localhost + `12D3KooW` + 44 X's. The peer-id
    # body is structurally a base58btc-shaped placeholder, not a real
    # peer; the IP is loopback so there's no coupling to any external
    # service. The test only cares that `apply_e2e_defaults` sees a
    # non-empty `bootstrap.nodes` and handles it correctly.
    devnet_boot = "/ip4/127.0.0.1/tcp/4001/p2p/12D3KooW" + "X" * 44

    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = {"bootstrap": {"nodes": [devnet_boot]}}

        with patch("builtins.open", mock_open()):
            with (
                patch("pathlib.Path.exists", return_value=True),
                patch("pathlib.Path.chmod"),
                patch("pathlib.Path.stat"),
            ):
                result = apply_e2e_defaults(
                    Path("/tmp/config.toml"),
                    "node1",
                    workflow_id="test-clear-boot",
                    # preserve_default_bootstrap omitted = default False
                )

                assert result is True

                args, _ = mock_toml.dump.call_args
                config_dict = args[0]

                # Pre-existing boot-node was cleared (the isolation default).
                assert config_dict["bootstrap"]["nodes"] == []
                # Clearing the boot-node must NOT skip the discovery/sync
                # defaults — guards against a refactor regression where
                # the conditional bootstrap.nodes block accidentally
                # short-circuits the rest of the e2e_config dict.
                assert (
                    config_dict["discovery"]["rendezvous"]["namespace"]
                    == "calimero/merobox-tests/test-clear-boot"
                )
                assert config_dict["discovery"]["mdns"] is True
                assert config_dict["sync"]["timeout_ms"] == 30000
                assert config_dict["sync"]["interval_ms"] == 500
                assert config_dict["sync"]["frequency_ms"] == 1000


def test_apply_e2e_defaults_preserve_default_bootstrap():
    """preserve_default_bootstrap=True keeps whatever bootstrap.nodes was
    written by `merod init` (the public devnet boot-node), and still
    applies the rest of the e2e defaults (discovery + sync)."""
    # Simulate what `merod init` writes by default: a single public
    # devnet boot-node in the bootstrap list.
    # Synthetic multiaddr — localhost + `12D3KooW` + 44 X's. The peer-id
    # body is structurally a base58btc-shaped placeholder, not a real
    # peer; the IP is loopback so there's no coupling to any external
    # service. The test only cares that `apply_e2e_defaults` sees a
    # non-empty `bootstrap.nodes` and handles it correctly.
    devnet_boot = "/ip4/127.0.0.1/tcp/4001/p2p/12D3KooW" + "X" * 44
    initial_config = {"bootstrap": {"nodes": [devnet_boot]}}

    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = {
            "bootstrap": {"nodes": list(initial_config["bootstrap"]["nodes"])}
        }

        with patch("builtins.open", mock_open()):
            with (
                patch("pathlib.Path.exists", return_value=True),
                patch("pathlib.Path.chmod"),
                patch("pathlib.Path.stat"),
            ):
                result = apply_e2e_defaults(
                    Path("/tmp/config.toml"),
                    "node1",
                    workflow_id="test-keep-boot",
                    preserve_default_bootstrap=True,
                )

                assert result is True

                args, _ = mock_toml.dump.call_args
                config_dict = args[0]

                # The boot-node from `merod init` survives — this is the
                # whole point of the opt-out.
                assert config_dict["bootstrap"]["nodes"] == [devnet_boot]
                # Discovery + sync defaults still applied.
                assert (
                    config_dict["discovery"]["rendezvous"]["namespace"]
                    == "calimero/merobox-tests/test-keep-boot"
                )
                assert config_dict["discovery"]["mdns"] is True
                assert config_dict["sync"]["timeout_ms"] == 30000


# ---------------------------------------------------------------------------
# read_peer_id
# ---------------------------------------------------------------------------


def test_read_peer_id_extracts_identity_peer_id():
    """read_peer_id returns identity.peer_id written by `merod init`."""
    config = {"identity": {"peer_id": "12D3KooWTestPeerId", "keypair": "secret"}}

    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = config
        with patch("builtins.open", mock_open()):
            with patch("pathlib.Path.exists", return_value=True):
                assert read_peer_id(Path("/tmp/config.toml")) == "12D3KooWTestPeerId"


def test_read_peer_id_missing_identity_returns_none():
    """read_peer_id returns None when identity.peer_id is absent."""
    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = {"swarm": {}}
        with patch("builtins.open", mock_open()):
            with patch("pathlib.Path.exists", return_value=True):
                assert read_peer_id(Path("/tmp/config.toml")) is None


def test_read_peer_id_file_not_found_returns_none():
    """read_peer_id returns None when the config file does not exist."""
    with patch("pathlib.Path.exists", return_value=False):
        assert read_peer_id(Path("/missing/config.toml")) is None


# ---------------------------------------------------------------------------
# read_bootstrap_nodes
# ---------------------------------------------------------------------------


def test_read_bootstrap_nodes_returns_existing_list():
    """read_bootstrap_nodes echoes the bootstrap.nodes list verbatim — the
    cluster-wiring code relies on this to recover the preserved merod-init
    boot-node before it appends sibling addrs."""
    devnet = "/ip4/127.0.0.1/tcp/4001/p2p/12D3KooW" + "Y" * 44
    config = {"bootstrap": {"nodes": [devnet]}, "discovery": {"mdns": True}}

    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = config
        with patch("builtins.open", mock_open()):
            with patch("pathlib.Path.exists", return_value=True):
                assert read_bootstrap_nodes(Path("/tmp/config.toml")) == [devnet]


def test_read_bootstrap_nodes_missing_key_returns_empty():
    """When `apply_e2e_defaults` has cleared the list (the default
    isolation behaviour) the wiring code must treat the absence the same
    as an explicit empty list and not raise."""
    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = {"discovery": {"mdns": True}}
        with patch("builtins.open", mock_open()):
            with patch("pathlib.Path.exists", return_value=True):
                assert read_bootstrap_nodes(Path("/tmp/config.toml")) == []


def test_read_bootstrap_nodes_file_not_found_returns_empty():
    """No config file (e.g. the node hasn't been initialised yet) must
    return [] so callers can fall through to their own defaults."""
    with patch("pathlib.Path.exists", return_value=False):
        assert read_bootstrap_nodes(Path("/missing/config.toml")) == []


def test_read_bootstrap_nodes_non_list_value_returns_empty():
    """Defensive: if the on-disk config has been corrupted and the value
    is a scalar (or anything non-list), don't propagate the bad shape to
    the wiring code — return [] and let it fall back."""
    with patch("merobox.commands.config_utils.toml") as mock_toml:
        mock_toml.load.return_value = {"bootstrap": {"nodes": "not-a-list"}}
        with patch("builtins.open", mock_open()):
            with patch("pathlib.Path.exists", return_value=True):
                assert read_bootstrap_nodes(Path("/tmp/config.toml")) == []


# ---------------------------------------------------------------------------
# build_sibling_bootstrap_addrs
# ---------------------------------------------------------------------------


# Realistic-length base58btc libp2p peer IDs for the bootstrap-address tests.
PID_1 = "12D3KooW" + "A" * 44
PID_2 = "12D3KooW" + "B" * 44
PID_3 = "12D3KooW" + "C" * 44
PID_DEVNET = "12D3KooW" + "D" * 44


def test_build_sibling_bootstrap_addrs_excludes_self_and_lists_siblings():
    """Every sibling appears as an /ip4 TCP + QUIC multiaddr; the node itself does not."""
    peers = {
        "calimero-node-1": ("172.20.0.2", PID_1),
        "calimero-node-2": ("172.20.0.3", PID_2),
        "calimero-node-3": ("172.20.0.4", PID_3),
    }

    addrs = build_sibling_bootstrap_addrs("calimero-node-2", peers, p2p_port=2428)

    # node-2 never bootstraps to itself
    assert all("172.20.0.3" not in a for a in addrs)
    # every other node appears as both transports, by IP (not /dns4)
    assert all("/dns4/" not in a for a in addrs)
    for sib in ("calimero-node-1", "calimero-node-3"):
        ip, pid = peers[sib]
        assert f"/ip4/{ip}/tcp/2428/p2p/{pid}" in addrs
        assert f"/ip4/{ip}/udp/2428/quic-v1/p2p/{pid}" in addrs
    assert len(addrs) == 4


def test_build_sibling_bootstrap_addrs_skips_unresolved_endpoints():
    """Siblings with a missing endpoint (None) are skipped."""
    peers = {
        "calimero-node-1": ("172.20.0.2", PID_1),
        "calimero-node-2": None,
        "calimero-node-3": ("172.20.0.4", PID_3),
    }

    addrs = build_sibling_bootstrap_addrs("calimero-node-1", peers, p2p_port=2428)

    assert all("calimero-node-2" not in a for a in addrs)
    assert f"/ip4/172.20.0.4/tcp/2428/p2p/{PID_3}" in addrs
    assert len(addrs) == 2


def test_build_sibling_bootstrap_addrs_skips_malformed_ip_or_peer_id():
    """Siblings with a malformed IP or non-base58 peer ID are skipped."""
    peers = {
        "calimero-node-1": ("172.20.0.2", PID_1),
        "calimero-node-2": ("not-an-ip", PID_2),
        "calimero-node-3": ("172.20.0.4", "not a valid peer id"),
        "calimero-node-4": ("172.20.0.5", PID_3),
    }

    addrs = build_sibling_bootstrap_addrs("calimero-node-1", peers, p2p_port=2428)

    assert all("172.20.0.4" not in a and "not-an-ip" not in a for a in addrs)
    assert f"/ip4/172.20.0.5/tcp/2428/p2p/{PID_3}" in addrs
    assert len(addrs) == 2


def test_build_sibling_bootstrap_addrs_appends_to_existing():
    """An existing bootstrap list is preserved; siblings are appended, deduped."""
    peers = {"node-a": ("172.20.0.2", PID_1), "node-b": ("172.20.0.3", PID_2)}
    existing = [f"/ip4/63.181.86.34/tcp/4001/p2p/{PID_DEVNET}"]

    addrs = build_sibling_bootstrap_addrs(
        "node-a", peers, p2p_port=2428, existing=existing
    )

    assert addrs[0] == f"/ip4/63.181.86.34/tcp/4001/p2p/{PID_DEVNET}"
    assert f"/ip4/172.20.0.3/tcp/2428/p2p/{PID_2}" in addrs
    # no duplicates
    assert len(addrs) == len(set(addrs))
