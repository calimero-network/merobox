#!/usr/bin/env bash
#
# Entrypoint for the merobox NAT-gateway container.
#
# Enables IPv4 forwarding and installs an iptables NAT rule that
# MASQUERADEs outbound packets from the LAN interface onto the public
# interface. The mode is selected by the `NAT_MODE` env var:
#
#   cone       — plain MASQUERADE; outbound port is consistent per
#                source so STUN-style port prediction works and
#                libp2p's DCUtR hole-punching may succeed.
#   symmetric  — MASQUERADE --random-fully; outbound port is
#                randomised per destination, so hole-punching fails
#                reliably and clients are reachable only via the
#                relay (the stricter relay-recovery test shape).
#
# `--random-fully` requires a kernel + iptables build with the
# `random-fully` extension. Older kernels (or non-iptables-nft setups
# with an old iptables binary) fall back to plain MASQUERADE with a
# warn — the test will still run, but cone NAT semantics apply, and
# any "the relay must be used" assertions in the workflow won't fire.
# Detected by a probe rule that we install + remove before the real
# rule.

set -euo pipefail

NAT_MODE="${NAT_MODE:-cone}"
PUBLIC_IFACE="${PUBLIC_IFACE:-eth0}"

echo "[merobox/nat-gateway] starting with NAT_MODE=${NAT_MODE} PUBLIC_IFACE=${PUBLIC_IFACE}"

# IPv4 forwarding off by default in the alpine container; without
# this the kernel drops packets between interfaces regardless of
# what iptables says.
sysctl -w net.ipv4.ip_forward=1 >/dev/null

# Wipe any leftover NAT rules in case the container is being reused
# (shouldn't happen in CI but cheap to defend against).
iptables -t nat -F POSTROUTING

case "${NAT_MODE}" in
    cone)
        iptables -t nat -A POSTROUTING -o "${PUBLIC_IFACE}" -j MASQUERADE
        echo "[merobox/nat-gateway] installed cone NAT (plain MASQUERADE) on ${PUBLIC_IFACE}"
        ;;
    symmetric)
        # Probe for --random-fully support: try to install a dummy rule
        # with the flag and immediately delete it. If the install fails
        # the flag isn't supported here; fall back to plain MASQUERADE.
        if iptables -t nat -A POSTROUTING -o "${PUBLIC_IFACE}" -j MASQUERADE --random-fully 2>/dev/null; then
            iptables -t nat -D POSTROUTING -o "${PUBLIC_IFACE}" -j MASQUERADE --random-fully
            iptables -t nat -A POSTROUTING -o "${PUBLIC_IFACE}" -j MASQUERADE --random-fully
            echo "[merobox/nat-gateway] installed symmetric NAT (MASQUERADE --random-fully) on ${PUBLIC_IFACE}"
        else
            echo "[merobox/nat-gateway] WARN: --random-fully not supported on this host; falling back to plain MASQUERADE. Symmetric-NAT semantics will not apply." >&2
            iptables -t nat -A POSTROUTING -o "${PUBLIC_IFACE}" -j MASQUERADE
        fi
        ;;
    *)
        echo "[merobox/nat-gateway] FATAL: unknown NAT_MODE='${NAT_MODE}' (expected 'cone' or 'symmetric')" >&2
        exit 1
        ;;
esac

# Diagnostic snapshot for log archaeology when a test fails.
echo "[merobox/nat-gateway] active iptables nat rules:"
iptables -t nat -L POSTROUTING -v -n

echo "[merobox/nat-gateway] active routes:"
ip route show

# Stay alive; routing is in-kernel from here on.
exec tail -f /dev/null
