"""Single-bridge boot-node topology: `topology: { type: bootstrap }`.

Default cluster mode wires every node's `bootstrap.nodes` with the
addresses of all its siblings, and leaves mDNS on. Both of those are
harness conveniences a real deployment does not have. A shipped node is
told about one or two bootstrap peers and has to ASK one of them who
else exists — rendezvous register/discover — and it never has a
multicast path to its peers, which live on other hosts.

The NAT topology (`nat.py`) does exercise that discovery path, because
its clients can only reach each other through the boot-node. But it does
so while simultaneously testing relay reservations, DCUtR hole-punching
and iptables MASQUERADE, so a discovery regression there presents as a
NAT failure and gets triaged as one. This topology is the same discovery
shape with those confounds removed: one plain bridge, one boot-node, no
gateway, no relay pressure, no route injection.

What each node gets:

  * a single non-internal bridge shared with the boot-node and every
    sibling, so any pair COULD dial directly once they know an address;
  * exactly one entry in `bootstrap.nodes` — the boot-node — so the only
    way to learn a sibling's address is to ask;
  * `discovery.mdns = false`, forced after any workflow override, so
    multicast cannot paper over a discovery failure.

That combination makes the interesting failure legible: if bootstrap
dialling, identify, kad or rendezvous regresses, nodes never learn about
each other and the scenario fails on its own convergence assertions.
Nothing else in the harness can carry the traffic.

Deliberately NOT here: sibling bootstrap wiring (that is cluster mode,
and it hands out the roster this topology exists to withhold) and the
NAT gateway (a client that cannot dial its sibling directly is a relay
test, which `nat.py` already owns).
"""

from dataclasses import dataclass, field

import docker

from merobox.commands.utils import console

# Reused from the NAT topology rather than duplicated: these are generic
# Docker/boot-node mechanics with hard-won details in them — the IPAM
# population race in `_resolve_container_ip`, the peer-id scan that has
# to tolerate the boot-node's log format, the create-vs-run auto-pull
# asymmetry. A third topology variant is the point at which they should
# move to a shared module; two does not justify the churn.
from merobox.topology.nat import (
    BOOT_NODE_IMAGE_TAG,
    BOOT_NODE_PORT,
    _ensure_network,
    _pull_image_if_missing,
    _resolve_boot_node_peer_id,
    _resolve_container_ip,
    _spawn_boot_node,
)


@dataclass
class BootstrapTopologyState:
    """Handles to the bridge + boot-node this topology created.

    Mirrors `NatTopologyState` minus the LAN bridge and gateway, which
    is the whole difference between the two topologies. The executor
    holds it for the run and hands it back to
    :func:`teardown_bootstrap_topology`.
    """

    network: docker.models.networks.Network
    boot_node_container: docker.models.containers.Container
    boot_node_peer_id: str
    boot_node_ip: str
    # Client containers are spawned through the normal NodeManager path;
    # these are names rather than handles so a transient Docker
    # reconnect cannot invalidate them before teardown runs.
    client_names: list[str] = field(default_factory=list)


def setup_bootstrap_topology(
    client: docker.DockerClient,
    workflow_name: str,
    boot_node_image_override: str | None = None,
) -> BootstrapTopologyState:
    """Create the bridge and boot-node, and return the state to tear down.

    Raises on any failure after cleaning up whatever it had already
    created, so a caller that sees an exception owns nothing.
    """
    network_name = f"{workflow_name}-bootstrap"
    boot_node_image = boot_node_image_override or BOOT_NODE_IMAGE_TAG

    # Non-internal: the clients need the bridge to reach the boot-node,
    # and unlike the NAT topology we are not trying to prevent direct
    # sibling dials — only to withhold the addresses that would make
    # them possible without asking.
    network = _ensure_network(client, network_name, internal=False)

    boot_node = None
    try:
        _pull_image_if_missing(client, boot_node_image)
        boot_node = _spawn_boot_node(client, boot_node_image, network, workflow_name)
        boot_node_ip = _resolve_container_ip(boot_node, network.name)
        boot_node_peer_id = _resolve_boot_node_peer_id(boot_node)
    except Exception:
        # Undo in reverse order. Leaving a half-built topology behind
        # would make the next run of the same workflow collide with its
        # own leftovers, which reads as an unrelated Docker error.
        if boot_node is not None:
            try:
                boot_node.remove(force=True)
            except Exception:
                pass
        try:
            network.remove()
        except Exception:
            pass
        raise

    console.print(
        f"[green]✓ Bootstrap topology up: boot-node at "
        f"/ip4/{boot_node_ip}/tcp/{BOOT_NODE_PORT}/p2p/{boot_node_peer_id} "
        f"on {network.name}[/green]"
    )

    return BootstrapTopologyState(
        network=network,
        boot_node_container=boot_node,
        boot_node_peer_id=boot_node_peer_id,
        boot_node_ip=boot_node_ip,
    )


def bootstrap_multiaddrs(state: BootstrapTopologyState) -> list[str]:
    """The single bootstrap entry every client is given.

    TCP and QUIC forms of the same peer — one contact, two transports,
    which is what a deployed node's config looks like. Sibling addresses
    are deliberately absent: learning those is the thing under test.
    """
    return [
        f"/ip4/{state.boot_node_ip}/tcp/{BOOT_NODE_PORT}/p2p/{state.boot_node_peer_id}",
        f"/ip4/{state.boot_node_ip}/udp/{BOOT_NODE_PORT}/quic-v1/p2p/{state.boot_node_peer_id}",
    ]


def teardown_bootstrap_topology(
    client: docker.DockerClient,
    state: BootstrapTopologyState,
    remove_networks: bool = True,
) -> None:
    """Remove the boot-node and its bridge.

    Client containers are stopped by the normal node-management teardown;
    this owns only what :func:`setup_bootstrap_topology` created. Every
    step is best-effort and independently guarded — a teardown that
    aborts halfway leaves resources that break the next run, so a
    failure to remove one thing must not skip the rest.
    """
    try:
        state.boot_node_container.remove(force=True)
    except docker.errors.NotFound:
        pass
    except Exception as e:
        console.print(f"[yellow]Could not remove boot-node: {e}[/yellow]")

    if not remove_networks:
        return

    # The bridge only goes once its last endpoint is gone; clients are
    # torn down by the caller, so a still-attached client here means the
    # caller's ordering changed and the warning is the signal for that.
    try:
        state.network.remove()
    except docker.errors.NotFound:
        pass
    except Exception as e:
        console.print(
            f"[yellow]Could not remove network {state.network.name}: {e} "
            f"(a client container may still be attached)[/yellow]"
        )
