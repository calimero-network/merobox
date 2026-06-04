"""Disconnect / connect step executors for the container's Docker network.

Used to simulate network partitions in tests. The container keeps running
and retains all in-memory state, but cannot reach (or be reached by) other
containers on its bridge network. Re-attach with connect_node.

Auto-targets the right network for the workflow: `merobox-cluster` for
multi-node (count >= 2) runs, `calimero_web` for auth-service workflows,
`bridge` for legacy / single-node setups. Override with explicit `network:`
if you need to disconnect from something specific (or from a network the
container is attached to alongside others).

Caveat: this is not a perfect partition. Containers also bind to the host's
exposed ports, so any peer connecting via host gateway would still see them.
Inside merobox's default 1-host setup this is fine — all inter-node libp2p
traffic flows over the bridge.

Reconnect typically needs a few seconds for libp2p mesh reformation
(heartbeats + peer discovery), so workflows should pair this with an
explicit `wait_for_sync` or short `wait` before asserting state propagation.
"""

import ipaddress
import subprocess
from typing import Any

import docker.errors

from merobox.commands.bootstrap.steps._docker_utils import (
    detect_node_network,
    get_docker_client,
    is_binary_mode,
    partition_network_key,
    reinject_nat_default_route,
    safe_console_error,
    warn_if_mdns_enabled,
)
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class DisconnectNodeStep(BaseStep):
    """Disconnect a node container from its Docker network.

    Auto-detects the network from the container's NetworkSettings when
    `network:` is not set; falls back to merobox-cluster / bridge per
    detect_node_network's priority.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_string_field("network", required=False)

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        if is_binary_mode(self.manager):
            console.print(
                "[yellow]Skipping disconnect_node: --no-docker mode has no "
                "Docker network to disconnect from[/yellow]"
            )
            return True

        node_name = self._resolve_dynamic_value(
            self.config["node"], workflow_results, dynamic_values
        )

        client = get_docker_client(self.manager)
        try:
            container = client.containers.get(node_name)
        except docker.errors.NotFound:
            console.print(f"[red]✗ Container '{node_name}' not found[/red]")
            return False

        explicit_network = self.config.get("network")
        if explicit_network is not None:
            network_name = self._resolve_dynamic_value(
                explicit_network, workflow_results, dynamic_values
            )
        else:
            network_name = detect_node_network(container)

        warn_if_mdns_enabled(container, node_name)

        console.print(
            f"[yellow]Disconnecting {node_name} from network {network_name}...[/yellow]"
        )

        try:
            network = client.networks.get(network_name)
            network.disconnect(container)
        except Exception as exc:
            safe_console_error(
                "✗ Failed to disconnect {node} from {network}: {err}",
                node=node_name,
                network=network_name,
                err=exc,
            )
            return False

        # Record so a downstream connect_node reattaches to the SAME network,
        # which matters when the workflow path used run_node directly
        # (no merobox-cluster created) — auto-detection then has no signal.
        dynamic_values[partition_network_key(node_name)] = network_name

        console.print(f"[green]✓ Disconnected {node_name} from {network_name}[/green]")
        return True


class ConnectNodeStep(BaseStep):
    """Connect a node container back to a Docker network.

    Network resolution order when `network:` is not set:
      1. The network recorded by a preceding disconnect_node call (read
         from dynamic_values). This is the common case and the only
         signal that survives a full disconnect.
      2. detect_node_network on the container — if it's only partially
         disconnected, picks an attached candidate; otherwise falls back
         to Docker's default `bridge`.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_string_field("network", required=False)

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        if is_binary_mode(self.manager):
            console.print(
                "[yellow]Skipping connect_node: --no-docker mode has no "
                "Docker network to connect to[/yellow]"
            )
            return True

        node_name = self._resolve_dynamic_value(
            self.config["node"], workflow_results, dynamic_values
        )

        client = get_docker_client(self.manager)
        try:
            container = client.containers.get(node_name)
        except docker.errors.NotFound:
            console.print(f"[red]✗ Container '{node_name}' not found[/red]")
            return False

        explicit_network = self.config.get("network")
        if explicit_network is not None:
            network_name = self._resolve_dynamic_value(
                explicit_network, workflow_results, dynamic_values
            )
        else:
            # Prefer the network recorded by a prior disconnect_node — this is
            # the only signal that survives a full container disconnect, since
            # the container's NetworkSettings is empty by then.
            recorded = dynamic_values.get(partition_network_key(node_name))
            network_name = recorded or detect_node_network(container)

        console.print(
            f"[yellow]Connecting {node_name} to network {network_name}...[/yellow]"
        )

        try:
            network = client.networks.get(network_name)
            network.connect(container)
        except Exception as exc:
            safe_console_error(
                "✗ Failed to connect {node} to {network}: {err}",
                node=node_name,
                network=network_name,
                err=exc,
            )
            return False

        # Clean up the recorded partition network so a fresh disconnect
        # cycle doesn't pick up stale state.
        dynamic_values.pop(partition_network_key(node_name), None)

        console.print(f"[green]✓ Connected {node_name} to {network_name}[/green]")

        # Reconnecting via `network.connect` rebuilds the container's network
        # attachment, which makes Docker re-point the default route at its
        # own bridge gateway — wiping the NAT-gateway override the topology
        # injected. Without re-applying it, the just-healed client routes
        # around the NAT gateway, its noise handshakes to the boot-node time
        # out, and the partition never actually "heals" for libp2p. No-op
        # outside a NAT topology.
        reinject_nat_default_route(self.manager, node_name, context="post-reconnect")
        return True


