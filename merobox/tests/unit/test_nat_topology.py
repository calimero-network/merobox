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
    NatTopologyState,
    boot_node_bootstrap_multiaddrs,
    gateway_base_image,
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


def test_boot_node_image_tag_is_ghcr_pull():
    """The boot-node image is pulled from
    `ghcr.io/calimero-network/boot-node` — the registry tag
    published by the boot-node repo's `release.yml`. We pin the
    `:edge` tag specifically (mirroring how merobox pulls
    `ghcr.io/calimero-network/merod:edge` for clients), so a
    fresh boot-node release flows through to merobox without a
    merobox-side bump. If the constant ever drifts off the GHCR
    namespace we catch it here rather than at first-pull (which
    would 404 mid-`setup_nat_topology` instead of failing fast
    in the unit suite)."""
    assert BOOT_NODE_IMAGE_TAG.startswith("ghcr.io/calimero-network/boot-node")
    assert BOOT_NODE_IMAGE_TAG.endswith(":edge")


def test_gateway_base_image_default_is_stock_alpine(monkeypatch):
    """The NAT-gateway role uses a stock alpine image with
    iptables installed inline via `apk add` at first run — NOT
    a wrapper image with a bundled Dockerfile. Bundling broke
    under PyInstaller (non-`.py` files weren't included in the
    frozen binary); inlining the iptables setup via `exec_run`
    sidesteps the bundling problem entirely.

    With the `MEROBOX_NAT_GATEWAY_IMAGE` env override, a CI that
    wants a digest-pinned image can opt into one without a
    merobox release; this test asserts the DEFAULT (no env var)
    stays on `alpine:3.19` so a future bump is a deliberate
    source-level change rather than an accidental drift.
    """
    monkeypatch.delenv("MEROBOX_NAT_GATEWAY_IMAGE", raising=False)
    assert gateway_base_image() == "alpine:3.19"


