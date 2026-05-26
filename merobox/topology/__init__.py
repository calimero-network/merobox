"""Multi-bridge / NAT topology primitives for merobox.

Default merobox cluster mode puts every node on a single Docker
bridge so siblings reach each other directly via `/ip4/<sibling>/...`.
That's the right shape for the bulk of e2e tests; the only thing it
can't exercise is the relay-reservation-recovery code path in
calimero-network/core#2446, which fires only when nodes physically
cannot dial each other and depend on a relay.

This package adds the NAT topology — `topology: { type: nat }` in
the workflow YAML — which spawns a dedicated boot-node + a NAT
gateway + N client merods on an `--internal` LAN bridge. Clients
must register relay reservations with the boot-node to be
reachable. See ``nat.py`` for the orchestration details.
"""

from merobox.topology.nat import setup_nat_topology, teardown_nat_topology

__all__ = ["setup_nat_topology", "teardown_nat_topology"]
