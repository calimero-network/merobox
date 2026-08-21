"""Topology primitives for merobox.

Default merobox cluster mode puts every node on a single Docker bridge
and wires each node's `bootstrap.nodes` with all of its siblings, mDNS
left on. That is convenient and unlike any deployment: a shipped node is
told about one or two bootstrap peers, has to ask one of them who else
exists, and has no multicast path to peers on other hosts.

A `topology:` block in the workflow YAML replaces that wiring. Two
variants:

* ``nat`` (``nat.py``) — a boot-node on a public bridge, a NAT gateway,
  and N clients on an ``--internal`` LAN bridge that can only reach each
  other through the boot-node's relay circuit. The only shape that
  exercises relay-reservation recovery (calimero-network/core#2446) and
  DCUtR hole-punching.
* ``bootstrap`` (``bootstrap.py``) — one plain bridge, one boot-node,
  mDNS forced off, and a single bootstrap address per client. The same
  ask-a-peer discovery path as ``nat``, without the relay, the gateway
  or the route injection, so a bootstrap/kad/rendezvous regression fails
  here and means only that.
"""

from merobox.topology.bootstrap import (
    setup_bootstrap_topology,
    teardown_bootstrap_topology,
)
from merobox.topology.nat import setup_nat_topology, teardown_nat_topology

__all__ = [
    "setup_bootstrap_topology",
    "setup_nat_topology",
    "teardown_bootstrap_topology",
    "teardown_nat_topology",
]
