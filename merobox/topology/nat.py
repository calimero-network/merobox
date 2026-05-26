"""NAT-topology orchestration.

Spawns a four-piece topology in Docker:

    [ public bridge ]                            [ --internal LAN bridge ]
    boot-node container  <-- nat-gateway -->  client-1 ... client-N

The boot-node is the relay/rendezvous server (the released
calimero-network/boot-node binary wrapped in a thin image). The
gateway straddles both bridges and runs `iptables MASQUERADE` so
clients can REACH the public bridge but cannot be REACHED from it
directly — they must register a relay reservation on the boot-node
to be findable. That's the precondition for exercising merod's
relay-reservation flow + the recovery-on-expiry code path: only
peers behind a NAT actually go through the reservation code, so a
flat single-bridge topology can't trigger it.

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

import os
import re
import shlex
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

# The boot-node image is built on first use from the bundled Dockerfile
# in `merobox/topology/images/boot-node/`. Once built it's cached
# locally as `merobox/boot-node:local`. Tracked under merobox#TODO
# to move to a pre-built `ghcr.io/calimero-network/boot-node:<tag>`
# pull pattern (mirror how we pull merod), at which point this
# constant is replaced with the GHCR tag.
BOOT_NODE_IMAGE_TAG = "merobox/boot-node:local"

# The NAT-gateway container is a stock Alpine container with
# iptables + iproute2 added at first-run via `apk add` against the
# already-pulled image. We deliberately do NOT ship a wrapper image
# for this role:
#   * The previous incarnation built a `merobox/nat-gateway:local`
#     image from a bundled Dockerfile, which broke under PyInstaller
#     frozen builds because non-`.py` files aren't bundled by
#     default.
#   * The actual work the wrapper did (sysctl, iptables, sleep) is
#     three subprocess calls that we issue via `exec_run` from the
#     Python side in `_spawn_nat_gateway`. No image-build step, no
#     Dockerfile to maintain.
# Alpine 3.19 is small (~7 MB) and ubiquitously cached; the `apk
# add iptables iproute2` adds ~5 s on first run but is no worse
# than the previous Docker build step would have been.
#
# Override via `MEROBOX_NAT_GATEWAY_IMAGE` if you want to pin to a
# specific digest for reproducibility (recommended in production
# CI):
#
#     docker pull alpine:3.19
#     docker inspect alpine:3.19 --format '{{index .RepoDigests 0}}'
#     export MEROBOX_NAT_GATEWAY_IMAGE=alpine@sha256:<digest>
#
# We default to the floating tag rather than baking a digest into
# the source so we don't have to ship a merobox release every
# time alpine gets a patch refresh, but the override is wired
# through every call site (sidecars + gateway) so a single env
# var is enough for full pinning.
GATEWAY_BASE_IMAGE = os.environ.get("MEROBOX_NAT_GATEWAY_IMAGE", "alpine:3.19")

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


def _pull_image_if_missing(
    client: docker.DockerClient,
    tag: str,
) -> None:
    """Ensure a remote image is locally present, pulling on miss.

    docker-py's `containers.create()` does NOT auto-pull (unlike
    `containers.run()`, which does). When we use the create + start
    sequence for containers that need pre-start network attachment,
    a missing image surfaces as a 404 from the daemon mid-create.
    Explicit pull avoids the 404 and gives a clean diagnostic on
    actual fetch failure (network down, image moved, etc.).
    """
    try:
        client.images.get(tag)
        console.print(f"[cyan]✓ Image {tag} already present[/cyan]")
        return
    except docker.errors.NotFound:
        pass

    console.print(f"[yellow]Pulling {tag} (first-use)...[/yellow]")
    try:
        client.images.pull(tag)
    except docker.errors.APIError as e:
        raise RuntimeError(
            f"Failed to pull image {tag!r}: {e}. "
            f"Check Docker daemon connectivity and that the tag exists "
            f"on its registry."
        ) from e
    console.print(f"[green]✓ Pulled {tag}[/green]")


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
            # containers don't block). Track which disconnects
            # failed so we don't silently call `net.remove()` and
            # let Docker raise a misleading "in use" error: if any
            # disconnect failed, we ABORT with a diagnostic listing
            # the residual containers, so the operator can
            # intervene rather than the workflow proceeding past a
            # broken precondition.
            net.reload()
            attached = (net.attrs or {}).get("Containers", {}) or {}
            failed_disconnects: list[tuple[str, str]] = []
            for container_id in list(attached.keys()):
                try:
                    net.disconnect(container_id, force=True)
                except Exception as e:
                    console.print(
                        f"[yellow]  failed to disconnect {container_id[:12]} from "
                        f"{name}: {e}[/yellow]"
                    )
                    failed_disconnects.append((container_id, str(e)))
            if failed_disconnects:
                raise RuntimeError(
                    f"Cannot recreate network {name!r}: "
                    f"{len(failed_disconnects)} container(s) still attached "
                    f"after force-disconnect attempts. "
                    f"Residuals: {[c[:12] for c, _ in failed_disconnects]}. "
                    f"Run `docker network disconnect -f {name} <id>` manually, "
                    f"or `docker network rm {name}` and rerun."
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
# ``CAP_NET_ADMIN``) and runs ``ip route replace``. Uses a stock
# ``alpine:3.19`` image with iproute2 installed inline via ``apk``;
# the merod container itself doesn't need iproute2 inside.
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
    `alpine:3.19` (the same image we use for the NAT gateway
    role); `iproute2` is installed inline via `apk` because the
    busybox `ip` shipped with stock alpine has subtly different
    output formatting for `ip route show`. The sidecar exits as
    soon as the route replace + verification grep return; no
    long-running process, no port collision concern, no merod
    image rebuild.

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
            GATEWAY_BASE_IMAGE,
            # Stock alpine has busybox's limited `ip` command but no
            # iproute2 — `ip route replace` works either way (busybox
            # `ip` is sufficient for adding a default route), but we
            # also need to VERIFY the route landed via `ip route
            # show default | grep`, and busybox `ip route show` is
            # subtly different in output format. Install iproute2
            # explicitly so the route-install + verification both
            # use the canonical `ip` binary. Adds ~3s per sidecar
            # invocation for the apk fetch; acceptable for the
            # one-shot topology-setup phase.
            entrypoint=["sh", "-c"],
            # `set -e` + apk install + route replace + explicit
            # verification step. `ip route replace` is broadcasting-
            # friendly — it returns 0 even for malformed targets
            # that the kernel silently rejects, so we follow up
            # with `ip route show default` and grep for the
            # expected gateway. If the route really did land, the
            # grep matches and the sidecar exits 0; if it didn't,
            # grep exits 1 and the outer ContainerError surfaces
            # a useful error.
            #
            # `grep -F` (fixed-string mode) is important: the
            # gateway IP contains `.` characters which are regex
            # metacharacters in default grep mode. Without `-F`,
            # the pattern `172.30.0.99` would also match
            # `172X30X0X99` — false positives that could mask a
            # real route-install failure.
            command=[
                f"set -e; "
                f"apk add --no-cache iproute2 > /dev/null; "
                f"ip route replace default via {shlex.quote(gateway_lan_ip)}; "
                f"ip route show default "
                f"| awk '{{print $1, $2, $3}}' "
                f"| grep -Fxq {shlex.quote(f'default via {gateway_lan_ip}')}"
            ],
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
    # `shlex.quote` defangs the IP / port before they hit the shell.
    # In practice both come from Docker's IPAM and BOOT_NODE_PORT is a
    # module constant, so neither is user-controlled today; but the
    # `sh -c` form is one refactor away from being passed a workflow-
    # configured override, and keeping the shell-safety property
    # local to the call site is much cheaper than tracking trust
    # boundaries across the executor.
    safe_ip = shlex.quote(state.boot_node_public_ip)
    safe_port = shlex.quote(str(BOOT_NODE_PORT))
    while time.monotonic() < deadline:
        attempt += 1
        try:
            out = client.containers.run(
                GATEWAY_BASE_IMAGE,
                # Alpine's busybox already ships `nc` and `timeout`,
                # so we don't need any extra package install for
                # this probe. `nc -zv` keeps the "<ip> (<ip>:<port>)
                # open" output shape we match on below.
                entrypoint=["sh", "-c"],
                command=[f"timeout 2 nc -zv {safe_ip} {safe_port} 2>&1"],
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
            # busybox nc -zv prints the success line in a
            # well-defined shape:
            #
            #   <ip> (<ip>:<port>) open
            #
            # A bare `"open" in last_output.lower()` substring
            # match would false-positive on error text that
            # happens to contain "open" — e.g., the Operating-
            # system path mentioned in some perror strings, or a
            # future busybox locale where "ENOTOPEN" et al. get
            # spelled out. Match on the structured form: the
            # exact boot-node IP+port followed by `open` at the
            # end of the line. False-negative risk is bounded to
            # busybox-nc changing its output format, which would
            # also break the symmetric `punt!` failure branch and
            # be caught by a CI regression.
            success_marker = f"({state.boot_node_public_ip}:{BOOT_NODE_PORT}) open"
            if success_marker in last_output:
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


def _decode_exec_output(output: bytes | str | None) -> str:
    """Normalize `container.exec_run(...)` output for error strings.

    With `demux=False`, docker-py returns either bytes (stdout +
    stderr combined) or None (no output emitted, e.g. for a
    silently-failing exec). The naive `output.decode()` raises
    on None; the naive `str(output)` would surface the literal
    string `'None'` in our error messages. This helper picks the
    right branch and substitutes a clearer sentinel when output
    is missing.
    """
    if output is None:
        return "<no output>"
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)


def _spawn_nat_gateway(
    client: docker.DockerClient,
    public_network: docker.models.networks.Network,
    lan_network: docker.models.networks.Network,
    nat_mode: str,
    workflow_name: str,
) -> tuple[docker.models.containers.Container, str]:
    """Start the NAT-gateway container straddling both bridges.

    Returns the container handle AND its LAN-side IP.

    The gateway is just a stock `alpine:3.19` container with
    iptables + iproute2 added via `apk` at first-run; we
    deliberately do NOT ship our own image for this role anymore.
    The previous incarnation built a `merobox/nat-gateway:local`
    image from a bundled Dockerfile + entrypoint script, but:

      * The Dockerfile-bundling approach breaks under PyInstaller
        frozen builds (the merobox CLI ships as a single binary
        on releases; non-`.py` files would need explicit
        `--add-data` entries in the .spec, which adds maintenance
        for what's essentially a 50-line bash wrapper).
      * The actual work the wrapper did (sysctl, iptables, then
        sleep) is just three subprocess calls that we can issue
        from the merobox Python side via `exec_run` against a
        stock alpine container — no image-build step, no
        Dockerfile to maintain, no PyInstaller bundling concern.

    Sequence:
        create (paused) → connect LAN with ipv4_address → start
        → exec apk add → exec per-iface forwarding enable + eth1
        verify → exec iptables MASQUERADE rule

    Attaching the LAN bridge BEFORE start means the container
    boots with both interfaces wired up; the IP is pre-assigned
    via `ipv4_address=` to bypass docker-py's async
    IPAM-population path (which CI saw take >30s on second-
    network attachments). CAP_NET_ADMIN is required for the
    in-container iptables + sysctl writes.
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
        image=GATEWAY_BASE_IMAGE,
        name=container_name,
        network=public_network.name,
        # `sleep infinity` keeps the container alive while we
        # exec_run the apk + iptables setup. Equivalent to the
        # old entrypoint's `tail -f /dev/null` but uses
        # busybox-sleep so we don't depend on /dev/null being
        # writable.
        command=["sleep", "infinity"],
        labels={
            "merobox.role": "nat-gateway",
            "merobox.workflow": workflow_name,
        },
        # `net.ipv4.ip_forward=1` is the master switch, but Linux
        # also requires PER-INTERFACE forwarding to be enabled on
        # the input interface (`net.ipv4.conf.<iface>.forwarding`).
        # Per-iface flags inherit from `default.forwarding` AT
        # the moment the interface is attached — and for the
        # LAN-side eth1 added via `network.connect()`
        # post-create, the inheritance was timing-dependent
        # enough that CI saw eth1.forwarding=0 even with the
        # master switch set, which makes the kernel return ICMP
        # "network unreachable" to clients. Setting BOTH master
        # + default at create-time pins the inheritance for any
        # interface attached afterwards.
        sysctls={
            "net.ipv4.ip_forward": "1",
            "net.ipv4.conf.all.forwarding": "1",
            "net.ipv4.conf.default.forwarding": "1",
        },
        # iptables + sysctl ip_forward both need NET_ADMIN.
        cap_add=["NET_ADMIN"],
        detach=True,
    )
    # Attach the LAN bridge with an explicit IP BEFORE start —
    # eliminates the IPAM-population race entirely.
    lan_network.connect(container, ipv4_address=gateway_lan_ip)
    container.start()
    container.reload()

    # Sanity-check container is up before we exec_run into it.
    status = container.attrs.get("State", {}).get("Status", "unknown")
    if status != "running":
        try:
            tail = container.logs(tail=200).decode("utf-8", errors="replace")
        except Exception:
            tail = "<could not read gateway logs>"
        raise RuntimeError(
            f"NAT gateway container {container_name!r} exited "
            f"during startup (status={status!r}). Logs:\n{tail}"
        )

    # Install iptables + iproute2 inside the running container.
    # Alpine's busybox ships a `ip` command, but its iptables
    # subset is minimal and lacks `--random-fully` (needed for
    # the symmetric NAT mode). Pulling iproute2 explicitly so
    # the diagnostic dumps later (`ip route`, `ip link`, etc.)
    # work consistently across both modes.
    apk_cmd = [
        "apk",
        "add",
        "--no-cache",
        "iptables",
        "ip6tables",
        "iproute2",
    ]
    # Interface-name invariant.
    # The exec steps below (per-iface forwarding loop, eth1
    # verify, iptables `-o eth0`) assume Docker named the public
    # network `eth0` and the LAN network `eth1`. For bridge
    # networks under the default Linux kernel netdev driver this
    # is universal: `eth0` is the first network attached at
    # `containers.create(network=...)` time, `eth1` is the
    # second attached via `network.connect(...)` before start.
    # That ordering is what _this function_ does, deterministically,
    # so the invariant holds for every gateway we spawn. If a
    # future Docker engine or non-bridge driver renames interfaces,
    # the eth1-forwarding verify step below will fail with a
    # clear `read=''` error rather than silently MASQUERADE-ing
    # off the wrong interface. We deliberately do NOT resolve
    # interfaces dynamically (via MAC-match against container
    # attrs) — the added complexity buys nothing for the bridge-
    # driver case that's the only one merobox supports today.
    apk_ec, apk_out = container.exec_run(apk_cmd, demux=False)
    if apk_ec != 0:
        raise RuntimeError(
            f"NAT gateway {container_name!r}: failed to apk add "
            f"iptables (exit {apk_ec}): {_decode_exec_output(apk_out)}"
        )

    # Belt-and-suspenders per-interface forwarding enable.
    # The create-time `default.forwarding=1` sysctl should make
    # any interface attached after-the-fact inherit forwarding=1,
    # but CI has empirically seen the inheritance race under load
    # (eth1 ending up with forwarding=0 even though `default` is
    # 1), which silently caused the kernel to drop forwarded
    # packets and presented as cryptic ICMP "network unreachable"
    # at the client. Walking the per-iface tree here — AFTER
    # `container.start()` returned and AFTER the LAN-network
    # attachment fully landed — guarantees every interface that
    # actually exists has forwarding pinned to 1 regardless of
    # what `default` was at attach-time.
    #
    # `2>/dev/null || true` per-file because /proc lists pseudo-
    # interfaces (e.g. `all`, `default`) that are already covered
    # by the sysctl and pre-existing entries that ignore writes.
    fwd_ec, fwd_out = container.exec_run(
        [
            "sh",
            "-c",
            "for f in /proc/sys/net/ipv4/conf/*/forwarding; do "
            "echo 1 > $f 2>/dev/null || true; done",
        ],
        demux=False,
    )
    if fwd_ec != 0:
        # The `|| true` above means the script itself never exits
        # non-zero, so a non-zero here means `sh` itself failed —
        # worth surfacing rather than swallowing.
        raise RuntimeError(
            f"NAT gateway {container_name!r}: failed to enable "
            f"per-interface forwarding (exit {fwd_ec}): "
            f"{_decode_exec_output(fwd_out)}"
        )

    # Verify eth1 specifically — that's the interface the LAN
    # bridge is attached to and the one that matters for the
    # NAT-forwarding semantics this gateway exists to provide.
    # If forwarding is off on eth1 despite the sysctl + loop
    # above, MASQUERADE will install but packets will still be
    # dropped; fail loudly here so the surfaced error points at
    # the actual cause.
    verify_ec, verify_out = container.exec_run(
        ["cat", "/proc/sys/net/ipv4/conf/eth1/forwarding"],
        demux=False,
    )
    verify_decoded = _decode_exec_output(verify_out).strip()
    if verify_ec != 0 or verify_decoded != "1":
        raise RuntimeError(
            f"NAT gateway {container_name!r}: eth1 forwarding is "
            f"not enabled (read={verify_decoded!r}, exit "
            f"{verify_ec}). This means the LAN bridge attachment "
            f"either landed without inheriting the master "
            f"forwarding sysctl OR a host-level `net.ipv4."
            f"conf.eth1.forwarding=0` is overriding the "
            f"container sysctl namespace."
        )

    # Install the MASQUERADE rule per `nat_mode`. For symmetric
    # we try `--random-fully` first; if iptables rejects the
    # flag (older kernel without iptables-extensions support),
    # we fall back to plain MASQUERADE BUT FAIL the spawn —
    # silently running cone semantics under a symmetric label
    # would mask DCUtR-related regressions in downstream tests.
    masquerade_base = [
        "iptables",
        "-t",
        "nat",
        "-A",
        "POSTROUTING",
        "-o",
        "eth0",
        "-j",
        "MASQUERADE",
    ]
    if nat_mode == "cone":
        iptables_cmd = masquerade_base
    elif nat_mode == "symmetric":
        iptables_cmd = masquerade_base + ["--random-fully"]
    else:
        raise ValueError(f"nat_mode must be 'cone' or 'symmetric', got {nat_mode!r}")

    ipt_ec, ipt_out = container.exec_run(iptables_cmd, demux=False)
    if ipt_ec != 0:
        # Tear down the half-spawned gateway before re-raising.
        # Without this, the container would be left running with
        # `sleep infinity` and NO MASQUERADE rule installed —
        # downstream teardown does eventually catch it (it's
        # labelled with `merobox.workflow`), but leaving the
        # cleanup until a later phase makes diagnostic dumps
        # noisier and risks a partial-failure path where the
        # caller doesn't run teardown at all (e.g. interactive
        # CLI flow). Best-effort: any cleanup error is reported
        # to the console but never overrides the original
        # RuntimeError.
        decoded = _decode_exec_output(ipt_out)
        try:
            container.remove(force=True)
        except Exception as cleanup_exc:
            console.print(
                f"[yellow]NAT gateway {container_name!r}: "
                f"cleanup after iptables failure also failed: "
                f"{cleanup_exc}[/yellow]"
            )
        raise RuntimeError(
            f"NAT gateway {container_name!r}: failed to install "
            f"iptables MASQUERADE ({nat_mode}, exit {ipt_ec}): "
            f"{decoded}.\n"
            f"For symmetric mode, this most often means the host "
            f"kernel + iptables build lacks the `random-fully` "
            f"extension. Use `nat_mode: cone` or upgrade the "
            f"runner to a kernel/iptables that supports it."
        )

    console.print(
        f"[green]✓ NAT gateway {container_name} up at "
        f"{gateway_lan_ip} (mode={nat_mode})[/green]"
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
    poll_interval = 0.2
    while True:
        container.reload()
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        net = networks.get(network_name) or {}
        ip = net.get("IPAddress")
        if ip:
            return ip
        # Check the deadline BEFORE sleeping. Without this the loop
        # can over-run the timeout by up to one poll-interval (200ms)
        # because the last sleep happens unconditionally — minor for
        # the 30s default but matters if a caller passes a tight
        # deadline.
        if time.monotonic() + poll_interval >= deadline:
            break
        time.sleep(poll_interval)
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
    # `PeerId("<id>")` — Debug-format from libp2p. The inner peer
    # id is base58btc-encoded multihash; the leading character set
    # depends on the keypair algorithm:
    #
    #   * Ed25519  → `12D3KooW…`  (the Calimero default)
    #   * RSA      → `Qm…` / `12…` depending on key size
    #   * secp256k1 → `16U…` / `1A…`
    #   * Future libp2p multihash codes → any base58btc char
    #
    # The previous regex hard-coded `12D3KooW`, which would silently
    # fail to extract a peer id from a boot-node built with a non-
    # default keypair (e.g., an operator overriding the boot-node
    # image with a stable RSA key for production tests). Match on
    # the structural envelope (`PeerId("…")`) and require the
    # captured string to look like a base58btc multihash (1-9, A-H,
    # J-N, P-Z, a-k, m-z — 58 chars, base58btc alphabet) of typical
    # peer-id length (≥40 chars). Tight enough to reject log noise,
    # loose enough to accept any libp2p-emitted peer id.
    peer_re = re.compile(r'PeerId\("([1-9A-HJ-NP-Za-km-z]{40,})"\)')
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
        f'Expected log shape: `Peer id: PeerId("<base58btc-multihash>")`.\n'
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
    #
    # boot-node: still built locally from the bundled Dockerfile
    # for now (moving to a pre-built GHCR image is tracked
    # separately). Skip the build if the operator passed an
    # explicit image override.
    #
    # alpine: stock base for the nat-gateway role + the per-client
    # default-route injection sidecars + the diagnostic-dump
    # sidecars. We MUST pull it explicitly: docker-py's
    # `containers.create()` (which `_spawn_nat_gateway` uses, so
    # it can attach the LAN bridge BEFORE start) does NOT auto-pull
    # on a missing image, unlike `containers.run()`. A missing
    # alpine:3.19 on a fresh CI runner would surface as
    # `404 Client Error ... No such image: alpine:3.19` from the
    # daemon mid-create. Explicit pull at setup time gives a
    # clean diagnostic if the registry is unreachable.
    boot_node_image = boot_node_image_override or BOOT_NODE_IMAGE_TAG
    if not boot_node_image_override:
        _build_image_if_missing(
            client,
            "boot-node",
            BOOT_NODE_IMAGE_TAG,
            build_args={"BOOT_NODE_VERSION": DEFAULT_BOOT_NODE_VERSION},
        )
    _pull_image_if_missing(client, GATEWAY_BASE_IMAGE)

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
    #
    # Step 3+: bring up the LAN network, boot-node, gateway, and
    # resolve identifiers. Wrap from LAN-network creation onward in
    # try/except so a failure at ANY of these steps cleans up
    # whatever earlier steps succeeded (including the LAN network
    # itself if its creation succeeded but a later step raised).
    # public_network creation is OUTSIDE the try: if it fails, no
    # resources to clean up; if it succeeds and a later step
    # raises, the except path will tear it down too.
    lan_network = None
    boot_node = None
    gateway = None
    try:
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
        boot_node = _spawn_boot_node(
            client, boot_node_image, public_network, workflow_name
        )

        # Step 4: NAT gateway. After this returns, the LAN network
        # has outbound routing to the public network. The gateway's
        # LAN-side IP is pre-assigned (not read back) so we know it
        # without waiting on Docker's async IPAM-population path.
        gateway, gateway_lan_ip = _spawn_nat_gateway(
            client,
            public_network,
            lan_network,
            nat_mode,
            workflow_name,
        )

        # Step 5: resolve boot-node's IP + peer id. Clients need
        # both to build their bootstrap multiaddr.
        boot_node_public_ip = _resolve_container_ip(boot_node, public_network.name)
        boot_node_peer_id = _resolve_boot_node_peer_id(boot_node)
    except Exception as setup_err:
        console.print(
            f"[red]✗ NAT topology setup failed: {setup_err}; "
            f"cleaning up partial resources...[/red]"
        )
        # Best-effort cleanup of whatever made it up. Each step is
        # tolerant of `None` (component never created) and exceptions
        # (the cleanup itself failing — we still want to try the
        # others). The exception is re-raised at the end so the
        # caller sees the ORIGINAL setup failure, not whatever
        # happened during cleanup.
        for component in (gateway, boot_node):
            if component is None:
                continue
            try:
                component.stop(timeout=5)
            except Exception as e:
                console.print(
                    f"[yellow]  cleanup: failed to stop {component.name}: {e}[/yellow]"
                )
            try:
                component.remove(force=True)
            except Exception as e:
                console.print(
                    f"[yellow]  cleanup: failed to remove {component.name}: {e}[/yellow]"
                )
        for net in (lan_network, public_network):
            # Skip networks that never made it past create (None) —
            # without this, `net.reload()` on the LAN side would
            # AttributeError if `_ensure_network` raised before
            # returning a network handle.
            if net is None:
                continue
            try:
                net.reload()
                attached = (net.attrs or {}).get("Containers", {}) or {}
                for container_id in list(attached.keys()):
                    try:
                        net.disconnect(container_id, force=True)
                    except Exception:
                        pass
                net.remove()
            except Exception as e:
                console.print(
                    f"[yellow]  cleanup: failed to remove {net.name}: {e}[/yellow]"
                )
        raise

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

    Diagnostic-only — NOT the readiness gate
    ----------------------------------------

    This is a WEAKER signal than `wait_for_relay_reservations`:
    it just asserts "topology infrastructure works" (route injection
    + MASQUERADE + per-iface forwarding + bridge plumbing all
    landed correctly), not "the relay path is alive end-to-end."

    Use cases:
      * Debugging a workflow failure interactively, to confirm the
        Docker plumbing is fine and the timeout is happening
        further up the stack.
      * Programmatic checks where the caller already accepts that
        the relay-reservation flow is broken in the merod build
        under test, and just wants the L3 reachability assertion.

    The executor's actual readiness gate uses the stricter
    `wait_for_relay_reservations` — without it, the smoke test
    would silently pass while the very bug it was built to expose
    (the merod-side gap that prevents the
    autonat-failure → relay-reservation trigger from firing when
    no external address is advertised) goes undetected.

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

    THIS is the executor's readiness gate. It currently times out
    on every NAT-topology workflow run because merod's relay-
    reservation flow is gated behind the `advertise_address` branch
    in `crates/network/src/discovery.rs`: a NAT'd merod with no
    advertised external address never opens the HOP stream to its
    relay candidate, so no reservation is ever requested. The
    topology infrastructure is fully functional — clients reach
    the boot-node, exchange Identify, autonat correctly reports
    NAT'd. The strict gate here ensures the workflow surfaces that
    merod-side gap rather than silently passing on a weaker
    assertion. Once the merod fix ships, every run of this workflow
    goes green with no code change here.

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
                GATEWAY_BASE_IMAGE,
                entrypoint=["sh", "-c"],
                # `apk add` first because stock alpine's busybox
                # `ip` differs subtly from iproute2's; we want
                # iproute2 to keep the diagnostic output stable.
                command=[
                    "apk add --no-cache iproute2 > /dev/null; "
                    "ip route show; ip addr show"
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
            console.print(f"[yellow]Client {name} netns state:\n{decoded}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Failed to dump {name} netns state: {e}[/yellow]")
    # End-to-end connectivity probes. From INSIDE the gateway, can
    # we reach the boot-node? (If not, gateway-side routing or
    # bridge isolation is broken; nothing the client does can
    # rescue this.) From INSIDE a client (via shared-netns sidecar),
    # can we ping the gateway? Reach the boot-node?
    #
    # All IP / port values entering the shell command below come from
    # Docker's IPAM and merobox module constants — none of them are
    # workflow-configured today. shlex.quote them anyway so a future
    # refactor that lets the operator override e.g. the boot-node IP
    # doesn't introduce a shell-injection path through the diagnostic
    # dump.
    safe_bn_ip = shlex.quote(state.boot_node_public_ip)
    safe_bn_port = shlex.quote(str(BOOT_NODE_PORT))
    safe_gw_ip = shlex.quote(state.gateway_lan_ip)
    try:
        ec, out = state.nat_gateway_container.exec_run(
            [
                "sh",
                "-c",
                (
                    f"echo '--- gw -> boot-node ({safe_bn_ip}) ---';"
                    f" (timeout 3 nc -zv {safe_bn_ip} {safe_bn_port}"
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
                GATEWAY_BASE_IMAGE,
                entrypoint=["sh", "-c"],
                # ping and nc are in busybox, no apk needed for the
                # probe itself.
                command=[
                    (
                        f"echo '--- client -> gateway ({safe_gw_ip}) ---';"
                        f" (timeout 3 ping -c 1 -W 2 {safe_gw_ip} 2>&1"
                        "   | head -5 || true);"
                        f" echo '--- client -> boot-node ({safe_bn_ip}) ---';"
                        f" (timeout 3 nc -zv {safe_bn_ip} {safe_bn_port}"
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

    Ordering matters: networks must be EMPTY before they can be
    removed. The original sequence (stop+remove containers, then
    remove networks) relied on Docker auto-cleaning network
    membership when a container is removed — and that DOES happen
    in the normal case, but a client container that the workflow
    executor failed to clean up (any leftover from
    `_start_nodes_nat_topology` returning False) would still be
    attached to the LAN network and silently block the network's
    `remove()`. We now disconnect every container Docker still
    reports as attached BEFORE attempting the network removal.
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
                # Reload to get the current set of attached
                # containers — anything still here is a leftover
                # the workflow executor couldn't clean up (e.g.,
                # client containers from a failed run that returned
                # False before reaching stop_all_nodes). Disconnect
                # them with force=True so `net.remove()` below has
                # a clean slate.
                net.reload()
                stragglers = (net.attrs or {}).get("Containers", {}) or {}
                for container_id in list(stragglers.keys()):
                    try:
                        net.disconnect(container_id, force=True)
                    except Exception as e:
                        console.print(
                            f"[yellow]  failed to disconnect {container_id[:12]} "
                            f"from {net.name}: {e}[/yellow]"
                        )
                net.remove()
            except Exception as e:
                console.print(
                    f"[yellow]Failed to remove network {net.name}: {e}[/yellow]"
                )
