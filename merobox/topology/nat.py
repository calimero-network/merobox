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
import shutil
import subprocess
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
    # Host iptables rules installed by `setup_nat_topology` to force
    # all inter-client traffic through the boot-node relay. Each
    # entry is the exact arg list passed to `iptables ...` (the `-I`
    # add form); teardown turns each into its matching `-D` remove
    # by swapping the verb. Tracking the exact rule lets us tear
    # down precisely what we installed even if Docker reassigns the
    # bridge interface name on a parallel run.
    host_iptables_rules: list[list[str]] = field(default_factory=list)


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
# Host iptables isolation
# ---------------------------------------------------------------------------
#
# To exercise the relay-reservation code path (the whole point of NAT
# topology), client containers must appear UNREACHABLE from the boot-
# node's autonat dial-back probe. The Docker `--internal=True` bridge
# would do that natively but breaks IP allocation on the gateway's
# second-network attachment (see `_ensure_network`'s rationale).
#
# Workaround: leave both bridges non-internal so Docker's IPAM works
# normally, then install a host-side iptables rule that DROPs traffic
# from the public bridge to the LAN bridge. Effect:
#
#   * Clients on LAN can still reach boot-node on public via Docker's
#     default LAN→public ACCEPT (route, MASQUERADE on the way out
#     through the host's NAT). The relay-reservation handshake
#     succeeds.
#   * Boot-node CAN'T direct-dial clients on LAN — the host's DOCKER-
#     USER iptables chain DROPs the packet before it reaches the LAN
#     bridge. libp2p autonat decides clients are unreachable and the
#     reservation flow fires.
#
# Implementation: shells out to `iptables` via `subprocess`. CI runners
# (GitHub Actions ubuntu-latest) have passwordless sudo for the runner
# user; locally, `merobox bootstrap run` against a NAT-topology
# workflow needs `sudo` or root. The error message below makes this
# explicit if the rule install fails with permission denied.


def _docker_bridge_iface_name(network: docker.models.networks.Network) -> str:
    """Map a Docker network handle to the host-side bridge interface
    name.

    Docker installs bridges named ``br-<first-12-chars-of-network-id>``
    on the host. The network ID is available on the model directly.
    Trims to 12 chars because ``ip link`` interface names are bounded
    at 15 chars and Docker reserves a 3-char prefix.
    """
    return f"br-{network.id[:12]}"


