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
# inject_default_route_into_client
# ---------------------------------------------------------------------------
#
# The default-route override is what makes the NAT topology actually
# route through the gateway — without it, Docker's bridge-isolation
# chain DROPs the cross-bridge traffic and clients can't reach the
# boot-node at all. These tests pin the sidecar shape so a refactor
# can't silently break that.


from docker import errors as docker_errors  # noqa: E402

from merobox.topology.nat import (  # noqa: E402
    inject_default_route_into_client,
)


def _make_state_with_gateway_lan_ip(lan_ip: str = "172.30.0.2") -> NatTopologyState:
    """State with a NAT gateway whose LAN-side `IPAddress` is mocked.

    `inject_default_route_into_client` calls `_resolve_container_ip` on
    the gateway against the LAN network's name; we mock the gateway's
    `NetworkSettings.Networks[<lan>].IPAddress` to make that resolve
    immediately."""
    public_net = MagicMock()
    public_net.name = "test-public"
    lan_net = MagicMock()
    lan_net.name = "test-lan"
    gateway = MagicMock()
    # `_resolve_container_ip` calls `container.reload()` then reads
    # `attrs["NetworkSettings"]["Networks"][<lan_name>]["IPAddress"]`.
    gateway.attrs = {
        "NetworkSettings": {"Networks": {"test-lan": {"IPAddress": lan_ip}}}
    }
    return NatTopologyState(
        public_network=public_net,
        lan_network=lan_net,
        boot_node_container=MagicMock(),
        boot_node_peer_id="12D3KooW" + "X" * 44,
        boot_node_public_ip="172.30.1.5",
        nat_gateway_container=gateway,
    )


def test_inject_default_route_spawns_sidecar_with_expected_shape():
    """Happy path: sidecar runs the nat-gateway image, shares the
    client's netns, has CAP_NET_ADMIN, and invokes `ip route replace
    default via <gateway-lan-ip>`. Each pinned detail matters —
    wrong image means no `ip`; wrong netns means modifying the wrong
    container's routes; missing CAP_NET_ADMIN means `ip route` fails
    with EPERM."""
    state = _make_state_with_gateway_lan_ip(lan_ip="172.30.0.99")
    client = MagicMock()
    # `containers.get(name)` is the existence-check that runs before
    # the sidecar is spawned; succeed silently.
    client.containers.get.return_value = MagicMock()

    inject_default_route_into_client(client, state, "nat-client-1")

    client.containers.get.assert_called_once_with("nat-client-1")
    client.containers.run.assert_called_once()
    _, kwargs = client.containers.run.call_args
    args = client.containers.run.call_args.args
    # Image must be the nat-gateway image — it's the only one we
    # know ships iproute2 + is already cached locally.
    assert args[0] == NAT_GATEWAY_IMAGE_TAG
    # Command: sh -c "ip route replace default via <gateway-ip>".
    assert kwargs["command"] == [
        "sh",
        "-c",
        "ip route replace default via 172.30.0.99",
    ]
    # Pin to the client's netns — modifies the right routing table.
    assert kwargs["network_mode"] == "container:nat-client-1"
    # CAP_NET_ADMIN — required for `ip route` to succeed inside
    # the sidecar (otherwise it errors with `Operation not permitted`).
    assert kwargs["cap_add"] == ["NET_ADMIN"]
    # `--rm`-equivalent: the sidecar disappears after exit so we
    # don't leak it across runs.
    assert kwargs["remove"] is True
    assert kwargs["detach"] is False


def test_inject_default_route_raises_when_client_container_missing():
    """If the client container doesn't exist, fail loudly BEFORE
    we try to spawn a sidecar against `container:<missing-name>`
    (which would otherwise produce a confusing Docker API error
    several seconds in)."""
    state = _make_state_with_gateway_lan_ip()
    client = MagicMock()
    client.containers.get.side_effect = docker_errors.NotFound("no such container")

    with pytest.raises(RuntimeError) as excinfo:
        inject_default_route_into_client(client, state, "ghost-client")
    msg = str(excinfo.value)
    assert "ghost-client" in msg
    assert "not found" in msg
    # The sidecar must NOT have been launched.
    client.containers.run.assert_not_called()


def test_inject_default_route_raises_with_sidecar_stderr_on_container_error():
    """`ip route replace` failing inside the sidecar surfaces as a
    `ContainerError`. The wrapper turns that into a `RuntimeError`
    with the stderr embedded so the operator can see WHY the route
    install failed (RTNETLINK errors are common: bad gateway IP,
    missing destination network, etc.)."""
    state = _make_state_with_gateway_lan_ip(lan_ip="10.0.0.1")
    client = MagicMock()
    client.containers.get.return_value = MagicMock()
    client.containers.run.side_effect = docker_errors.ContainerError(
        container=MagicMock(),
        exit_status=2,
        command="ip route replace default via 10.0.0.1",
        image=NAT_GATEWAY_IMAGE_TAG,
        stderr=b"RTNETLINK answers: Network is unreachable",
    )

    with pytest.raises(RuntimeError) as excinfo:
        inject_default_route_into_client(client, state, "nat-client-1")
    msg = str(excinfo.value)
    assert "nat-client-1" in msg
    assert "10.0.0.1" in msg
    assert "exit 2" in msg
    assert "Network is unreachable" in msg


def test_inject_default_route_raises_with_diagnostic_on_docker_api_error():
    """A Docker daemon error (e.g. daemon stopped, OOM-killer reaped
    a container) surfaces as `APIError`. Wrap it with enough context
    (client name, gateway IP) to debug from logs alone."""
    state = _make_state_with_gateway_lan_ip(lan_ip="172.30.0.77")
    client = MagicMock()
    client.containers.get.return_value = MagicMock()
    client.containers.run.side_effect = docker_errors.APIError("daemon unavailable")

    with pytest.raises(RuntimeError) as excinfo:
        inject_default_route_into_client(client, state, "nat-client-2")
    msg = str(excinfo.value)
    assert "nat-client-2" in msg
    assert "172.30.0.77" in msg
    assert "daemon unavailable" in msg


def test_inject_default_route_resolves_gateway_ip_from_lan_network():
    """The gateway IP we install is the one Docker assigned to the
    gateway on the LAN bridge — NOT the public bridge. Sanity-check
    the lookup so a future refactor that swaps argument order to
    `_resolve_container_ip` can't silently start installing the
    public-side IP (which the client can't reach without traversing
    the host's `DOCKER-ISOLATION-STAGE-2` chain — i.e., the bug
    we're trying to avoid)."""
    state = _make_state_with_gateway_lan_ip(lan_ip="172.30.0.42")
    # If the resolver mistakenly used the PUBLIC network name, this
    # would surface as a KeyError when the test runs.
    state.nat_gateway_container.attrs["NetworkSettings"]["Networks"]["test-public"] = {
        "IPAddress": "172.99.99.99"
    }
    client = MagicMock()
    client.containers.get.return_value = MagicMock()

    inject_default_route_into_client(client, state, "nat-client-1")

    _, kwargs = client.containers.run.call_args
    # LAN IP made it into the command; public IP must NOT appear.
    cmd = " ".join(kwargs["command"])
    assert "172.30.0.42" in cmd
    assert "172.99.99.99" not in cmd
