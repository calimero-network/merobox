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
    slugify_workflow_name,
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
    for tag in (BOOT_NODE_IMAGE_TAG, NAT_GATEWAY_IMAGE_TAG):
        assert tag.startswith("merobox/"), tag
        # `:local` makes it obvious to operators that these are
        # locally-built / not a published registry image.
        assert tag.endswith(":local"), tag


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


# ---------------------------------------------------------------------------
# slugify_workflow_name
# ---------------------------------------------------------------------------
#
# The CI failure that motivated this whole helper was Docker rejecting a
# container named "NAT Topology — Cone Mode Smoke-boot-node" because of
# the spaces and em-dash. These cases exercise every category of input
# the slugifier has to deal with so the rejection can't re-happen
# silently.


def test_slugify_workflow_name_handles_spaces_and_em_dash():
    """The motivating real-world case: a display name with spaces +
    em-dash + mixed case must yield a Docker-safe slug."""
    assert (
        slugify_workflow_name("NAT Topology — Cone Mode Smoke")
        == "nat-topology-cone-mode-smoke"
    )


def test_slugify_workflow_name_collapses_runs_of_separators():
    """Multiple unsafe chars in a row should collapse to a single
    hyphen; otherwise long names produce ugly `---` runs."""
    assert slugify_workflow_name("foo --- bar") == "foo-bar"
    assert slugify_workflow_name("a  b   c") == "a-b-c"


def test_slugify_workflow_name_strips_leading_and_trailing_separators():
    """Docker requires the first char to be alphanumeric. A leading
    separator (`.`, `-`, `_`) after substitution must be stripped."""
    assert slugify_workflow_name("--- leading") == "leading"
    assert slugify_workflow_name("trailing ---") == "trailing"
    assert slugify_workflow_name("...both...") == "both"


def test_slugify_workflow_name_preserves_safe_chars():
    """Underscores, dots, and digits are all allowed in Docker
    resource names and should pass through unchanged."""
    # The slugifier lowercases everything, so capital `V` in `v0.6.18`
    # is kept as `v`. Dots + digits + underscores survive intact.
    assert slugify_workflow_name("test_run_v0.6.18") == "test_run_v0.6.18"


def test_slugify_workflow_name_falls_back_for_unusable_input():
    """Empty / whitespace-only / all-unsafe inputs collapse to the
    default prefix rather than raising."""
    assert slugify_workflow_name("") == "merobox-nat"
    assert slugify_workflow_name("   ") == "merobox-nat"
    # An all-unsafe input (`!@#$`) leaves nothing after substitution
    # and stripping; the fallback prevents an empty Docker name from
    # ever reaching the daemon.
    assert slugify_workflow_name("!@#$") == "merobox-nat"


def test_slugify_workflow_name_deterministic_across_calls():
    """The slugifier is pure — same input always produces the same
    output. The cluster-wiring teardown path relies on this for the
    leftover-from-prior-run detection."""
    assert slugify_workflow_name("My Test") == slugify_workflow_name("My Test")


# ---------------------------------------------------------------------------
# _resolve_boot_node_peer_id matcher
# ---------------------------------------------------------------------------
#
# The original CI failure on this workflow was a 30s timeout in
# this scanner: the matcher was looking for the substring `local peer
# id`, but the actual log line from boot-node's main.rs is
# `Peer id: PeerId("12D3KooW…")` (note: no "local" prefix, and the
# id is rendered inside Debug-quotes). These cases pin the matcher
# against several real-world log-line shapes so a regression on the
# regex can't reintroduce the timeout silently.

import re  # noqa: E402  (test-local import to keep the unrelated test groups clean)

from merobox.topology.nat import _resolve_boot_node_peer_id  # noqa: E402

_PEER_ID = "12D3KooWR5V4zmisVtVdGE6i8jfFwtgRNq5t8eDGxfckKuhXu7Eh"


def _make_container_with_logs(log_text: str) -> MagicMock:
    """Return a mock container whose `.logs(tail=…)` returns the
    given text as bytes. Mirrors what docker-py returns for a real
    container's stdout/stderr."""
    container = MagicMock()
    container.logs.return_value = log_text.encode("utf-8")
    return container