def _run_iptables(rule_argv: list[str]) -> tuple[int, str]:
    """Run a single ``iptables`` command, surfacing exit + stderr.

    Returns ``(exit_code, combined_stderr_stdout)``. The caller decides
    whether to treat non-zero as fatal (rule install) or best-effort
    (rule remove during teardown). A missing binary or permission
    denied is signaled by exit codes >= 100 with a helpful message in
    the second tuple element.
    """
    iptables = shutil.which("iptables")
    if iptables is None:
        return (
            127,
            "iptables not found on PATH — NAT topology requires "
            "iptables on the host kernel (Linux only; not available "
            "via Docker Desktop on macOS/Windows because their LinuxKit "
            "VM doesn't expose iptables to the host).",
        )
    argv = [iptables, *rule_argv]
    # We try non-interactive sudo first (CI runner passwordless sudo,
    # plus local root just runs through). If that fails because sudo
    # isn't on PATH or denies, fall back to running iptables directly
    # (works if the user already has CAP_NET_ADMIN).
    sudo = shutil.which("sudo")
    if sudo is not None:
        try:
            r = subprocess.run(
                [sudo, "-n", *argv],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if r.returncode == 0:
                return (0, "")
            # Non-zero from sudo could be permission denied or a real
            # iptables failure. Re-try without sudo to differentiate;
            # if that ALSO fails, the original sudo output is more
            # diagnostic.
            sudo_output = (r.stderr or r.stdout or "").strip()
        except Exception as e:
            sudo_output = f"sudo subprocess raised: {e}"
    else:
        sudo_output = ""
    try:
        r = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if r.returncode == 0:
            return (0, "")
        bare_output = (r.stderr or r.stdout or "").strip()
        # Surface both attempts so the operator sees the full picture.
        joined = f"sudo iptables: {sudo_output}\n" f"iptables (no sudo): {bare_output}"
        return (r.returncode, joined)
    except Exception as e:
        return (128, f"iptables subprocess raised: {e}")


def _install_host_iptables_isolation(
    state: NatTopologyState,
) -> None:
    """Install the host-side iptables DROP rule that makes clients
    unreachable from the public bridge.

    The rule lives in ``DOCKER-USER`` so it takes precedence over
    Docker's own bridge-routing ACCEPT rules. Inserted at position 1
    so it fires before any later rules. The exact `iptables` arg list
    is captured in `state.host_iptables_rules` so teardown removes
    precisely what was installed.

    Raises ``RuntimeError`` if the install fails — the workflow can't
    achieve its stated relay-only semantics without this, and silently
    proceeding would produce a test that looks like it's exercising
    the relay path while clients direct-dial each other behind the
    operator's back.
    """
    public_br = _docker_bridge_iface_name(state.public_network)
    lan_br = _docker_bridge_iface_name(state.lan_network)
    rule = [
        "-I",
        "DOCKER-USER",
        "1",
        "-i",
        public_br,
        "-o",
        lan_br,
        "-j",
        "DROP",
        "-m",
        "comment",
        "--comment",
        # Embed the workflow slug so a future operator running
        # `iptables -L DOCKER-USER` can see what installed the rule
        # and what to remove if cleanup ever leaks.
        f"merobox-nat:{state.lan_network.name}",
    ]
    console.print(
        f"[yellow]Installing host iptables rule: DROP {public_br} -> {lan_br}"
        f"[/yellow]"
    )
    exit_code, output = _run_iptables(rule)
    if exit_code != 0:
        raise RuntimeError(
            "Failed to install host iptables isolation rule (the NAT "
            "topology relies on this rule to make clients unreachable "
            "from the boot-node's autonat probe). Common causes:\n"
            "  * Running outside Linux (Docker Desktop's LinuxKit VM "
            "    doesn't expose iptables).\n"
            "  * No passwordless sudo + no CAP_NET_ADMIN on the user.\n"
            "  * `iptables` binary not installed on the host PATH.\n"
            f"Exit code: {exit_code}. Subprocess output:\n{output}"
        )
    # Track the EXACT argv we installed so teardown removes the same
    # entry (in case parallel workflows have similar rules).
    state.host_iptables_rules.append(rule)
    console.print("[green]✓ Host iptables isolation rule installed[/green]")


def _remove_host_iptables_isolation(state: NatTopologyState) -> None:
    """Remove every iptables rule the topology installed.

    Best-effort: errors are logged but don't propagate. A leaked rule
    is recoverable (operator can `iptables -F DOCKER-USER` or grep for
    `merobox-nat:` in the chain), but failing teardown loudly would
    mask the actual workflow result.
    """
    while state.host_iptables_rules:
        rule = state.host_iptables_rules.pop()
        # Swap `-I` + position with `-D`. iptables `-D` doesn't take
        # a position; we drop the `1` argument too.
        delete_rule = ["-D" if a == "-I" else a for a in rule if a != "1"]
        exit_code, output = _run_iptables(delete_rule)
        if exit_code != 0:
            console.print(
                f"[yellow]Best-effort iptables cleanup failed "
                f"(exit {exit_code}): {output}[/yellow]"
            )
        else:
            console.print("[green]✓ Removed host iptables isolation rule[/green]")


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
    # `connect()` is synchronous at the Docker API level but the
    # daemon's network-state update propagates asynchronously to
    # `container.attrs`; downstream callers that need the LAN IP
    # poll via `_resolve_container_ip` (which now retries up to
    # 30s) to absorb the race.
    lan_network.connect(container)
    # Reload immediately so the second-network entry is visible to
    # the next code path that introspects `container.attrs`. Cheap.
    container.reload()
    return container


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
    # Falling back to `internal=False` is a deliberate trade-off:
    #
    # * Clients CAN reach boot-node via the gateway (good — the
    #   relay-reservation flow needs outbound).
    # * Clients are technically reachable directly from the public
    #   bridge via Docker's host-level routing too (bad — autonat's
    #   probe may succeed and the relay path stays dead code).
    #
    # The first-iteration goal is "primitive works end-to-end and
    # the workflow infrastructure is reusable"; strict isolation is
    # documented as out-of-scope for this iteration in the spec doc.
    # A future PR can address by either (a) injecting host-level
    # iptables FORWARD rules that block public→LAN routing, or
    # (b) making clients listen only on their LAN-bridge interface
    # (so autonat dial-backs targeting their advertised address
    # bypass any direct route Docker happens to install).
    lan_network = _ensure_network(client, f"{workflow_name}-lan", internal=False)

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

    state = NatTopologyState(
        public_network=public_network,
        lan_network=lan_network,
        boot_node_container=boot_node,
        boot_node_peer_id=boot_node_peer_id,
        boot_node_public_ip=boot_node_public_ip,
        nat_gateway_container=gateway,
    )

    # Install the host iptables DROP rule LAST, after the bridges
    # have IDs we can reference. This is what actually forces the
    # relay path — without it, both bridges are non-internal and
    # Docker's default ACCEPT lets the boot-node direct-dial clients.
    # See `_install_host_iptables_isolation` for the full rationale.
    _install_host_iptables_isolation(state)
    return state


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
    # Remove iptables rules FIRST, before any containers / networks
    # go away. If the bridge interface disappears mid-teardown, the
    # `-D` removal would error out and leak the rule. Doing it first
    # also means a partial-teardown failure later still leaves the
    # iptables state clean.
    _remove_host_iptables_isolation(state)

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
