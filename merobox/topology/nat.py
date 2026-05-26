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
    # The gateway's IP on the LAN bridge, captured at connect-time.
    # We pre-assign this rather than reading it back from
    # `container.attrs["NetworkSettings"]["Networks"][...]["IPAddress"]`
    # because Docker's daemon doesn't reliably populate that field
    # after a `network.connect()` on a second bridge — CI observed
    # the IP staying empty for ≥30s even with create+connect+start
    # ordering. By specifying `ipv4_address=` at connect-time we
    # bypass the IPAM-population race entirely: we already know the
    # IP we asked for.
    gateway_lan_ip: str = ""
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
    subnet: str | None = None,
) -> docker.models.networks.Network:
    """Get-or-create a Docker bridge network.

    The `internal` flag is the key knob — internal=True means the
    network has no default gateway and no route to anything outside
    Docker, which is what makes the LAN bridge non-routable from the
    public side.

    `subnet` optionally pins the network's IPAM subnet. Pinning is
    REQUIRED if any caller later wants to attach a container with
    `network.connect(container, ipv4_address=X)` — Docker rejects
    the explicit IP with `"user specified IP address is supported
    only when connecting to networks with user configured subnets"`
    unless the IPAM config was user-specified at creation time. We
    pin the LAN network's subnet for that reason; the public
    network leaves it auto-assigned (no static IPs ever requested
    there).
    """
    try:
        net = client.networks.get(name)
        # If a leftover from a prior run has the wrong `internal`
        # value, recreate it. Otherwise the NAT semantics get
        # silently swapped.
        attrs = net.attrs or {}
        existing_internal = bool(attrs.get("Internal", False))
        # Recreate if either internal flag mismatches OR the subnet
        # constraint changed. A leftover network with no user-pinned
        # subnet would fail every subsequent ipv4_address request,
        # so we have to scrub it.
        existing_subnet = None
        ipam_config = (attrs.get("IPAM") or {}).get("Config") or []
        if ipam_config and "Subnet" in ipam_config[0]:
            existing_subnet = ipam_config[0]["Subnet"]
        needs_recreate = existing_internal != internal or (
            subnet is not None and existing_subnet != subnet
        )
        if needs_recreate:
            console.print(
                f"[yellow]Network {name} exists with "
                f"internal={existing_internal}, subnet={existing_subnet!r}; "
                f"recreating with internal={internal}, subnet={subnet!r}"
                f"[/yellow]"
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

    console.print(
        f"[yellow]Creating network: {name} "
        f"(internal={internal}, subnet={subnet!r})[/yellow]"
    )
    create_kwargs: dict = {"name": name, "driver": "bridge", "internal": internal}
    if subnet is not None:
        # `docker.types.IPAMConfig` wraps the IPAM pool shape the
        # Docker API expects. Gateway is intentionally left unset
        # so Docker picks the first usable address (`.1`) as the
        # bridge's host-side gateway — the same default as auto-
        # assigned networks; only the subnet itself is pinned.
        create_kwargs["ipam"] = docker.types.IPAMConfig(
            pool_configs=[docker.types.IPAMPool(subnet=subnet)]
        )
    net = client.networks.create(**create_kwargs)
    console.print(f"[green]✓ Created network: {name}[/green]")
    return net


# ---------------------------------------------------------------------------
# Client default-route injection
# ---------------------------------------------------------------------------
#
# Docker bridge isolation is stricter than I'd assumed in earlier
# iterations: the daemon installs `DOCKER-ISOLATION-STAGE-2` rules that
# DROP any forward between two different Docker bridges. So even with
# both networks created as ``internal=False`` and a NAT gateway
# straddling them, a client on the LAN bridge cannot reach the boot-
# node on the public bridge VIA THE HOST. Docker's own isolation chain
# discards the packet before any user FORWARD rule fires.
#
# The only way to traverse a Docker bridge boundary is through a
# container with veths in BOTH bridges — i.e., the NAT gateway. For
# that to happen, the client's default route has to point at the
# gateway's LAN-side IP. Docker installs the LAN bridge's host-side
# IP as the default route by default, which is wrong for our purpose;
# we explicitly replace it with the gateway's LAN IP.
#
# Implementation: spawn a one-shot privileged sidecar that shares the
# client's network namespace (``--network container:<client>`` +
# ``CAP_NET_ADMIN``) and runs ``ip route replace``. Reuses the bundled
# ``merobox/nat-gateway:local`` image — it's already built locally and
# already ships iproute2 — so no extra image is needed and the stock
# merod container doesn't have to be rebuilt with iproute2 inside.
#
# Why this replaces the earlier host-iptables approach
# ----------------------------------------------------
#
# A previous iteration tried to enforce NAT semantics by installing a
# host-side DROP rule on `DOCKER-USER` that filtered public→LAN
# traffic. That solved the wrong problem: cross-bridge traffic was
# already being dropped by Docker's own isolation chain, so clients
# couldn't reach the boot-node at all (CI showed "Handshake with the
# remote timed out" on every dial). With proper route injection, the
# NAT gateway IS the cross-bridge path, and its lack of inbound port
# forwarding naturally drops autonat dial-backs from the boot-node —
# we get the asymmetric reachability the test wants without needing
# any host iptables manipulation.


def inject_default_route_into_client(
    client: docker.DockerClient,
    state: NatTopologyState,
    client_container_name: str,
) -> None:
    """Replace the named client's default route to point at the NAT
    gateway's LAN-side IP.

    Without this, the client tries to reach the boot-node via Docker's
    auto-injected default route (the LAN bridge's host-side gateway),
    which Docker's `DOCKER-ISOLATION-STAGE-2` chain then DROPs because
    the destination is on a different bridge. The result is silent
    handshake timeouts — the worst possible failure mode for a smoke
    test, because the workflow looks like it's running.

    With this, packets exit the client into the LAN bridge, get
    delivered L2 to the NAT gateway container (which is on the same
    bridge), and the gateway forwards them via its own veth on the
    public bridge — entirely inside the gateway's network namespace,
    so Docker's host-level FORWARD chain never sees the cross-bridge
    hop.

    Implementation: a one-shot privileged sidecar sharing the
    client's netns runs `ip route replace`. The sidecar image is
    `merobox/nat-gateway:local` because it's already built locally,
    already in the image cache, and already ships iproute2. The
    sidecar exits as soon as the route replace returns; no
    long-running process, no port collision concern, no rebuild of
    the merod image with iproute2.

    Raises ``RuntimeError`` if the sidecar fails — silent fallback
    here would produce a workflow that LOOKS like it's exercising
    the relay path while every dial silently times out instead.
    """
    if not state.gateway_lan_ip:
        # Should never happen — `setup_nat_topology` populates this
        # eagerly. Catch it loudly rather than launching a sidecar
        # that would default-route to the empty string.
        raise RuntimeError(
            "NatTopologyState.gateway_lan_ip is empty; the NAT "
            "topology setup did not complete cleanly. Refusing to "
            "inject a route into "
            f"{client_container_name!r} without a known gateway IP."
        )
    gateway_lan_ip = state.gateway_lan_ip
    console.print(
        f"[yellow]Injecting default route into {client_container_name} "
        f"via NAT gateway at {gateway_lan_ip}[/yellow]"
    )
    try:
        # Make sure the client container actually exists before we
        # spawn a sidecar that pins to its netns — `docker run
        # --network container:NAME` would fail with an unhelpful
        # "No such container" if it didn't, well into the API call
        # and after a brief image-pull pause. Doing the lookup first
        # gives a clean error.
        client.containers.get(client_container_name)
    except docker.errors.NotFound as e:
        raise RuntimeError(
            f"Cannot inject default route: client container "
            f"{client_container_name!r} not found"
        ) from e
    try:
        client.containers.run(
            NAT_GATEWAY_IMAGE_TAG,
            # Override the image's ENTRYPOINT. The nat-gateway image
            # ships an ENTRYPOINT script that runs `sysctl -w
            # net.ipv4.ip_forward=1` + installs iptables MASQUERADE
            # — fine when the container plays the gateway role with
            # its OWN netns, but fatal in this sidecar context where
            # we share the client's netns (sysctl errors with
            # `Read-only file system` and we never get to the route
            # install). Passing a fresh entrypoint+command pair
            # makes the sidecar do exactly one thing: install the
            # default route, then exit.
            entrypoint=["sh", "-c"],
            command=[f"ip route replace default via {gateway_lan_ip}"],
            # Sharing the client's network namespace is what lets
            # `ip route` see and modify the client's routing table.
            # The sidecar gets the client's loopback + LAN-bridge
            # veth and no other interfaces.
            network_mode=f"container:{client_container_name}",
            cap_add=["NET_ADMIN"],
            remove=True,
            detach=False,
            # Surface stderr in the exception below if the sidecar
            # exits non-zero — `ip route` itself logs the actual
            # reason (RTNETLINK errors, etc.) on stderr.
            stdout=True,
            stderr=True,
        )
    except docker.errors.ContainerError as e:
        stderr = (
            e.stderr.decode("utf-8", errors="replace") if e.stderr else "<no stderr>"
        )
        raise RuntimeError(
            f"Failed to install default route in {client_container_name} "
            f"(gateway {gateway_lan_ip}, exit {e.exit_status}): {stderr}"
        ) from e
    except docker.errors.APIError as e:
        raise RuntimeError(
            f"Docker API error installing default route in "
            f"{client_container_name} via NAT gateway at {gateway_lan_ip}: {e}"
        ) from e
    console.print(
        f"[green]✓ Default route in {client_container_name} now via "
        f"{gateway_lan_ip}[/green]"
    )


# Max time to wait for the client's `nc -zv <boot-node>:4001` to
# return "open" after the default-route injection. CI showed the
# forwarding path settles intermittently — first probe sometimes
# hits ICMP unreachable while ARP / forwarding state stabilises;
# a second probe ~1-2s later succeeds. 20s is generous for a slow
# CI runner without inflating the overall topology-setup budget.
NAT_CONNECTIVITY_PROBE_TIMEOUT_SECONDS = 20
NAT_CONNECTIVITY_PROBE_INTERVAL_SECONDS = 1.0


def wait_for_client_reachability(
    client: docker.DockerClient,
    state: NatTopologyState,
    client_container_name: str,
    *,
    timeout_seconds: float = NAT_CONNECTIVITY_PROBE_TIMEOUT_SECONDS,
) -> None:
    """Poll a TCP probe from the client to the boot-node until it
    succeeds or `timeout_seconds` elapse.

    Why
    ---
    `inject_default_route_into_client` returns as soon as the
    sidecar's `ip route replace` exits, but the kernel's forwarding
    path (per-iface forwarding, ARP cache, neighbour table) can
    take an additional second or two to settle. CI observed roughly
    1-in-3 retries where the first post-injection probe returned
    EHOSTUNREACH (busybox-nc's instant "punt!"); a probe ~1s later
    returned `open`. Without this polling step, merod inside the
    client races the forwarding-path warmup and caches the early
    failure as "peer unreachable" in its address book, never
    actually getting a relay reservation in the 90s readiness
    window.

    Raises RuntimeError if the probe never succeeds within the
    timeout — same shape as the other setup failures so the
    executor's failure path handles it uniformly.
    """
    deadline = time.monotonic() + timeout_seconds
    last_output = "<no probe attempted yet>"
    attempt = 0
    console.print(
        f"[yellow]Probing {client_container_name} → boot-node "
        f"({state.boot_node_public_ip}:{BOOT_NODE_PORT}) until reachable "
        f"(up to {timeout_seconds:.0f}s)...[/yellow]"
    )
    while time.monotonic() < deadline:
        attempt += 1
        try:
            out = client.containers.run(
                NAT_GATEWAY_IMAGE_TAG,
                entrypoint=["sh", "-c"],
                command=[
                    f"timeout 2 nc -zv {state.boot_node_public_ip} "
                    f"{BOOT_NODE_PORT} 2>&1"
                ],
                network_mode=f"container:{client_container_name}",
                cap_add=["NET_ADMIN"],
                remove=True,
                detach=False,
                stdout=True,
                stderr=True,
            )
            last_output = (
                out.decode("utf-8", errors="replace")
                if isinstance(out, bytes)
                else str(out)
            )
            # busybox nc -zv prints "<ip> (<ip>:<port>) open" on
            # success. Any other output (including "punt!" on
            # failure) means the probe didn't succeed.
            if "open" in last_output.lower():
                console.print(
                    f"[green]✓ {client_container_name} reached boot-node "
                    f"after {attempt} probe(s)[/green]"
                )
                return
        except docker.errors.ContainerError as e:
            # `nc` returns non-zero on connection failure; the
            # docker-py wrapper raises ContainerError. That's the
            # normal failure path here, not an exceptional one —
            # capture the stderr and try again.
            last_output = (
                e.stderr.decode("utf-8", errors="replace")
                if e.stderr
                else f"<no stderr; exit {e.exit_status}>"
            )
        except docker.errors.APIError as e:
            # Daemon-level error is unusual; surface it loudly.
            raise RuntimeError(
                f"Docker API error while probing {client_container_name} "
                f"→ boot-node: {e}"
            ) from e
        time.sleep(NAT_CONNECTIVITY_PROBE_INTERVAL_SECONDS)
    raise RuntimeError(
        f"{client_container_name} could not reach the boot-node "
        f"({state.boot_node_public_ip}:{BOOT_NODE_PORT}) within "
        f"{timeout_seconds:.0f}s after {attempt} probe(s). "
        f"Last probe output:\n{last_output}"
    )


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


def _pick_lan_subnet(workflow_name: str) -> str:
    """Choose a deterministic LAN-bridge subnet for the workflow.

    The subnet has to be user-configured (not Docker-auto-assigned)
    because `_spawn_nat_gateway` connects the gateway with
    `ipv4_address=`, and Docker rejects explicit IPs on
    auto-subnetted networks. Determinism per workflow name lets a
    rerun pick up the same subnet a previous crashed run created;
    different workflow names yield different subnets so parallel
    workflows don't collide.

    Layout: `172.30.<hash & 0xff>.0/24`. The 172.16.0.0/12 range is
    RFC1918 private and rarely in use on CI runners (which use
    172.17.x for the default Docker bridge). 256 distinct subnets
    is plenty — collisions would only matter if a single host ran
    more than ~256 NAT workflows in parallel, which we're nowhere
    near.
    """
    import hashlib

    octet = int(hashlib.sha256(workflow_name.encode()).hexdigest(), 16) & 0xFF
    return f"172.30.{octet}.0/24"


def _pick_gateway_lan_ip(lan_network: docker.models.networks.Network) -> str:
    """Pick a deterministic LAN-side IP for the NAT gateway.

    Reads the LAN network's IPAM subnet, returns the second usable
    address (``.2`` for a /24, etc.). The first usable address
    (``.1``) is reserved by Docker for the bridge's host-side
    interface (the LAN bridge's gateway). Picking ``.2`` is safe
    because the gateway is always the first container attached to
    the LAN network — Docker's IPAM allocates clients starting at
    ``.3`` from there.

    Why we pre-pick rather than read back: docker-py's
    `network.connect(container, ipv4_address=X)` accepts an
    explicit IP, while `network.connect(container)` lets Docker
    pick and the assigned IP only lands in `container.attrs` after
    an asynchronous daemon update that CI showed taking >30s
    (sometimes never inside our budget). Specifying the IP turns
    a race into a known.
    """
    import ipaddress

    lan_network.reload()
    ipam_config = (lan_network.attrs.get("IPAM") or {}).get("Config") or []
    if not ipam_config or "Subnet" not in ipam_config[0]:
        raise RuntimeError(
            f"LAN network {lan_network.name!r} has no IPAM subnet in attrs; "
            f"cannot pre-assign a gateway IP. This usually means the network "
            f"was created in a way that bypassed Docker's IPAM "
            f"(driver=null?). Recreate with `driver=bridge` and try again."
        )
    network = ipaddress.IPv4Network(ipam_config[0]["Subnet"])
    # `.0` is the network address, `.1` is the bridge gateway by
    # convention, so `.2` is the first non-reserved address.
    return str(network.network_address + 2)


def _spawn_nat_gateway(
    client: docker.DockerClient,
    image: str,
    public_network: docker.models.networks.Network,
    lan_network: docker.models.networks.Network,
    nat_mode: str,
    workflow_name: str,
) -> tuple[docker.models.containers.Container, str]:
    """Start the NAT-gateway container straddling both bridges.

    Returns the container handle AND its LAN-side IP. The IP is
    pre-assigned via `ipv4_address=` at connect-time rather than
    read back from `container.attrs`, because docker-py / dockerd
    asynchronously populate the per-network `IPAddress` field
    after `network.connect()` and CI observed that field staying
    empty for >30s on the second-network attachment (sometimes
    never inside our budget). Specifying the IP eliminates the
    race: we already know what we asked for.

    Sequence:
        create (paused) → connect LAN with ipv4_address → start

    Attaching the LAN bridge BEFORE start means the container
    boots with both interfaces wired up. CAP_NET_ADMIN is required
    for the container's internal iptables MASQUERADE setup.
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

    gateway_lan_ip = _pick_gateway_lan_ip(lan_network)
    console.print(
        f"[yellow]Starting NAT gateway {container_name} "
        f"(mode={nat_mode}, lan_ip={gateway_lan_ip})[/yellow]"
    )
    container = client.containers.create(
        image=image,
        name=container_name,
        network=public_network.name,
        labels={
            "merobox.role": "nat-gateway",
            "merobox.workflow": workflow_name,
        },
        environment={
            "NAT_MODE": nat_mode,
            # PUBLIC_IFACE: by the order networks were attached at
            # create+connect time, the public bridge becomes eth0
            # and the LAN bridge becomes eth1. The container's
            # entrypoint defaults to eth0 too, but we set it
            # explicitly to make the contract visible.
            "PUBLIC_IFACE": "eth0",
        },
        # `net.ipv4.ip_forward=1` is the master switch, but Linux
        # also requires PER-INTERFACE forwarding to be enabled on
        # the input interface (`net.ipv4.conf.<iface>.forwarding`).
        # Per-iface flags inherit from `default.forwarding` AT the
        # moment the interface is attached — and for the LAN-side
        # eth1 added via `network.connect()` post-create, the
        # inheritance was timing-dependent enough that CI saw
        # eth1.forwarding=0 even with the master switch set, which
        # makes the kernel return ICMP "network unreachable" to
        # clients (the immediate `punt!` from `nc` we diagnosed).
        # Setting BOTH master + default at create-time pins the
        # inheritance for any interface attached afterwards. The
        # entrypoint also writes every present per-iface flag in
        # a loop, as a belt-and-suspenders fallback.
        sysctls={
            "net.ipv4.ip_forward": "1",
            "net.ipv4.conf.all.forwarding": "1",
            "net.ipv4.conf.default.forwarding": "1",
        },
        # iptables + sysctl ip_forward both need NET_ADMIN. The
        # rest of the container is otherwise unprivileged.
        cap_add=["NET_ADMIN"],
        detach=True,
    )
    # Attach the LAN bridge with an explicit IP BEFORE start —
    # eliminates the IPAM-population race entirely.
    lan_network.connect(container, ipv4_address=gateway_lan_ip)
    container.start()
    # Reload so subsequent reads of `container.attrs` see both
    # network entries. Doesn't matter for the LAN IP (we already
    # know it) but useful for diagnostics.
    container.reload()
    # Sanity-check: gateway must actually be running. If the
    # entrypoint exited (e.g., MASQUERADE install failed), the
    # whole topology is broken downstream — clients would dial
    # through a dead gateway and get EHOSTUNREACH. Fail loudly
    # here instead.
    status = container.attrs.get("State", {}).get("Status", "unknown")
    if status != "running":
        # Pull the entrypoint's stderr into the exception so the
        # operator can see WHY the gateway died.
        try:
            tail = container.logs(tail=200).decode("utf-8", errors="replace")
        except Exception:
            tail = "<could not read gateway logs>"
        raise RuntimeError(
            f"NAT gateway container {container_name!r} exited "
            f"during startup (status={status!r}); its entrypoint "
            f"failed. Logs:\n{tail}"
        )
    return container, gateway_lan_ip


# ---------------------------------------------------------------------------
# Boot-node peer-id + IP resolution
# ---------------------------------------------------------------------------


def _resolve_container_ip(
    container: docker.models.containers.Container,
    network_name: str,
    timeout_seconds: float = 30.0,
) -> str:
    """Fetch a container's IPv4 on the named network.

    docker-py keeps the network attachment metadata on the container
    attrs but only after a `reload()`. There are TWO distinct delays
    we have to absorb:

    1. **First-network attachment** (the one passed to
       ``containers.run(network=…)``): Docker assigns an IP
       synchronously as part of container creation, but it doesn't
       always land in the *first* `container.attrs["NetworkSettings"]`
       fetched via `.reload()` — small race between the create call
       returning and the daemon's network-state update. Sub-second.

    2. **Second-network attachment** (via `network.connect(container)`
       after the container is already running): the daemon processes
       the attachment on a separate path that can take noticeably
       longer, especially on `--internal=True` bridges where it has
       to allocate from a fresh subnet without a gateway. We've
       observed up to ~6s in CI; the previous 5s ceiling was the
       reason the cone-mode workflow failed with `gateway didn't
       acquire an IP on …-lan within 5 seconds`.

    30s default ceiling covers a slow CI runner with headroom; if a
    real failure mode emerges the diagnostic at the bottom helps
    distinguish "never attached" from "attached but no IP".
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        container.reload()
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        net = networks.get(network_name) or {}
        ip = net.get("IPAddress")
        if ip:
            return ip
        time.sleep(0.2)
    # Surface what Docker DID know about the container's networks
    # so a future failure is diagnosable from the error alone.
    try:
        container.reload()
        attached = container.attrs.get("NetworkSettings", {}).get("Networks", {}) or {}
    except Exception:
        attached = {}
    known = ", ".join(sorted(attached.keys())) or "<none>"
    raise RuntimeError(
        f"Container {container.name} didn't acquire an IP on "
        f"{network_name} within {timeout_seconds:.0f} seconds "
        f"(attached networks per docker: {known})"
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
    # NOTE on the LAN bridge being NON-internal:
    #
    # The original design called for `internal=True` here so clients
    # were physically unreachable from the public-bridge side; that
    # would make autonat's dial-back from the boot-node fail, mark
    # the client as NAT'd, and trigger the relay-reservation flow
    # we want to exercise. In practice, CI hit a Docker daemon
    # behaviour where the NAT-gateway container — attached to the
    # internal bridge via `network.connect()` after the container
    # was already running — never had an `IPAddress` populated
    # under `NetworkSettings.Networks[<lan>]`, even though Docker
    # listed it as attached. The behaviour reproduced consistently
    # across CI runs; the workflow could never get past the gateway
    # IP-resolution step.
    #
    # Falling back to `internal=False` is fine for our purposes:
    # Docker's `DOCKER-ISOLATION-STAGE-2` chain already blocks the
    # cross-bridge LAN→public path through the host, so even with
    # the LAN bridge "non-internal" the client can't reach the
    # public bridge directly via the host. The only cross-bridge
    # path is through the NAT gateway container (which has veths in
    # both bridges and forwards inside its own netns) — exactly the
    # path the test wants to exercise. We make the client USE that
    # path by injecting a default-route override post-startup; see
    # `inject_default_route_into_client`.
    lan_network = _ensure_network(
        client,
        f"{workflow_name}-lan",
        internal=False,
        # User-configured subnet is REQUIRED because the gateway
        # gets attached with an explicit `ipv4_address=` (see
        # `_spawn_nat_gateway`). The actual subnet doesn't matter
        # — picking a deterministic-per-workflow value from a
        # private range keeps parallel workflows from colliding
        # while still letting reruns find their own leftover state.
        subnet=_pick_lan_subnet(workflow_name),
    )

    # Step 3: boot-node.
    boot_node = _spawn_boot_node(client, boot_node_image, public_network, workflow_name)

    # Step 4: NAT gateway. After this returns, the LAN network has
    # outbound routing to the public network. The gateway's LAN-side
    # IP is pre-assigned (not read back) so we know it without
    # waiting on Docker's async IPAM-population path.
    gateway, gateway_lan_ip = _spawn_nat_gateway(
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
        gateway_lan_ip=gateway_lan_ip,
    )


def wait_for_clients_connected_to_boot_node(
    client: docker.DockerClient,
    state: NatTopologyState,
    timeout_seconds: int = RELAY_READINESS_TIMEOUT_SECONDS,
) -> bool:
    """Block until every client has established a libp2p connection
    to the boot-node, or the timeout elapses.

    Why this and NOT `wait_for_relay_reservations`
    -----------------------------------------------

    The original intent was to gate readiness on
    `ReservationReqAccepted` — the signal that the client has
    successfully registered a circuit-relay-v2 reservation with the
    boot-node. That's the signal you'd want once the relay path is
    actually exercised end-to-end. But as of today, merod doesn't
    auto-trigger a reservation when autonat reports a NAT'd
    address: relay-reservation is gated behind the
    `advertise_address` branch in `crates/network/src/discovery.rs`
    (see calimero-network/core#2475). Without an advertised
    external address, no reservation is ever requested — so the
    smoke test would hang for 90s every run while the merod side
    of the gap remains open.

    The connection-established signal IS reachable today: clients
    successfully dial the boot-node through the NAT gateway, do a
    yamux handshake, exchange Identify, and Calimero logs
    "Connection established" at DEBUG. That proves the topology
    infrastructure works end-to-end (route injection + NAT MASQUERADE
    + per-iface forwarding + bridge plumbing).

    Once core#2475 lands, switch the executor's readiness gate from
    `wait_for_clients_connected_to_boot_node` to
    `wait_for_relay_reservations` (which we keep below for that
    purpose) so the smoke test also asserts the relay path.

    Returns True when every client has at least one matching
    "Connection established" line referring to the boot-node's peer
    id; False on timeout. Caller decides whether to fail the
    workflow."""
    return _wait_for_log_line(
        client,
        state,
        # The exact shape emitted by Calimero's swarm handler:
        #   `Connection established  peer_id=PeerId("<bn>") endpoint=Dialer ...`
        # We match on `peer_id=Some(PeerId("<bn>"))` because the
        # SwarmEvent's `peer_id` is `Some<PeerId>` in the underlying
        # tracing emit (see `calimero_network::handlers::stream::swarm`).
        f'peer_id=Some(PeerId("{state.boot_node_peer_id}"))',
        signal_name="boot-node connection",
        timeout_seconds=timeout_seconds,
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

    NOTE: this is currently NOT wired into the executor's readiness
    gate — see `wait_for_clients_connected_to_boot_node` for why
    (core#2475 gap). Once core ships the relay-reservation trigger,
    swap the executor's call site to this function and the smoke
    test will assert the strictly stronger property.

    Returns True when every client has at least one Accepted; False
    on timeout. Caller decides whether to fail the workflow (CI
    should; an interactive `bootstrap run` might prefer a warning).
    """
    return _wait_for_log_line(
        client,
        state,
        "ReservationReqAccepted",
        signal_name="relay reservation",
        timeout_seconds=timeout_seconds,
    )


def _wait_for_log_line(
    client: docker.DockerClient,
    state: NatTopologyState,
    needle: str,
    *,
    signal_name: str,
    timeout_seconds: int,
) -> bool:
    """Poll every client's container logs for ``needle`` (substring
    match) until each has at least one hit, or ``timeout_seconds``
    elapses. On timeout, dump topology diagnostics to console.

    ``signal_name`` appears in the console messages — e.g.,
    "relay reservation" or "boot-node connection" — so the operator
    can tell at a glance which readiness gate fired.

    Returns True if every client matched; False on timeout. A
    client container disappearing mid-wait counts as a failure for
    that client only, but the wait continues for the others so the
    final error reports every casualty."""
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
        f"[yellow]Waiting up to {timeout_seconds}s for {signal_name} on "
        f"{len(pending)} client(s)...[/yellow]"
    )

    while time.monotonic() < deadline and pending:
        for name in list(pending):
            try:
                container = client.containers.get(name)
            except docker.errors.NotFound:
                console.print(
                    f"[red]✗ Client container {name} disappeared "
                    f"while waiting for {signal_name}[/red]"
                )
                disappeared.add(name)
                pending.discard(name)
                continue
            try:
                tail = container.logs(tail=400).decode("utf-8", errors="replace")
            except Exception:
                tail = ""
            if needle in tail:
                pending.discard(name)
                console.print(f"[green]✓ {name} reached {signal_name} signal[/green]")
        if pending:
            time.sleep(RELAY_READINESS_POLL_INTERVAL_SECONDS)

    if disappeared:
        console.print(
            f"[red]✗ {len(disappeared)} client(s) disappeared during the "
            f"{signal_name} wait: {sorted(disappeared)}[/red]"
        )
        return False

    if pending:
        console.print(
            f"[red]✗ Timed out waiting for {signal_name} on: "
            f"{sorted(pending)}[/red]"
        )
        # On timeout, dump everything that helps diagnose WHY no
        # signal showed up. Cheap to gather, expensive to have to
        # add later when the next CI failure lands. Each block is
        # best-effort; a missing piece shouldn't suppress the others.
        _dump_topology_diagnostics(client, state)
        return False
    return True


def _dump_topology_diagnostics(
    client: docker.DockerClient,
    state: NatTopologyState,
) -> None:
    """Print gateway logs, client routes, boot-node listening
    addresses, and gateway iptables — every datum needed to pin
    down a relay-reservation timeout. Called from the readiness-gate
    failure path; safe to call ad hoc as well."""
    console.print("[yellow]── NAT topology diagnostics ──[/yellow]")
    # Gateway logs (entrypoint output: iptables rules, ip routes,
    # any FATAL the script emitted).
    try:
        state.nat_gateway_container.reload()
        gw_status = state.nat_gateway_container.attrs.get("State", {}).get(
            "Status", "?"
        )
        gw_logs = state.nat_gateway_container.logs(tail=200).decode(
            "utf-8", errors="replace"
        )
        console.print(
            f"[yellow]NAT gateway container status={gw_status}; logs:\n"
            f"{gw_logs}[/yellow]"
        )
    except Exception as e:
        console.print(f"[yellow]Failed to read gateway logs: {e}[/yellow]")
    # Gateway runtime state: ip_forward sysctl value (the
    # entrypoint's WARN-on-failure path can hide a 0), FORWARD
    # chain hit counters (default policy + per-rule), iptables-save
    # for a complete picture. exec_run is in the container's
    # NETNS but a fresh process — captures the live state at
    # diagnostic time, not just the startup snapshot.
    try:
        ec, out = state.nat_gateway_container.exec_run(
            [
                "sh",
                "-c",
                (
                    "echo '--- ip_forward ---';"
                    " cat /proc/sys/net/ipv4/ip_forward;"
                    " echo '--- per-iface forwarding ---';"
                    " for f in /proc/sys/net/ipv4/conf/*/forwarding;"
                    '   do printf \'%s = \' "$f"; cat "$f"; done;'
                    " echo '--- FORWARD chain ---';"
                    " iptables -L FORWARD -nv;"
                    " echo '--- nat table ---';"
                    " iptables -t nat -L -nv;"
                    " echo '--- iptables-save ---';"
                    " iptables-save;"
                    " echo '--- conntrack (best effort) ---';"
                    " (conntrack -L 2>&1 | head -20)"
                    "  || echo 'conntrack tool not installed in gateway image';"
                ),
            ],
        )
        decoded = (
            out.decode("utf-8", errors="replace")
            if isinstance(out, bytes)
            else str(out)
        )
        console.print(f"[yellow]Gateway runtime state (exit {ec}):\n{decoded}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Failed to dump gateway runtime state: {e}[/yellow]")
    # Per-client routes (does the default route still point at the
    # gateway?). One-shot sidecar with shared netns, like the route
    # injection itself — except this one just reads.
    for name in state.client_names:
        try:
            out = client.containers.run(
                NAT_GATEWAY_IMAGE_TAG,
                entrypoint=["sh", "-c"],
                command=["ip route show; ip addr show"],
                network_mode=f"container:{name}",
                cap_add=["NET_ADMIN"],
                remove=True,
                detach=False,
                stdout=True,
                stderr=True,
            )
            decoded = (
                out.decode("utf-8", errors="replace") if isinstance(out, bytes) else out
            )
            console.print(f"[yellow]Client {name} netns state:\n{decoded}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Failed to dump {name} netns state: {e}[/yellow]")
    # End-to-end connectivity probes. From INSIDE the gateway, can
    # we reach the boot-node? (If not, gateway-side routing or
    # bridge isolation is broken; nothing the client does can
    # rescue this.) From INSIDE a client (via shared-netns sidecar),
    # can we ping the gateway? Reach the boot-node?
    try:
        ec, out = state.nat_gateway_container.exec_run(
            [
                "sh",
                "-c",
                (
                    f"echo '--- gw -> boot-node ({state.boot_node_public_ip}) ---';"
                    f" (timeout 3 nc -zv {state.boot_node_public_ip} {BOOT_NODE_PORT}"
                    "   2>&1 || true);"
                    " echo '--- gw -> public bridge default gateway ---';"
                    " (timeout 3 ping -c 1 -W 2 -I eth0 8.8.8.8 2>&1 | head -5 || true);"
                ),
            ],
        )
        decoded = (
            out.decode("utf-8", errors="replace")
            if isinstance(out, bytes)
            else str(out)
        )
        console.print(
            f"[yellow]Gateway → boot-node probe (exit {ec}):\n{decoded}[/yellow]"
        )
    except Exception as e:
        console.print(f"[yellow]Gateway connectivity probe failed: {e}[/yellow]")
    for name in state.client_names[:1]:  # first client is enough
        try:
            out = client.containers.run(
                NAT_GATEWAY_IMAGE_TAG,
                entrypoint=["sh", "-c"],
                command=[
                    (
                        f"echo '--- client -> gateway ({state.gateway_lan_ip}) ---';"
                        f" (timeout 3 ping -c 1 -W 2 {state.gateway_lan_ip} 2>&1"
                        "   | head -5 || true);"
                        f" echo '--- client -> boot-node ({state.boot_node_public_ip}) ---';"
                        f" (timeout 3 nc -zv {state.boot_node_public_ip} {BOOT_NODE_PORT}"
                        "   2>&1 || true);"
                    ),
                ],
                network_mode=f"container:{name}",
                cap_add=["NET_ADMIN"],
                remove=True,
                detach=False,
                stdout=True,
                stderr=True,
            )
            decoded = (
                out.decode("utf-8", errors="replace") if isinstance(out, bytes) else out
            )
            console.print(
                f"[yellow]Client {name} → boot-node probe:\n{decoded}[/yellow]"
            )
        except Exception as e:
            console.print(f"[yellow]Client {name} probe failed: {e}[/yellow]")
    # Boot-node listening addresses — we want to see what merod's
    # libp2p stack is actually advertising. Looking for `/p2p/` and
    # `relay` in particular.
    try:
        bn_logs = state.boot_node_container.logs(tail=80).decode(
            "utf-8", errors="replace"
        )
        console.print(f"[yellow]Boot-node logs (tail):\n{bn_logs}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Failed to read boot-node logs: {e}[/yellow]")
    console.print("[yellow]── end diagnostics ──[/yellow]")


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
    and the two networks. The default-route sidecars injected via
    `inject_default_route_into_client` are short-lived `--rm`
    containers that have already exited by the time we get here.
    Errors on each step are logged but not propagated — partial
    teardown is better than an exception leaving even more state
    behind.
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
