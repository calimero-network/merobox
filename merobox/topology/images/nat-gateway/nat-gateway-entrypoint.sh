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
#
# `net.ipv4.ip_forward` is the master switch, but Linux ALSO
# requires per-interface forwarding to be enabled on the INPUT
# interface (see `net.ipv4.conf.<iface>.forwarding`). The master
# switch is set via `--sysctl net.ipv4.ip_forward=1` at container
# create-time, but per-iface flags inherit from
# `net.ipv4.conf.default.forwarding` AT THE MOMENT THE INTERFACE
# IS ATTACHED — and for the LAN-side eth1, which is added via
# `network.connect()` post-create, the inheritance was timing-
# dependent. Result: master switch =1, eth1 per-iface forwarding =0,
# kernel sends "network unreachable" ICMPs back to clients (the
# `punt!` we saw in CI diagnostics).
#
# Belt-and-suspenders: explicitly enable both master + every
# attached interface's per-iface forwarding here, in the entry-
# point that runs AFTER all interfaces are wired up. Per-iface
# sysctls become writable from inside the netns under NET_ADMIN.
if ! sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1; then
    echo "[merobox/nat-gateway] WARN: in-container sysctl write failed; expecting --sysctl on container create to have set ip_forward already" >&2
    if [ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0)" != "1" ]; then
        echo "[merobox/nat-gateway] FATAL: net.ipv4.ip_forward is NOT 1 — gateway cannot forward; refusing to start" >&2
        exit 1
    fi
fi
# Per-interface forwarding for every attached interface, including
# `all` and `default` (which gate the inheritance). The loop is
# tolerant of write failures — `lo`'s forwarding sometimes can't
# be written, and that's fine.
for iface_dir in /proc/sys/net/ipv4/conf/*/forwarding; do
    if ! echo 1 > "${iface_dir}" 2>/dev/null; then
        echo "[merobox/nat-gateway] WARN: could not set ${iface_dir}=1 (probably benign for lo)" >&2
    fi
done
echo "[merobox/nat-gateway] per-iface forwarding state:"
for iface_dir in /proc/sys/net/ipv4/conf/*/forwarding; do
    echo "  ${iface_dir} = $(cat "${iface_dir}")"
done

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