def test_gateway_base_image_env_override(monkeypatch):
    """`MEROBOX_NAT_GATEWAY_IMAGE` lets CI pin to a digest
    (e.g. `alpine@sha256:<digest>`) without shipping a merobox
    release. The override is resolved per-call rather than at
    module-import, so monkeypatch alone is enough — no
    `importlib.reload` dance, and the override stays scoped to
    this test by pytest's monkeypatch cleanup."""
    pinned = "alpine@sha256:" + "0" * 64
    monkeypatch.setenv("MEROBOX_NAT_GATEWAY_IMAGE", pinned)
    assert gateway_base_image() == pinned


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
    matching algorithm. The accepted shape is base58btc multihash
    (1-9, A-H, J-N, P-Z, a-k, m-z) of ≥40 chars — broad enough to
    accept any libp2p-emitted PeerId, not just the Ed25519
    `12D3KooW…` shape."""
    pattern = r'PeerId\("([1-9A-HJ-NP-Za-km-z]{40,})"\)'
    sample = f'Peer id: PeerId("{_PEER_ID}"), some trailing junk'
    match = re.search(pattern, sample)
    assert match is not None
    assert match.group(1) == _PEER_ID


def test_resolve_boot_node_peer_id_re_accepts_non_ed25519_prefix():
    """Regression guard against re-anchoring the regex on `12D3KooW`.
    libp2p keypair algorithms other than Ed25519 produce different
    base58btc prefixes (`Qm…` for RSA, `16U…` for secp256k1, etc.).
    Pin that the regex accepts them — otherwise an operator pinning
    the boot-node image to a stable RSA key would silently fail
    peer-id extraction with the same misleading log shape."""
    pattern = r'PeerId\("([1-9A-HJ-NP-Za-km-z]{40,})"\)'
    # A plausibly-shaped RSA peer id (base58btc, ≥40 chars, leading Qm).
    rsa_peer_id = "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"
    sample = f'Peer id: PeerId("{rsa_peer_id}")'
    match = re.search(pattern, sample)
    assert match is not None
    assert match.group(1) == rsa_peer_id


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
    """State with the gateway's LAN IP pre-set on the state directly.

    `inject_default_route_into_client` reads `state.gateway_lan_ip`
    rather than introspecting the container, so the fixture just
    stashes the IP there. Container `attrs` are no longer touched
    by the inject path."""
    public_net = MagicMock()
    public_net.name = "test-public"
    lan_net = MagicMock()
    lan_net.name = "test-lan"
    return NatTopologyState(
        public_network=public_net,
        lan_network=lan_net,
        boot_node_container=MagicMock(),
        boot_node_peer_id="12D3KooW" + "X" * 44,
        boot_node_public_ip="172.30.1.5",
        nat_gateway_container=MagicMock(),
        gateway_lan_ip=lan_ip,
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
    # Image must be the stock alpine base — we install iproute2
    # inline via apk inside the sidecar's command, rather than
    # depending on a wrapper image that bundles a Dockerfile (which
    # broke under PyInstaller frozen builds because non-`.py`
    # files aren't bundled by default).
    assert args[0] == gateway_base_image()
    # Stock alpine has no ENTRYPOINT we'd need to override, but
    # we still pass `sh -c` because the command is a multi-step
    # script (apk add + ip route replace + verification grep).
    assert kwargs["entrypoint"] == ["sh", "-c"]
    # Command shape: `set -e; ip route replace; ip route show |
    # grep`. The trailing grep is the "did the route actually
    # land?" verification — `ip route replace` returns 0 even on
    # silently-rejected targets, so the explicit show+grep is
    # what surfaces a route-install failure as a non-zero sidecar
    # exit.
    assert len(kwargs["command"]) == 1
    cmd = kwargs["command"][0]
    assert "set -e" in cmd
    # Inline iproute2 install — stock alpine ships busybox `ip`
    # but its output format differs from iproute2's, and the
    # downstream `ip route show default | awk | grep` parse
    # depends on the iproute2 shape.
    assert "apk add --no-cache iproute2" in cmd
    # shlex.quote on a plain IP returns it unchanged (no shell-meta
    # chars), but the wrapper still calls it so a future override
    # with a hostname containing spaces would be safely quoted.
    assert "ip route replace default via 172.30.0.99" in cmd
    assert "ip route show default" in cmd
    # `grep -Fxq` (fixed-string, exact-match, quiet) — defeats the
    # regex-metacharacter misinterpretation that default grep does
    # with the `.` chars in IPv4 dotted-quad form.
    assert "grep -Fxq" in cmd
    assert "default via 172.30.0.99" in cmd
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
        image=gateway_base_image(),
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


def test_inject_default_route_uses_state_gateway_ip_verbatim():
    """The gateway IP threaded into the sidecar's `ip route` command
    is the one captured at `setup_nat_topology` time (pre-assigned
    via `ipv4_address=` at connect-time, not read back from
    `container.attrs`). The IPAM-population race that broke earlier
    iterations only mattered when we polled `container.attrs`; with
    pre-assigned + stashed-on-state, the test asserts the exact
    string flows through unchanged."""
    state = _make_state_with_gateway_lan_ip(lan_ip="172.30.0.42")
    client = MagicMock()
    client.containers.get.return_value = MagicMock()

    inject_default_route_into_client(client, state, "nat-client-1")

    _, kwargs = client.containers.run.call_args
    cmd = " ".join(kwargs["command"])
    assert "172.30.0.42" in cmd


def test_inject_default_route_raises_when_state_has_no_gateway_ip():
    """Empty `state.gateway_lan_ip` means setup didn't run, didn't
    finish, or was corrupted — refuse to inject. Otherwise we'd
    spawn a sidecar that runs `ip route replace default via ` (no
    target) and either errors with a confusing RTNETLINK message or
    silently no-ops, depending on iproute2 build."""
    state = _make_state_with_gateway_lan_ip(lan_ip="")
    client = MagicMock()

    with pytest.raises(RuntimeError) as excinfo:
        inject_default_route_into_client(client, state, "nat-client-1")
    msg = str(excinfo.value)
    assert "nat-client-1" in msg
    assert "gateway_lan_ip" in msg or "gateway IP" in msg
    # Make sure we bailed BEFORE consulting Docker.
    client.containers.get.assert_not_called()
    client.containers.run.assert_not_called()


# ---------------------------------------------------------------------------
# _pick_gateway_lan_ip
# ---------------------------------------------------------------------------
#
# The gateway's LAN IP is pre-assigned (not read back from
# `container.attrs`) so the test pins the picking convention: second
# usable address in the network's subnet — `.1` belongs to Docker's
# bridge-gateway interface.


from merobox.topology.nat import _pick_gateway_lan_ip  # noqa: E402


def _mock_lan_network_with_subnet(subnet: str) -> MagicMock:
    """LAN-network mock whose `attrs["IPAM"]["Config"][0]["Subnet"]`
    returns the given subnet after `reload()`."""
    net = MagicMock()
    net.name = "test-lan"
    net.attrs = {"IPAM": {"Config": [{"Subnet": subnet}]}}
    return net


def test_pick_gateway_lan_ip_picks_second_address_of_subnet():
    """For a `/24`, the gateway takes `.2`. `.0` is the network
    address (unusable) and `.1` is the bridge's host-side IP — both
    are reserved by Docker, so `.2` is the first free slot."""
    net = _mock_lan_network_with_subnet("172.30.0.0/24")
    assert _pick_gateway_lan_ip(net) == "172.30.0.2"


def test_pick_gateway_lan_ip_works_for_larger_subnet():
    """A `/16` still picks `.0.2`. The convention is `.network+2`,
    not `.network+1`-of-the-last-octet, so larger subnets are
    fine."""
    net = _mock_lan_network_with_subnet("10.0.0.0/16")
    assert _pick_gateway_lan_ip(net) == "10.0.0.2"


def test_pick_gateway_lan_ip_raises_if_subnet_missing():
    """An IPAM config without a subnet (driver=null, or a malformed
    network attrs payload) raises with a diagnostic — silently
    falling back to a default subnet would risk colliding with the
    runner's host network and dropping unrelated traffic."""
    net = MagicMock()
    net.name = "broken-lan"
    net.attrs = {"IPAM": {"Config": []}}
    with pytest.raises(RuntimeError) as excinfo:
        _pick_gateway_lan_ip(net)
    assert "broken-lan" in str(excinfo.value)
    assert "subnet" in str(excinfo.value).lower()