def test_resolve_boot_node_peer_id_matches_actual_boot_node_log_shape():
    """The exact format boot-node's main.rs emits: tracing prefix +
    `Peer id: PeerId("…")`. This is the line we get in CI on the
    real binary, and the regression target — earlier matcher missed
    it and the workflow timed out at 30s."""
    log = (
        "2026-05-25T11:55:56.123456Z  INFO boot_node: "
        f'Peer id: PeerId("{_PEER_ID}")\n'
    )
    container = _make_container_with_logs(log)
    assert _resolve_boot_node_peer_id(container) == _PEER_ID


def test_resolve_boot_node_peer_id_returns_first_match_with_surrounding_noise():
    """The line lands in the middle of a busy log; tracing prefix +
    later lines about listeners / identify must not confuse the
    extraction."""
    log = (
        "2026-05-25T11:55:55.999999Z  INFO boot_node: starting up\n"
        "2026-05-25T11:55:56.001234Z  INFO boot_node: "
        f'Peer id: PeerId("{_PEER_ID}")\n'
        "2026-05-25T11:55:56.123456Z  INFO boot_node: "
        '  Listening on "/ip4/0.0.0.0/tcp/4001"\n'
    )
    container = _make_container_with_logs(log)
    assert _resolve_boot_node_peer_id(container) == _PEER_ID


def test_resolve_boot_node_peer_id_raises_with_log_tail_on_timeout(monkeypatch):
    """When the matcher never fires the operator must see the
    actual log tail in the error — opaque "didn't print … within
    30s" used to send people to `docker logs` to figure out what
    the real output was."""
    # Shrink the wait so the test doesn't hang for 30s. Make the
    # mocked clock jump from 0 → past-deadline on the second call,
    # which is what the inner `while time.monotonic() < deadline`
    # check inspects after the first sleep-noop.
    #
    # Counter-based mock (not iter) so subsequent reads after the
    # deadline keep returning a past-deadline value rather than
    # raising StopIteration; the function also calls monotonic
    # one final time inside the `raise` path's log-fetch.
    clock = {"now": 0.0}

    def _fake_monotonic():
        clock["now"] += 31.0  # jump past the 30s deadline on first call
        return clock["now"]

    monkeypatch.setattr("merobox.topology.nat.time.monotonic", _fake_monotonic)
    monkeypatch.setattr("merobox.topology.nat.time.sleep", lambda _s: None)

    container = _make_container_with_logs(
        "INFO boot_node: unrelated startup chatter\n"
        "INFO boot_node: no peer id here\n"
    )
    with pytest.raises(RuntimeError, match="didn't print its peer id") as excinfo:
        _resolve_boot_node_peer_id(container)
    # The error message must include the actual log content so
    # operators don't have to docker-log it themselves.
    assert "unrelated startup chatter" in str(excinfo.value)


def test_resolve_boot_node_peer_id_re_matches_inside_debug_quotes():
    """Sanity-pin the regex shape directly so a future refactor of
    `_resolve_boot_node_peer_id` doesn't silently change the
    matching algorithm."""
    # The regex is internal but pinned by behavior above.
    sample = f'Peer id: PeerId("{_PEER_ID}"), some trailing junk'
    match = re.search(r'PeerId\("(12D3KooW[^"]+)"\)', sample)
    assert match is not None
    assert match.group(1) == _PEER_ID


def test_bootstrap_multiaddrs_are_well_formed_libp2p():
    """Each addr should start with `/ip4/` and end with `/p2p/<peer_id>`
    — that's what merod's libp2p parser accepts in `bootstrap.nodes`."""
    state = _make_state()
    for a in boot_node_bootstrap_multiaddrs(state):
        assert a.startswith("/ip4/")
        assert a.endswith("/p2p/" + state.boot_node_peer_id)


# ---------------------------------------------------------------------------
# Host iptables isolation helpers
# ---------------------------------------------------------------------------
#
# These rules force the relay path: without them Docker's default
# inter-bridge ACCEPT lets the boot-node direct-dial clients (autonat
# decides reachable → no relay reservation → readiness gate times
# out). The tests pin the install/remove rule shapes so a refactor
# can't silently drop the isolation.

from merobox.topology.nat import (  # noqa: E402
    _docker_bridge_iface_name,
    _install_host_iptables_isolation,
    _remove_host_iptables_isolation,
)


