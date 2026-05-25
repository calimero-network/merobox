# merobox NAT gateway
#
# Straddles a "public" Docker bridge and an `--internal` LAN bridge,
# routing client-bound packets through `iptables MASQUERADE`. Used
# only by `topology: { type: nat }` workflows; not exposed outside
# CI.
#
# The script reads two env vars at startup:
#   NAT_MODE       — "cone" | "symmetric"
#                    cone     → plain MASQUERADE
#                    symmetric → MASQUERADE --random-fully
#                                (requires kernel + iptables-extensions
#                                 with random-fully support; falls back
#                                 to plain MASQUERADE with a warn if
#                                 unavailable)
#   PUBLIC_IFACE   — interface name on the public bridge
#                    (defaults to `eth0`)
#
# `tail -f /dev/null` keeps the container alive once iptables is set;
# the routing happens entirely in-kernel and the container itself
# does no application-level work.

FROM alpine:3.19

# iptables + iproute2 = NAT rules + diagnostic `ip route show`.
# bash for `set -euo pipefail` semantics in the entrypoint.
RUN apk add --no-cache iptables ip6tables iproute2 bash

COPY nat-gateway-entrypoint.sh /usr/local/bin/nat-gateway-entrypoint.sh
RUN chmod +x /usr/local/bin/nat-gateway-entrypoint.sh

ENV NAT_MODE=cone \
    PUBLIC_IFACE=eth0

ENTRYPOINT ["/usr/local/bin/nat-gateway-entrypoint.sh"]