# ── Surgical peer-pair partition (calimero-network/merobox#278) ──────────────
#
# disconnect_node detaches a container from its bridge, which also tears down
# its published-port DNAT — so the partitioned node can't be `call`ed. That is
# fine when a test only reads a partitioned node *after* reconnecting, but it
# can't drive a node that must execute a call WHILE partitioned (e.g. two nodes
# concurrently rotating a SharedStorage writer set).
#
# partition_peers instead drops only *container-to-container* (libp2p) traffic
# between specific peers, via symmetric DROP rules in the host's DOCKER-USER
# iptables chain matched on the containers' bridge IPs. DOCKER-USER is consulted
# for forwarded traffic, so the peers' libp2p packets are dropped both ways;
# host→container traffic for the published RPC port arrives via DNAT with a
# non-container source IP, so it does not match these rules and RPC stays up.
# The container keeps its interface, IP, and routing table intact.
#
# Requires a Linux host with iptables + passwordless sudo (GitHub-hosted runners
# qualify); it is incompatible with Docker Desktop's VM, so — like the other
# Docker-network fault steps — it is a CI / Linux primitive.

_DOCKER_USER_CHAIN = "DOCKER-USER"
# Bound on a wedged sudo/iptables call so the workflow can't hang.
_IPTABLES_TIMEOUT_S = 30
# Upper bound on delete-until-gone, so a stuck rule can't loop forever.
_MAX_DUP_DELETES = 16
# iptables stderr substrings that mean "the rule/chain isn't there" — i.e. the
# benign already-healed case, as opposed to a real failure (sudo denied, etc.).
_BENIGN_DELETE_ERRORS = ("does not exist", "no chain/target/match")


def _libp2p_ip(container: Any) -> str | None:
    """The container's IPv4 on the network it uses for inter-node libp2p.

    Resolves the IP on the network ``detect_node_network`` would partition
    (merobox-cluster / calimero_web / the single attached bridge) rather than
    whichever network Docker happened to list first — so on a multi-attached
    (e.g. auth-mode) node the DROP rules target the subnet libp2p actually flows
    on. Falls back to any attached IPv4 if the chosen network has none, and
    validates the result. Returns None if no well-formed IPv4 is found.
    """
    # detect_node_network reloads the container and applies the priority rule.
    network_name = detect_node_network(container)
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {}) or {}
    ip = (networks.get(network_name) or {}).get("IPAddress") or ""
    if not ip:
        for net in networks.values():
            if net.get("IPAddress"):
                ip = net["IPAddress"]
                break
    if not ip:
        return None
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return None
    return ip


def _iptables(action: str, src: str, dst: str) -> subprocess.CompletedProcess:
    """Run `iptables <action> DOCKER-USER -s src -d dst -j DROP` on the host.

    `sudo -n` fails fast instead of prompting; `timeout` bounds a wedged
    sudo/iptables; stdout and stderr are kept SEPARATE so a sudo auth error is
    distinguishable from a "rule not found". A timeout surfaces as a synthetic
    non-zero result rather than raising, so callers handle it uniformly.
    """
    argv = [
        "sudo",
        "-n",
        "iptables",
        action,
        _DOCKER_USER_CHAIN,
        "-s",
        src,
        "-d",
        dst,
        "-j",
        "DROP",
    ]
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_IPTABLES_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv,
            returncode=124,
            stdout="",
            stderr=f"timed out after {_IPTABLES_TIMEOUT_S}s",
        )


def _iptables_err(result: subprocess.CompletedProcess) -> str:
    """Combined, trimmed stdout+stderr from an iptables run (for diagnostics)."""
    parts = [p.strip() for p in (result.stdout, result.stderr) if p and p.strip()]
    return " ".join(parts)