def test_pick_gateway_lan_ip_reloads_network_before_reading_attrs():
    """The network attrs are populated by the daemon after creation
    and only show up after a `reload()` call. Verify the reload
    happens — otherwise a freshly-created network would yield
    `attrs={}` and the `Config[0]` lookup would explode."""
    net = _mock_lan_network_with_subnet("192.168.50.0/24")
    _pick_gateway_lan_ip(net)
    net.reload.assert_called_once()


# ---------------------------------------------------------------------------
# _pick_lan_subnet
# ---------------------------------------------------------------------------
#
# Subnet has to be user-configured (Docker rejects ipv4_address= on
# auto-subnetted networks), but it can't collide with neighbouring
# workflows on the same host. Determinism per workflow name solves
# both — same name yields same subnet (rerun finds its own leftovers);
# different names yield different subnets (parallel CI doesn't trip
# over itself).


from merobox.topology.nat import _pick_lan_subnet  # noqa: E402


def test_pick_lan_subnet_is_deterministic_per_workflow_name():
    """Same workflow name must yield the same subnet across calls —
    otherwise a rerun would create a NEW subnet and leave leftover
    state on the previous one."""
    assert _pick_lan_subnet("nat-topology-cone-mode-smoke") == _pick_lan_subnet(
        "nat-topology-cone-mode-smoke"
    )


def test_pick_lan_subnet_varies_across_workflow_names():
    """Different workflow names should land on different subnets so
    parallel runs don't collide. (Probability of collision is ~1/256
    per pair, so this can theoretically flake on adversarial naming
    — but the two real workflow names we ship don't, which is what
    the assertion checks here.)"""
    cone = _pick_lan_subnet("nat-topology-cone-mode-smoke")
    sym = _pick_lan_subnet("nat-topology-symmetric-mode-smoke")
    assert cone != sym