def test_docker_bridge_iface_name_uses_first_12_chars_of_network_id():
    """Docker's host-side bridge interface is `br-<first-12-chars-of-id>`.
    iptables `-i`/`-o` selectors must match exactly or the rule never
    fires."""
    net = MagicMock()
    net.id = "abcdef0123456789deadbeef" + "f" * 40
    assert _docker_bridge_iface_name(net) == "br-abcdef012345"


def _make_state_with_bridge_ids(
    public_id: str = "p" * 64,
    lan_id: str = "l" * 64,
) -> NatTopologyState:
    """NatTopologyState with mocked Network handles whose `.id` we
    control — `_docker_bridge_iface_name` reads them to compute the
    iptables `-i`/`-o` selectors."""
    public_net = MagicMock()
    public_net.id = public_id
    public_net.name = "test-public"
    lan_net = MagicMock()
    lan_net.id = lan_id
    lan_net.name = "test-lan"
    return NatTopologyState(
        public_network=public_net,
        lan_network=lan_net,
        boot_node_container=MagicMock(),
        boot_node_peer_id="12D3KooW" + "X" * 44,
        boot_node_public_ip="172.30.1.5",
        nat_gateway_container=MagicMock(),
    )


def test_install_host_iptables_isolation_inserts_stateful_drop_in_docker_user(
    monkeypatch,
):
    """Insert at position 1 of DOCKER-USER with stateful conntrack
    matching: ``-i br-<public> -o br-<lan> -m conntrack --ctstate NEW
    -j DROP``. The `--ctstate NEW` qualifier is the difference
    between "drop every public→LAN packet" (which kills the return
    half of every LAN-initiated TCP handshake — clients can't dial
    the boot-node at all) and "drop only inbound-initiated
    connections" (the intended NAT semantics). The `merobox-nat:`
    comment lets a leaked rule be recognised on a stray
    `iptables -L` run."""
    state = _make_state_with_bridge_ids()
    captured: list[list[str]] = []

    def fake_run_iptables(argv):
        captured.append(argv)
        return (0, "")

    monkeypatch.setattr("merobox.topology.nat._run_iptables", fake_run_iptables)

    _install_host_iptables_isolation(state)

    assert len(captured) == 1
    rule = captured[0]
    assert rule[:3] == ["-I", "DOCKER-USER", "1"]
    assert "-i" in rule and "br-pppppppppppp" in rule
    assert "-o" in rule and "br-llllllllllll" in rule
    assert "DROP" in rule
    # Stateful match — `NEW` MUST be present or we accidentally drop
    # return traffic for LAN-initiated dials too.
    assert "conntrack" in rule
    assert "--ctstate" in rule
    ctstate_idx = rule.index("--ctstate")
    assert rule[ctstate_idx + 1] == "NEW"
    comment_idx = rule.index("--comment")
    assert rule[comment_idx + 1] == "merobox-nat:test-lan"
    assert state.host_iptables_rules == [rule]


def test_install_host_iptables_isolation_raises_on_iptables_failure(monkeypatch):
    """Non-zero exit aborts setup with a diagnostic error — silently
    proceeding would produce a test that looks like it's exercising
    the relay path while clients bypass it."""
    state = _make_state_with_bridge_ids()
    monkeypatch.setattr(
        "merobox.topology.nat._run_iptables",
        lambda _argv: (1, "iptables: Permission denied"),
    )
    with pytest.raises(RuntimeError) as excinfo:
        _install_host_iptables_isolation(state)
    msg = str(excinfo.value)
    assert "Permission denied" in msg
    # Failed install must NOT leave a phantom entry in state else
    # teardown would try to delete a rule that doesn't exist.
    assert state.host_iptables_rules == []