class _PeerPartitionBase(BaseStep):
    """Shared field validation + IP resolution for partition_peers / heal_peers."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "peers"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_list_field(
            "peers", required=True, allow_empty=False, element_type=str
        )

    def _resolve_ips(self, workflow_results, dynamic_values):
        """Resolve (node_name, node_ip, [(peer_name, peer_ip), ...]).

        Returns None (after a diagnostic naming the step) if a container is
        missing or has no usable libp2p IPv4.
        """
        client = get_docker_client(self.manager)
        step_type = self.config.get("type", "partition_peers")

        def lookup(raw_name):
            name = self._resolve_dynamic_value(
                raw_name, workflow_results, dynamic_values
            )
            try:
                container = client.containers.get(name)
            except docker.errors.NotFound:
                console.print(f"[red]✗ {step_type}: container '{name}' not found[/red]")
                return None, None
            ip = _libp2p_ip(container)
            if not ip:
                console.print(
                    f"[red]✗ {step_type}: could not resolve a libp2p IP "
                    f"for {name}[/red]"
                )
                return name, None
            return name, ip

        node_name, node_ip = lookup(self.config["node"])
        if node_ip is None:
            return None

        peers = []
        for raw_peer in self.config["peers"]:
            peer_name, peer_ip = lookup(raw_peer)
            if peer_ip is None:
                return None
            peers.append((peer_name, peer_ip))
        return node_name, node_ip, peers


class PartitionPeersStep(_PeerPartitionBase):
    """Cut libp2p between ``node`` and each of ``peers`` while keeping RPC up.

    Inserts symmetric DROP rules into the host's DOCKER-USER chain on the
    containers' libp2p IPs. **Idempotent**: a rule already present (checked with
    ``iptables -C``) is not re-inserted, so re-runs don't accumulate duplicates.
    On a partial failure the rules inserted by THIS call are rolled back before
    returning, so the step never leaves a half-applied partition. Heal with
    heal_peers (same args).
    """

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        if is_binary_mode(self.manager):
            console.print(
                "[yellow]Skipping partition_peers: --no-docker mode has no "
                "container network to partition[/yellow]"
            )
            return True

        resolved = self._resolve_ips(workflow_results, dynamic_values)
        if resolved is None:
            return False
        node_name, node_ip, peers = resolved

        inserted: list[tuple[str, str]] = []

        def rollback():
            for s, d in inserted:
                _iptables("-D", s, d)  # best-effort

        for peer_name, peer_ip in peers:
            console.print(
                f"[yellow]Partitioning {node_name} ({node_ip}) <-x-> "
                f"{peer_name} ({peer_ip}) — libp2p only, RPC stays up[/yellow]"
            )
            for src, dst in ((node_ip, peer_ip), (peer_ip, node_ip)):
                # Idempotent: skip if the DROP rule is already present.
                if _iptables("-C", src, dst).returncode == 0:
                    continue
                result = _iptables("-I", src, dst)
                if result.returncode != 0:
                    safe_console_error(
                        "✗ partition_peers: iptables insert failed "
                        "({src} -> {dst}): {out}",
                        src=src,
                        dst=dst,
                        out=_iptables_err(result),
                    )
                    rollback()
                    return False
                inserted.append((src, dst))

        console.print(
            f"[green]✓ Partitioned {node_name} from {len(peers)} peer(s) "
            f"(libp2p dropped; RPC reachable)[/green]"
        )
        return True


class HealPeersStep(_PeerPartitionBase):
    """Remove the DROP rules a prior partition_peers added (same args).

    For each direction the rule is deleted until iptables reports it is gone
    (clearing any duplicates an interrupted/re-run workflow may have left). A
    "rule does not exist" is the benign already-healed case; any OTHER iptables
    failure (sudo denied, missing binary) fails the step, so a partition is
    never silently reported as healed.
    """

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        if is_binary_mode(self.manager):
            console.print(
                "[yellow]Skipping heal_peers: --no-docker mode has no "
                "container network to heal[/yellow]"
            )
            return True

        resolved = self._resolve_ips(workflow_results, dynamic_values)
        if resolved is None:
            return False
        node_name, node_ip, peers = resolved

        ok = True
        for peer_name, peer_ip in peers:
            console.print(f"[yellow]Healing {node_name} <--> {peer_name}[/yellow]")
            for src, dst in ((node_ip, peer_ip), (peer_ip, node_ip)):
                for _ in range(_MAX_DUP_DELETES):
                    result = _iptables("-D", src, dst)
                    if result.returncode == 0:
                        continue  # removed one; retry in case of duplicates
                    err = _iptables_err(result).lower()
                    if any(benign in err for benign in _BENIGN_DELETE_ERRORS):
                        break  # nothing (more) to remove — already healed
                    safe_console_error(
                        "✗ heal_peers: iptables delete failed "
                        "({src} -> {dst}): {out}",
                        src=src,
                        dst=dst,
                        out=_iptables_err(result),
                    )
                    ok = False
                    break

        if ok:
            console.print(
                f"[green]✓ Healed {node_name} from {len(peers)} peer(s)[/green]"
            )
        return ok