def test_pick_lan_subnet_returns_valid_rfc1918_24():
    """Subnet shape: `172.30.<octet>.0/24`. RFC1918 private; /24 is
    enough address space for the gateway + 250+ clients (we'll never
    spin up more than a handful)."""
    import ipaddress

    subnet = _pick_lan_subnet("any-workflow")
    network = ipaddress.IPv4Network(subnet)
    assert network.prefixlen == 24
    assert subnet.startswith("172.30.")
    assert subnet.endswith(".0/24")


# ---------------------------------------------------------------------------
# _pick_lan_subnet — overlap avoidance (the peer-restart-symmetric fix)
#
# The public network is created with a Docker-AUTO-assigned subnet
# BEFORE the LAN. Docker's pool can hand the public bridge a /16 (e.g.
# 172.30.0.0/16) that swallows the hashed LAN /24. Overlapping ranges on
# the gateway's two interfaces make the kernel's reverse-path lookup for
# return traffic ambiguous, silently killing the return path — the
# client SYN leaves but the reply never comes back, and the reachability
# probe hangs until `timeout` SIGTERMs it. When a `client` is supplied,
# the picker must dodge every existing network's subnet.
# ---------------------------------------------------------------------------


def _fake_docker_client_with_subnets(subnets):
    """Build a stub Docker client whose `networks.list()` returns
    networks carrying the given subnets, shaped like docker-py's
    `.attrs["IPAM"]["Config"]`."""

    class _FakeNet:
        def __init__(self, name, subnet):
            self.name = name
            self.attrs = {"IPAM": {"Config": [{"Subnet": subnet}]}}

    class _FakeNetworks:
        def __init__(self, nets):
            self._nets = nets

        def list(self):
            return self._nets

    class _FakeClient:
        def __init__(self, nets):
            self.networks = _FakeNetworks(nets)

    return _FakeClient([_FakeNet(f"net-{i}", s) for i, s in enumerate(subnets)])


def test_pick_lan_subnet_client_none_matches_pure_hash():
    """Passing no client preserves the historical pure-hash behaviour,
    so reruns on a clean host still reuse their own subnet."""
    assert _pick_lan_subnet("some-workflow", None) == _pick_lan_subnet("some-workflow")


def test_pick_lan_subnet_avoids_overlapping_public_16():
    """If the auto-assigned public network grabbed the /16 that
    contains the hashed /24, the picker must return a /24 that does
    NOT overlap it."""
    import ipaddress

    name = "Sync Resilience — Peer Restart (NAT symmetric)"
    hashed = _pick_lan_subnet(name)  # pure hash, no client
    # Simulate the public bridge swallowing the hashed /24 inside a /16.
    hashed_net = ipaddress.ip_network(hashed)
    public_16 = ipaddress.ip_network("172.30.0.0/16")
    assert hashed_net.overlaps(public_16)  # precondition for the bug

    client = _fake_docker_client_with_subnets(["172.30.0.0/16", "172.17.0.0/16"])
    chosen = _pick_lan_subnet(name, client)
    chosen_net = ipaddress.ip_network(chosen)
    # Must not overlap any existing network.
    assert not chosen_net.overlaps(public_16)
    assert not chosen_net.overlaps(ipaddress.ip_network("172.17.0.0/16"))


def test_pick_lan_subnet_falls_back_to_10_range_when_17230_exhausted():
    """If all of 172.30.0.0/16 is occupied, the picker falls back to a
    10.x /24 rather than returning an overlapping subnet."""
    import ipaddress

    client = _fake_docker_client_with_subnets(["172.30.0.0/16"])
    chosen = _pick_lan_subnet("whatever-workflow", client)
    chosen_net = ipaddress.ip_network(chosen)
    assert not chosen_net.overlaps(ipaddress.ip_network("172.30.0.0/16"))
    assert chosen.startswith("10.")