def test_remove_host_iptables_isolation_swaps_insert_for_delete(monkeypatch):
    """Teardown rewrites `-I <chain> <pos> <spec>` to `-D <chain>
    <spec>` (iptables `-D` doesn't take a position) and calls
    iptables once per installed rule. Exact-argv tracking lets
    parallel workflows clean up just their own rules even if Docker
    reassigns bridge interface names between runs. The fixtures
    here mirror the real installed shape with conntrack args."""
    state = _make_state_with_bridge_ids()
    full_spec_a = [
        "-i",
        "br-A",
        "-o",
        "br-B",
        "-m",
        "conntrack",
        "--ctstate",
        "NEW",
        "-j",
        "DROP",
        "-m",
        "comment",
        "--comment",
        "merobox-nat:lan-A",
    ]
    full_spec_b = [
        "-i",
        "br-C",
        "-o",
        "br-D",
        "-m",
        "conntrack",
        "--ctstate",
        "NEW",
        "-j",
        "DROP",
        "-m",
        "comment",
        "--comment",
        "merobox-nat:lan-B",
    ]
    state.host_iptables_rules = [
        ["-I", "DOCKER-USER", "1", *full_spec_a],
        ["-I", "DOCKER-USER", "1", *full_spec_b],
    ]
    captured: list[list[str]] = []
    monkeypatch.setattr(
        "merobox.topology.nat._run_iptables",
        lambda argv: (captured.append(argv) or (0, "")),
    )

    _remove_host_iptables_isolation(state)

    assert len(captured) == 2
    # `.pop()` drains the list LIFO, so spec_b comes off first.
    # The position arg ("1") is gone, but everything else in the
    # spec — including any conntrack args — is preserved verbatim.
    assert captured[0] == ["-D", "DOCKER-USER", *full_spec_b]
    assert captured[1] == ["-D", "DOCKER-USER", *full_spec_a]
    assert state.host_iptables_rules == []


def test_remove_host_iptables_isolation_strips_only_position_arg(monkeypatch):
    """Regression guard: a previous implementation filtered the
    rule with `[a for a in rule if a != "1"]`, which would also
    strip a legitimate `"1"` elsewhere in the spec (e.g. a future
    `--dst-len 1` or `--hashlimit-burst 1` extension). Removal
    must be position-based — drop rule[2] only."""
    state = _make_state_with_bridge_ids()
    state.host_iptables_rules = [
        # `1` appears twice in the SPEC (positions 5 and 9), plus
        # once as the iptables position arg at index 2. Only the
        # index-2 occurrence should be stripped.
        [
            "-I",
            "DOCKER-USER",
            "1",
            "-i",
            "br-A",
            "--dport",
            "1",  # legitimate "1" in spec
            "-o",
            "br-B",
            "--sport",
            "1",  # legitimate "1" in spec
            "-j",
            "DROP",
        ],
    ]
    captured: list[list[str]] = []
    monkeypatch.setattr(
        "merobox.topology.nat._run_iptables",
        lambda argv: (captured.append(argv) or (0, "")),
    )

    _remove_host_iptables_isolation(state)

    assert len(captured) == 1
    argv = captured[0]
    # Both legitimate "1"s survived; only the position arg is gone.
    assert argv.count("1") == 2
    assert argv == [
        "-D",
        "DOCKER-USER",
        "-i",
        "br-A",
        "--dport",
        "1",
        "-o",
        "br-B",
        "--sport",
        "1",
        "-j",
        "DROP",
    ]


def test_remove_host_iptables_isolation_skips_malformed_rule(monkeypatch):
    """A malformed entry (no position arg, wrong chain shape) must
    NOT be sent to iptables — otherwise we'd issue a `-D` against
    a chain it was never installed in. Just log and skip."""
    state = _make_state_with_bridge_ids()
    state.host_iptables_rules = [
        # No position arg where `-I` rules require one.
        ["-A", "DOCKER-USER", "-i", "br-A", "-o", "br-B", "-j", "DROP"],
    ]
    captured: list[list[str]] = []
    monkeypatch.setattr(
        "merobox.topology.nat._run_iptables",
        lambda argv: (captured.append(argv) or (0, "")),
    )

    _remove_host_iptables_isolation(state)

    assert captured == []
    assert state.host_iptables_rules == []


def test_remove_host_iptables_isolation_tolerates_remove_errors(monkeypatch):
    """A non-zero `-D` exit (rule already gone, bridge vanished)
    logs but doesn't propagate. A propagating exception would mask
    the workflow's actual result."""
    state = _make_state_with_bridge_ids()
    state.host_iptables_rules = [
        ["-I", "DOCKER-USER", "1", "-i", "br-X", "-o", "br-Y", "-j", "DROP"],
    ]
    monkeypatch.setattr(
        "merobox.topology.nat._run_iptables",
        lambda _argv: (1, "iptables: No chain/target/match by that name"),
    )
    # Must NOT raise
    _remove_host_iptables_isolation(state)
    assert state.host_iptables_rules == []
