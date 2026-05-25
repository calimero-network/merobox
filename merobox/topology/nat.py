"""NAT-topology orchestration.

Spawns a four-piece topology in Docker:

    [ public bridge ]                            [ --internal LAN bridge ]
    boot-node container  <-- nat-gateway -->  client-1 ... client-N

The boot-node is the relay/rendezvous server (the released
calimero-network/boot-node binary wrapped in a thin image). The
gateway straddles both bridges and runs `iptables MASQUERADE` so
clients can REACH the public bridge but cannot be REACHED from it
directly — they must register a relay reservation on the boot-node
to be findable. That's the precondition for exercising the relay-
reservation-recovery code path in calimero-network/core#2446.

Public entrypoints:

* :func:`setup_nat_topology` — called by the workflow executor when a
  `topology: { type: nat }` block is present. Creates the two
  networks, builds the bundled images if needed, spawns the four
  containers in the right order, waits for relay reservations to
  land, and returns a teardown handle.

* :func:`teardown_nat_topology` — symmetric counterpart, used by the
  workflow executor's exit path (and the merobox `nuke` command).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import docker
import docker.errors
import docker.models.containers
import docker.models.networks

from merobox.commands.utils import console

# ---------------------------------------------------------------------------
# Image identifiers
# ---------------------------------------------------------------------------
#
# Images are built on first use from the Dockerfiles in
# `merobox/topology/images/<name>/`. Once built they're cached locally
# as `merobox/<name>:local` so subsequent runs skip the build entirely.
# Operators can also build + tag them out-of-band and the build will
# short-circuit.

BOOT_NODE_IMAGE_TAG = "merobox/boot-node:local"
NAT_GATEWAY_IMAGE_TAG = "merobox/nat-gateway:local"

# Default version of the boot-node binary baked into the boot-node
# image. Bump in lockstep with the calimero-network/boot-node release
# that's also deployed to the devnet, so test behaviour matches what
# real clients see. Override per-workflow via
# `topology.boot_node.image` if a specific build is required.
DEFAULT_BOOT_NODE_VERSION = "0.8.0"

# Boot-node's `--port` default. The image's Dockerfile EXPOSEs the
# same value; if you bump this, change both.
BOOT_NODE_PORT = 4001


# Docker container + network names accept `[a-zA-Z0-9][a-zA-Z0-9_.-]*`;
# anything outside that set has to be stripped or replaced before the
# name is used as a resource prefix. Workflow display names
# ("NAT Topology — Cone Mode Smoke") routinely include spaces,
# em-dashes, and other Unicode that Docker rejects. The slugifier
# below maps each unsafe char to `-`, collapses runs, lowercases,
# trims leading/trailing dashes, and finally guarantees the leading
# char is alphanumeric. Empty / all-unsafe inputs collapse to
# `merobox-nat`.
_SLUG_REPLACE = re.compile(r"[^a-zA-Z0-9_.-]+")
_SLUG_COLLAPSE = re.compile(r"-+")


def slugify_workflow_name(name: str) -> str:
    """Map a free-form workflow ``name:`` to a Docker-safe slug.

    Used as the prefix for the NAT topology's container + network
    names. The transformation is deterministic — the same workflow
    name slugs to the same result across runs, so a rerun finds and
    cleans up leftovers from a previous crashed run before recreating.
    """
    if not name:
        return "merobox-nat"
    slug = _SLUG_REPLACE.sub("-", name.strip())
    slug = _SLUG_COLLAPSE.sub("-", slug)
    slug = slug.strip("-.").lower()
    # Docker requires the first char be alphanumeric (no leading
    # dot/dash/underscore). Strip any remainder and fall back to the
    # default if nothing's left.
    while slug and not slug[0].isalnum():
        slug = slug[1:]
    return slug or "merobox-nat"


# How long to wait for clients to register a relay reservation with
# the boot-node before giving up. Sized for a single CI runner under
# load: the libp2p reservation handshake itself is well under a
# second, but the boot-node and clients have to fully come up first,
# and a slow runner can easily eat 30s on `merod init` alone.
RELAY_READINESS_TIMEOUT_SECONDS = 90

# Poll interval for the readiness check. 1s keeps the loop cheap;
# the assertion log line we're scanning for is emitted exactly once
# per reservation so missing a tick costs at most one second.
RELAY_READINESS_POLL_INTERVAL_SECONDS = 1.0


# ---------------------------------------------------------------------------
# State carried across setup / teardown
# ---------------------------------------------------------------------------


@dataclass
class NatTopologyState:
    """Handles to every container + network the topology created.

    The workflow executor holds this for the duration of the run and
    passes it back to :func:`teardown_nat_topology` at the end. Each
    field is a small, owned reference — we never mutate the actual
    Docker objects after setup, so there's no shared-state concern.
    """

    public_network: docker.models.networks.Network
    lan_network: docker.models.networks.Network
    boot_node_container: docker.models.containers.Container
    boot_node_peer_id: str
    boot_node_public_ip: str
    nat_gateway_container: docker.models.containers.Container
    # Client containers are spawned by the existing `NodeManager`
    # path; this list only holds the IDs so teardown can stop them
    # explicitly when the workflow ends without going through the
    # normal `stop_all_nodes` exit. Names rather than container
    # handles so a transient Docker reconnect doesn't invalidate the
    # references.
    client_names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Image building
# ---------------------------------------------------------------------------


def _images_root() -> Path:
    """Path to the bundled `merobox/topology/images/` directory."""
    return Path(__file__).parent / "images"


def _build_image_if_missing(
    client: docker.DockerClient,
    image_subdir: str,
    tag: str,
    build_args: dict[str, str] | None = None,
) -> None:
    """Ensure the named image exists locally, building it from the
    bundled Dockerfile if not.

    A pre-existing tag (built out-of-band, or left over from a prior
    workflow run) is treated as authoritative — we don't rebuild on
    every workflow start. To force a rebuild, `docker rmi <tag>` and
    re-run.
    """
    try:
        client.images.get(tag)
        console.print(f"[cyan]✓ Image {tag} already present[/cyan]")
        return
    except docker.errors.NotFound:
        pass

    dockerfile_dir = _images_root() / image_subdir
    if not (dockerfile_dir / "Dockerfile").exists():
        raise RuntimeError(
            f"No Dockerfile at {dockerfile_dir}. The merobox install "
            f"is incomplete — reinstall the package."
        )

    console.print(
        f"[yellow]Building {tag} from {dockerfile_dir} "
        f"(first-use, future runs reuse the cached image)...[/yellow]"
    )
    # `client.images.build()` returns `(image, build_log_generator)`.
    # The generator yields already-parsed JSON dicts from the build
    # stream (docker-py handles `decode=True` for us on this
    # high-level API — `decode=True` is only an explicit kwarg on
    # the lower-level `client.api.build`). We iterate the generator
    # to (a) drive the build to completion (it's lazy) and (b) trap
    # `{"error": …}` chunks so a failed layer surfaces as a Python
    # exception rather than as a phantom-success that fails later
    # when the missing image is needed.
    _image, build_logs = client.images.build(
        path=str(dockerfile_dir),
        tag=tag,
        buildargs=build_args or {},
        rm=True,
        forcerm=True,
        # Older Docker versions error if buildkit isn't enabled and
        # `--platform` is set; we let Docker pick the platform.
    )
    for chunk in build_logs:
        if isinstance(chunk, dict) and "error" in chunk:
            raise RuntimeError(f"Failed to build {tag}: {chunk.get('error')}")
    console.print(f"[green]✓ Built {tag}[/green]")


# ---------------------------------------------------------------------------
# Network creation
# ---------------------------------------------------------------------------


def _ensure_network(
    client: docker.DockerClient,
    name: str,
    internal: bool,
) -> docker.models.networks.Network:
    """Get-or-create a Docker bridge network.

    The `internal` flag is the key knob — internal=True means the
    network has no default gateway and no route to anything outside
    Docker, which is what makes the LAN bridge non-routable from the
    public side.
    """
    try:
        net = client.networks.get(name)
        # If a leftover from a prior run has the wrong `internal`
        # value, recreate it. Otherwise the NAT semantics get
        # silently swapped.
        attrs = net.attrs or {}
        existing_internal = bool(attrs.get("Internal", False))
        if existing_internal != internal:
            console.print(
                f"[yellow]Network {name} exists with internal={existing_internal}, "
                f"recreating with internal={internal}[/yellow]"
            )
            # Docker refuses `network.remove()` while any container is
            # still attached — common after a crashed previous run
            # where teardown didn't finish. Disconnect each attached
            # container first (with force=True so paused / dead
            # containers don't block); errors are tolerated because
            # the worst case is that `remove` below raises with a
            # clearer message about why.
            net.reload()
            attached = (net.attrs or {}).get("Containers", {}) or {}
            for container_id in list(attached.keys()):
                try:
                    net.disconnect(container_id, force=True)
                except Exception as e:
                    console.print(
                        f"[yellow]  failed to disconnect {container_id[:12]} from "
                        f"{name}: {e}[/yellow]"
                    )
            net.remove()
        else:
            console.print(f"[cyan]✓ Network {name} already exists[/cyan]")
            return net
    except docker.errors.NotFound:
        pass

    console.print(f"[yellow]Creating network: {name} (internal={internal})[/yellow]")
    net = client.networks.create(name=name, driver="bridge", internal=internal)
    console.print(f"[green]✓ Created network: {name}[/green]")
    return net


# ---------------------------------------------------------------------------
# Boot-node + gateway containers
# ---------------------------------------------------------------------------


def _spawn_boot_node(
    client: docker.DockerClient,
    image: str,
    public_network: docker.models.networks.Network,
    workflow_name: str,
) -> docker.models.containers.Container:
    """Start the boot-node container on the public bridge.

    Uses `--dev` for an ephemeral keypair by default. The container
    is labelled with the workflow name so `merobox nuke` can clean
    up leftovers between runs without affecting other workflows.
    """
    container_name = f"{workflow_name}-boot-node"
    # If a leftover from a previous run is still around, remove it —
    # the previous run probably crashed before its teardown ran.
    try:
        existing = client.containers.get(container_name)
        console.print(
            f"[yellow]Removing leftover {container_name} from prior run[/yellow]"
        )
        existing.remove(force=True)
    except docker.errors.NotFound:
        pass

    console.print(f"[yellow]Starting boot-node container {container_name}[/yellow]")
    container = client.containers.run(
        image=image,
        name=container_name,
        network=public_network.name,
        labels={
            "merobox.role": "boot-node",
            "merobox.workflow": workflow_name,
        },
        detach=True,
        # Default CMD ["boot-node", "--dev"] is the right shape; let
        # the image handle the rest. No env vars, no mounts.
    )
    return container


def _spawn_nat_gateway(
    client: docker.DockerClient,
    image: str,
    public_network: docker.models.networks.Network,
    lan_network: docker.models.networks.Network,
    nat_mode: str,
    workflow_name: str,
) -> docker.models.containers.Container:
    """Start the NAT-gateway container straddling both bridges.

    docker-py only lets `run()` attach to ONE network at create
    time, so we start on the public bridge then `connect` the LAN
    bridge afterwards. Capability NET_ADMIN is required for the
    container's iptables setup.
    """
    container_name = f"{workflow_name}-nat-gateway"
    try:
        existing = client.containers.get(container_name)
        console.print(
            f"[yellow]Removing leftover {container_name} from prior run[/yellow]"
        )
        existing.remove(force=True)
    except docker.errors.NotFound:
        pass

    console.print(
        f"[yellow]Starting NAT gateway {container_name} (mode={nat_mode})[/yellow]"
    )
    container = client.containers.run(
        image=image,
        name=container_name,
        network=public_network.name,
        labels={
            "merobox.role": "nat-gateway",
            "merobox.workflow": workflow_name,
        },
        environment={
            "NAT_MODE": nat_mode,
            # PUBLIC_IFACE: the first attached interface in the
            # container is eth0 (the public bridge here, by start
            # order). The container's entrypoint also defaults to
            # eth0, but we set it explicitly to make the contract
            # visible.
            "PUBLIC_IFACE": "eth0",
        },
        # iptables + sysctl ip_forward both need NET_ADMIN. The
        # rest of the container is otherwise unprivileged.
        cap_add=["NET_ADMIN"],
        detach=True,
    )
    # Attach the LAN bridge as a second interface (becomes eth1).
    lan_network.connect(container)
    return container


# ---------------------------------------------------------------------------
# Boot-node peer-id + IP resolution
# ---------------------------------------------------------------------------


def _resolve_container_ip(
    container: docker.models.containers.Container,
    network_name: str,
) -> str:
    """Fetch a container's IPv4 on the named network.

    docker-py keeps the network attachment metadata on the container
    attrs but only after a `reload()`. The first lookup right after
    `run()` returns empty; this helper polls briefly.
    """
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        container.reload()
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        net = networks.get(network_name) or {}
        ip = net.get("IPAddress")
        if ip:
            return ip
        time.sleep(0.1)
    raise RuntimeError(
        f"Container {container.name} didn't acquire an IP on {network_name} "
        f"within 5 seconds"
    )


def _resolve_boot_node_peer_id(
    container: docker.models.containers.Container,
) -> str:
    """Scan the boot-node's stdout for its libp2p peer id.

    The boot-node's `main.rs` does

        info!("Peer id: {:?}", peer_id);

    very early in startup (right after deriving the keypair). With
    `{:?}`, `PeerId`'s Debug impl renders as `PeerId("12D3KooW…")`
    — surrounding quotes included. We extract the `12D3KooW…`
    substring from inside those quotes; the surrounding tracing
    decoration (timestamps, level, target) doesn't matter.

    Log scraping (rather than a `--print-peer-id` flag that doesn't
    exist) is acceptable here because the line is emitted within
    the first ~100ms of process start — we'd be waiting on it
    anyway as a readiness signal. A short 30s ceiling is plenty.
    """
    # `PeerId("12D3KooW…")` — anchor on the prefix that follows
    # `PeerId(`, allow any non-quote chars up to the closing
    # `")`, and capture the inside. `12D3KooW` is the Ed25519
    # multihash prefix base58btc emits for all Calimero/libp2p
    # default keypairs.
    peer_re = re.compile(r'PeerId\("(12D3KooW[^"]+)"\)')
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            logs = container.logs(tail=200).decode("utf-8", errors="replace")
        except Exception:
            logs = ""
        match = peer_re.search(logs)
        if match:
            return match.group(1)
        time.sleep(0.5)

    # Surface the boot-node's actual log tail so a future format
    # change is obvious from the failure message, rather than
    # opaquely manifesting as a 30s timeout the operator has to
    # `docker logs` themselves to diagnose.
    try:
        tail = container.logs(tail=50).decode("utf-8", errors="replace")
    except Exception:
        tail = "<unable to read logs>"
    raise RuntimeError(
        f"Boot-node {container.name} didn't print its peer id within 30s.\n"
        f'Expected log shape: `Peer id: PeerId("12D3KooW…")`.\n'
        f"Last 50 log lines from the container:\n{tail}"
    )


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------


def setup_nat_topology(
    client: docker.DockerClient,
    workflow_name: str,
    nat_mode: str,
    boot_node_image_override: str | None = None,
) -> NatTopologyState:
    """Bring up the NAT topology and return a teardown handle.

    Order of operations matters:

      1. Build (or reuse) the boot-node + nat-gateway images.
      2. Create the two networks (public + LAN-internal).
      3. Start the boot-node on the public network.
      4. Start the NAT gateway straddling both networks; the
         iptables MASQUERADE rule lands here.
      5. Wait for the boot-node's peer id to land in its logs and
         resolve its public-network IP. This gives us the multiaddr
         the clients need to dial.
      6. Caller spawns clients separately (via the normal node-
         management path) using the multiaddr returned in
         :class:`NatTopologyState`.

    The boot-node container needs to be reachable from the LAN
    network even though it's not connected to it — the NAT gateway
    handles that via its iptables rule.
    """
    if nat_mode not in ("cone", "symmetric"):
        raise ValueError(f"nat_mode must be 'cone' or 'symmetric', got {nat_mode!r}")

    # Step 1: images.
    boot_node_image = boot_node_image_override or BOOT_NODE_IMAGE_TAG
    if not boot_node_image_override:
        _build_image_if_missing(
            client,
            "boot-node",
            BOOT_NODE_IMAGE_TAG,
            build_args={"BOOT_NODE_VERSION": DEFAULT_BOOT_NODE_VERSION},
        )
    _build_image_if_missing(client, "nat-gateway", NAT_GATEWAY_IMAGE_TAG)

    # Step 2: networks. Names are workflow-scoped so parallel
    # workflows don't clobber each other.
    public_network = _ensure_network(client, f"{workflow_name}-public", internal=False)
    lan_network = _ensure_network(client, f"{workflow_name}-lan", internal=True)

    # Step 3: boot-node.
    boot_node = _spawn_boot_node(client, boot_node_image, public_network, workflow_name)

    # Step 4: NAT gateway. After this returns, the LAN network has
    # outbound routing to the public network.
    gateway = _spawn_nat_gateway(
        client,
        NAT_GATEWAY_IMAGE_TAG,
        public_network,
        lan_network,
        nat_mode,
        workflow_name,
    )

    # Step 5: resolve boot-node's IP + peer id. Clients need both
    # to build their bootstrap multiaddr.
    boot_node_public_ip = _resolve_container_ip(boot_node, public_network.name)
    boot_node_peer_id = _resolve_boot_node_peer_id(boot_node)
    console.print(
        f"[green]✓ Boot-node ready at "
        f"/ip4/{boot_node_public_ip}/tcp/{BOOT_NODE_PORT}/p2p/{boot_node_peer_id}[/green]"
    )

    return NatTopologyState(
        public_network=public_network,
        lan_network=lan_network,
        boot_node_container=boot_node,
        boot_node_peer_id=boot_node_peer_id,
        boot_node_public_ip=boot_node_public_ip,
        nat_gateway_container=gateway,
    )


def wait_for_relay_reservations(
    client: docker.DockerClient,
    state: NatTopologyState,
    timeout_seconds: int = RELAY_READINESS_TIMEOUT_SECONDS,
) -> bool:
    """Block until every client container's merod has logged a
    successful relay reservation with the boot-node, or the timeout
    elapses.

    The signal we scan for is `ReservationReqAccepted` — emitted by
    `crates/network/src/handlers/stream/swarm/relay.rs` at DEBUG level
    when libp2p's relay-client receives an accept from the relay
    server. CI workflows already run merod at log_level=debug
    (`apply_e2e_defaults`'s `RUST_LOG`), so the line lands in
    stdout without any extra config.

    Returns True when every client has at least one Accepted; False
    on timeout. Caller decides whether to fail the workflow (CI
    should; an interactive `bootstrap run` might prefer a warning).
    """
    if not state.client_names:
        # Nothing to wait on — caller hasn't spawned clients yet, or
        # this is a boot-node-only smoke test. Trivially ready.
        return True

    deadline = time.monotonic() + timeout_seconds
    pending = set(state.client_names)
    # Track containers that have disappeared mid-wait so we report
    # every one at the end rather than bailing on the first.
    # Returning False early would mask a multi-client crash and
    # leave the operator to find the others manually.
    disappeared: set[str] = set()
    console.print(
        f"[yellow]Waiting up to {timeout_seconds}s for relay reservations on "
        f"{len(pending)} client(s)...[/yellow]"
    )

    while time.monotonic() < deadline and pending:
        for name in list(pending):
            try:
                container = client.containers.get(name)
            except docker.errors.NotFound:
                # The container disappeared while we were waiting —
                # almost certainly a crash during startup. Record
                # the loss so we can report every disappeared client
                # at the end; don't bail on the first one (others
                # might also have crashed and the operator deserves
                # the full list).
                console.print(
                    f"[red]✗ Client container {name} disappeared "
                    f"while waiting for relay reservation[/red]"
                )
                disappeared.add(name)
                pending.discard(name)
                continue
            try:
                tail = container.logs(tail=400).decode("utf-8", errors="replace")
            except Exception:
                tail = ""
            if "ReservationReqAccepted" in tail:
                pending.discard(name)
                console.print(f"[green]✓ {name} registered a relay reservation[/green]")
        if pending:
            time.sleep(RELAY_READINESS_POLL_INTERVAL_SECONDS)

    # Any client that disappeared is a hard failure regardless of
    # whether the others reached the reservation signal.
    if disappeared:
        console.print(
            f"[red]✗ {len(disappeared)} client(s) disappeared during the "
            f"readiness wait: {sorted(disappeared)}[/red]"
        )
        return False

    if pending:
        console.print(
            f"[red]✗ Timed out waiting for relay reservation on: "
            f"{sorted(pending)}[/red]"
        )
        return False
    return True


def boot_node_bootstrap_multiaddrs(state: NatTopologyState) -> list[str]:
    """Build the bootstrap-multiaddr list a NAT'd client should use.

    Returns both the TCP and QUIC forms. Clients only need to know
    about the boot-node — peer-to-peer hops between siblings happen
    via the boot-node's relay circuit, not via direct dial.
    """
    return [
        f"/ip4/{state.boot_node_public_ip}/tcp/{BOOT_NODE_PORT}/p2p/{state.boot_node_peer_id}",
        f"/ip4/{state.boot_node_public_ip}/udp/{BOOT_NODE_PORT}/quic-v1/p2p/{state.boot_node_peer_id}",
    ]


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


def teardown_nat_topology(
    client: docker.DockerClient,
    state: NatTopologyState,
    remove_networks: bool = True,
) -> None:
    """Stop and remove every container + network the topology created.

    Client containers are stopped by the normal node-management
    teardown path; this function only owns the boot-node, gateway,
    and the two networks. Errors on each step are logged but not
    propagated — partial teardown is better than an exception
    leaving even more state behind.
    """
    for container in (state.nat_gateway_container, state.boot_node_container):
        try:
            container.stop(timeout=5)
        except Exception as e:
            console.print(f"[yellow]Failed to stop {container.name}: {e}[/yellow]")
        try:
            container.remove(force=True)
        except Exception as e:
            console.print(f"[yellow]Failed to remove {container.name}: {e}[/yellow]")

    if remove_networks:
        for net in (state.lan_network, state.public_network):
            try:
                net.reload()
                net.remove()
            except Exception as e:
                console.print(
                    f"[yellow]Failed to remove network {net.name}: {e}[/yellow]"
                )
