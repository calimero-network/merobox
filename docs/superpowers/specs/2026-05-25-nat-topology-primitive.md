# NAT-topology primitive

**Issue:** [merobox#251](https://github.com/calimero-network/merobox/issues/251) · **Status:** Implemented

## What it is

A new top-level `topology` block in the workflow YAML that switches
merobox out of the default single-bridge cluster mode and into a
multi-bridge topology with a dedicated boot-node, a NAT gateway, and
client nodes on an `--internal` LAN bridge.

```yaml
topology:
  type: nat
  nat_mode: cone           # or "symmetric"
  boot_node:
    image: …               # optional; defaults to a bundled built-on-first-use image

nodes:
  count: 2
  image: ghcr.io/calimero-network/merod:edge
  prefix: client
```

When `topology:` is present, the executor diverts to
`merobox.topology.nat.setup_nat_topology` and skips the normal
`_wire_cluster_bootstrap_peers` step entirely. Clients land on the
LAN bridge with `bootstrap.nodes` pointing at the boot-node's
public-bridge address; mDNS is forced off; sibling addresses are
NOT injected.

## When to use it

The default cluster mode (one Docker bridge, every node on it) is
the right shape for the bulk of e2e tests — fast startup, every
peer reachable directly, simple to reason about. The NAT topology
trades startup speed and topology simplicity for **exercising the
relay-reservation recovery code path** that landed in
[core#2446](https://github.com/calimero-network/core/pull/2446)
(and the follow-ups #2454 / #2459 / #2462).

That code path fires only when a node:

1. Has registered a circuit-relay reservation with a relay server.
2. Loses that reservation (relay restart, TCP timeout, sleep/wake,
   App Nap freeze, …).

For (1) to ever happen, the node must be **not directly reachable**
by its peers. On a shared Docker bridge, nodes always have direct
sibling routes, so they never register reservations, and the
recovery code stays as dead code in CI. The NAT topology removes
the direct route — clients on the LAN bridge cannot reach the
public bridge directly because Docker's `DOCKER-ISOLATION-STAGE-2`
chain DROPs cross-bridge forwarding through the host. The only
cross-bridge path is through the NAT gateway container (veths in
both bridges, forwards inside its own netns, MASQUERADEs source).
Clients are wired to use the gateway via a default-route override
injected post-startup. Boot-node dial-backs to the MASQUERADE'd
source port hit a no-listener wall, autonat marks clients NAT'd,
relay reservations become mandatory.

Use `topology: { type: nat }` when:

- The test specifically targets relay-recovery behaviour
  (#2446-class regressions).
- The test wants to verify that DCUtR hole-punching either does
  succeed (`cone` mode) or fails (`symmetric` mode).
- The test simulates the eight real-world triggers documented in
  the `project_relay_reservation_recovery_2026_05_22` memory:
  boot-node restart, laptop sleep/wake, Wi-Fi switch, VPN toggle,
  long-session renewal blip, relay quota exhaustion, symmetric NAT,
  App Nap freeze.

Stick with **default cluster mode** when:

- The test is about sync, governance, contexts, or any other
  application-layer concern that doesn't care how packets reach
  the wire.
- Fast iteration matters (NAT topology adds ~10-30s of startup
  for image build on first use + readiness gate wait).

## NAT modes

| Mode | iptables | DCUtR hole-punch | Use case |
|---|---|---|---|
| `cone` | `MASQUERADE` | Usually succeeds | Smoke test that the relay path *works* + supplements direct dial |
| `symmetric` | `MASQUERADE --random-fully` | Reliably fails | Strict test that the relay is the *only* path |

`--random-fully` randomises the outbound port per destination, so
STUN-style port prediction (the basis of DCUtR hole-punching) can
never resolve a consistent NAT'd address for the client. The
client is reachable exclusively via the relay circuit.

If the kernel/iptables stack doesn't support `--random-fully` the
gateway falls back to plain MASQUERADE with a warning — the test
will still run, but in cone-mode semantics. Detected by a probe
rule at gateway startup.

## What the primitive spawns

For workflow named `<wf>`:

1. **Public bridge** `<wf>-public` — regular Docker bridge with
   outside connectivity.
2. **LAN bridge** `<wf>-lan` — Docker bridge with a workflow-pinned
   subnet (so the NAT gateway can be attached with an explicit
   `ipv4_address`). The bridge itself is `--internal=False` for
   IPAM compatibility, but cross-bridge isolation is enforced by
   Docker's own `DOCKER-ISOLATION-STAGE-2` chain plus the
   client-side default-route override that forces all egress
   through the NAT gateway container.
3. **Boot-node container** `<wf>-boot-node` on the public bridge.
   Wraps the released `calimero-network/boot-node` binary
   (relay-server + rendezvous-server + Kademlia DHT). Default
   version `0.8.0`, override via `topology.boot_node.image`.
4. **NAT gateway container** `<wf>-nat-gateway` straddling both
   bridges. Runs `iptables MASQUERADE` so outbound LAN traffic is
   NAT'd onto the public bridge; needs `NET_ADMIN`.
5. **N client containers** on the LAN bridge, started via the
   normal `manager.run_node` path with `bootstrap_nodes` pointing
   at the boot-node's public-bridge multiaddr and `mdns=False`.

The boot-node + gateway images are built on first use from the
Dockerfiles under `merobox/topology/images/`. Builds are cached
locally as `merobox/boot-node:local` and `merobox/nat-gateway:local`,
so subsequent workflow runs skip the build entirely.

## Readiness gate

`setup_nat_topology` doesn't return until the boot-node has logged
its peer id and resolved a public-bridge IP. **After** clients are
spawned, the executor calls
`merobox.topology.nat.wait_for_relay_reservations` to poll each
client's logs for `ReservationReqAccepted` (the libp2p
`relay::client::Event` variant). The workflow's first step does
not run until every client has registered a reservation, so
downstream steps never race the relay handshake.

Timeout is 90s by default — sized for a slow CI runner doing first-
build of the boot-node image + cold `merod init` + libp2p
handshake. Override per-call by editing
`RELAY_READINESS_TIMEOUT_SECONDS` in `merobox/topology/nat.py`.

## What it doesn't do

- **IP rotation / network move.** That's
  [merobox#246](https://github.com/calimero-network/merobox/issues/246)
  / [core#2453](https://github.com/calimero-network/core/issues/2453).
- **IPv6 NAT.** IPv4 only. File a separate issue if needed.
- **Multi-boot-node topologies.** Single boot-node only. Fleet
  tests can stand up multiple workflows in parallel; merobox#251
  doesn't require a single workflow to coordinate multiple relay
  servers.
- **Production NAT exploration.** The bundled images are
  test-only. No plan to ship them outside CI.

## Sibling workflow

`workflow-examples/workflow-nat-topology-cone-example.yml` smoke-
tests the primitive end-to-end: spawns the topology, exchanges a
delta between two clients (which must go via the relay), asserts
that `/p2p-circuit/` appears in each client's log (confirming the
relay path was actually used). Symmetric-mode example is a one-
line change away; not yet shipped as its own file.

## Related context

- [core#2466](https://github.com/calimero-network/core/pull/2466) —
  sync-resilience workflows that ran into exactly the
  "direct-dial-bypasses-the-relay" problem this primitive solves.
  Those tests can be rewritten against NAT topology in a follow-up.
- [core#2467](https://github.com/calimero-network/core/issues/2467) —
  the core-side issue for actually writing relay-recovery
  regression workflows that depend on this primitive.
- [calimero-network/boot-node#37](https://github.com/calimero-network/boot-node/issues/37) —
  long-term: fold boot-node into core. Would let this primitive
  use a single binary instead of two images.