def test_pick_lan_subnet_returns_hashed_start_when_no_overlap():
    """With a client present but no conflicting networks, the picker
    still returns the deterministic hashed /24 (the search starts there
    and the first candidate is free)."""
    client = _fake_docker_client_with_subnets(["172.18.0.0/16", "172.17.0.0/16"])
    assert _pick_lan_subnet("cone-smoke", client) == _pick_lan_subnet("cone-smoke")


# ---------------------------------------------------------------------------
# _build_masquerade_cmd — interface-agnostic NAT rule
#
# The gateway's MASQUERADE must match on the LAN SOURCE SUBNET, not on an
# output interface (`-o eth0`). Docker doesn't guarantee that the public
# network becomes eth0 and the LAN eth1 — CI caught the inverted
# assignment, which put the old `-o eth0` rule on the LAN interface and
# left client→boot-node traffic un-masqueraded, killing the return path
# (the reachability probe hung to a 60s/exit-143 timeout). These tests
# pin the source-subnet form so the regression can't silently return.
# ---------------------------------------------------------------------------


from merobox.topology.nat import _build_masquerade_cmd  # noqa: E402


def test_masquerade_cmd_matches_lan_source_subnet_not_output_iface():
    """Cone rule: masquerade traffic FROM the LAN going anywhere but the
    LAN, with NO `-o <iface>` clause (the interface-name assumption is
    exactly the bug this avoids)."""
    cmd = _build_masquerade_cmd("172.30.48.0/24", "cone")
    # Source-subnet match present, destination-exclusion present.
    assert "-s" in cmd and "172.30.48.0/24" in cmd
    s_idx = cmd.index("-s")
    assert cmd[s_idx + 1] == "172.30.48.0/24"
    # `! -d <lan>` excludes intra-LAN traffic from masquerading.
    assert "!" in cmd and "-d" in cmd
    bang_idx = cmd.index("!")
    assert cmd[bang_idx : bang_idx + 3] == ["!", "-d", "172.30.48.0/24"]
    assert cmd[-1] == "MASQUERADE"
    # The whole point: no output-interface assumption.
    assert "-o" not in cmd
    assert "eth0" not in cmd and "eth1" not in cmd


def test_masquerade_cmd_symmetric_appends_random_fully():
    """Symmetric mode randomises the source port so DCUtR can't predict
    it — appended AFTER the base rule, not replacing the source match."""
    cmd = _build_masquerade_cmd("10.5.0.0/24", "symmetric")
    assert cmd[-1] == "--random-fully"
    assert "-o" not in cmd
    # Still source-subnet based.
    assert cmd[cmd.index("-s") + 1] == "10.5.0.0/24"


def test_masquerade_cmd_cone_has_no_random_fully():
    """Cone must NOT randomise — that would let it mask DCUtR-direct
    regressions the symmetric variant exists to catch."""
    cmd = _build_masquerade_cmd("172.30.1.0/24", "cone")
    assert "--random-fully" not in cmd


def test_masquerade_cmd_rejects_unknown_mode():
    """A bad nat_mode is a programming error, surfaced loudly rather
    than silently producing a cone rule under a symmetric label."""
    with pytest.raises(ValueError, match="nat_mode"):
        _build_masquerade_cmd("172.30.1.0/24", "double-cone")


# ---------------------------------------------------------------------------
# teardown_nat_topology
# ---------------------------------------------------------------------------
#
# Critical operational invariants:
#   * Stop+remove the boot-node and gateway containers (we own them).
#   * Disconnect any residual containers from the LAN/public networks
#     BEFORE attempting `net.remove()` — otherwise Docker refuses with
#     "network has active endpoints" and the next workflow run can't
#     create its own network with the same name.
#   * Errors on individual steps are logged but not re-raised —
#     partial teardown is better than leaving cleanup half-done.

from merobox.topology.nat import teardown_nat_topology  # noqa: E402


def _make_state_with_teardown_handles() -> NatTopologyState:
    """State whose containers + networks are MagicMocks we can
    inspect to verify the teardown sequence."""
    public_net = MagicMock()
    public_net.name = "test-public"
    public_net.attrs = {"Containers": {}}
    lan_net = MagicMock()
    lan_net.name = "test-lan"
    lan_net.attrs = {"Containers": {}}
    boot = MagicMock()
    boot.name = "test-boot-node"
    gw = MagicMock()
    gw.name = "test-gateway"
    return NatTopologyState(
        public_network=public_net,
        lan_network=lan_net,
        boot_node_container=boot,
        boot_node_peer_id="12D3KooW" + "X" * 44,
        boot_node_public_ip="172.30.1.5",
        nat_gateway_container=gw,
        gateway_lan_ip="172.30.0.2",
    )


def test_teardown_stops_and_removes_owned_containers():
    """Boot-node + gateway are both stopped + removed exactly once
    each. Order is gateway-first (closer to the leaves of the
    bridge graph) then boot-node — matches the spawn order's
    reverse."""
    state = _make_state_with_teardown_handles()
    teardown_nat_topology(MagicMock(), state, remove_networks=False)
    state.nat_gateway_container.stop.assert_called_once()
    state.nat_gateway_container.remove.assert_called_once_with(force=True)
    state.boot_node_container.stop.assert_called_once()
    state.boot_node_container.remove.assert_called_once_with(force=True)


def test_teardown_disconnects_stragglers_before_removing_network():
    """If the workflow executor failed mid-run and left leftover
    containers attached to the LAN network, the teardown must
    force-disconnect them BEFORE calling `net.remove()`. Otherwise
    Docker refuses with `network has active endpoints` and the next
    workflow run finds a stale network with the same name."""
    state = _make_state_with_teardown_handles()
    # Two containers still attached to the LAN bridge after a
    # crashed earlier run.
    state.lan_network.attrs = {
        "Containers": {
            "leftover-client-1-id": {"Name": "leftover-client-1"},
            "leftover-client-2-id": {"Name": "leftover-client-2"},
        }
    }
    teardown_nat_topology(MagicMock(), state, remove_networks=True)
    # Each leftover got force-disconnected from the LAN bridge.
    disconnect_calls = [c.args[0] for c in state.lan_network.disconnect.call_args_list]
    assert set(disconnect_calls) == {
        "leftover-client-1-id",
        "leftover-client-2-id",
    }
    # All `disconnect` calls were force=True.
    for call in state.lan_network.disconnect.call_args_list:
        assert call.kwargs.get("force") is True
    # Network removal happened AFTER disconnect — pin the order
    # via mock_calls.
    method_order = [
        name
        for name, _, _ in state.lan_network.mock_calls
        if name in ("reload", "disconnect", "remove")
    ]
    assert method_order.index("disconnect") < method_order.index("remove")


def test_teardown_skips_network_removal_when_remove_networks_false():
    """`remove_networks=False` must not touch the networks. Used by
    callers that want to retain the bridges across runs (e.g., a
    debugging shell that's still attached)."""
    state = _make_state_with_teardown_handles()
    teardown_nat_topology(MagicMock(), state, remove_networks=False)
    state.lan_network.remove.assert_not_called()
    state.public_network.remove.assert_not_called()
    state.lan_network.disconnect.assert_not_called()
    state.public_network.disconnect.assert_not_called()


def test_teardown_tolerates_individual_step_failures():
    """A container stop that raises must not abort the rest of
    teardown — every remaining container/network still gets its
    cleanup attempt. Otherwise a single flaky `stop()` leaves
    leaked state."""
    state = _make_state_with_teardown_handles()
    state.nat_gateway_container.stop.side_effect = RuntimeError("boom")
    # Must NOT propagate.
    teardown_nat_topology(MagicMock(), state, remove_networks=True)
    # gateway.remove still attempted despite stop failing.
    state.nat_gateway_container.remove.assert_called_once_with(force=True)
    # boot-node still got its cleanup.
    state.boot_node_container.stop.assert_called_once()
    state.boot_node_container.remove.assert_called_once_with(force=True)
    # Networks still removed.
    state.lan_network.remove.assert_called_once()
    state.public_network.remove.assert_called_once()
