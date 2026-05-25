# merobox boot-node (relay-server) image
#
# Wraps the released `boot-node-x86_64-unknown-linux` binary from
# calimero-network/boot-node in a minimal debian-slim base. The boot-
# node is the libp2p relay-server + rendezvous-server + kademlia-DHT
# entrypoint that NAT'd Calimero clients register reservations with;
# merod itself only ships the relay-CLIENT behaviour, so the NAT
# topology can't use a stock merod container for this role.
#
# The release version is parameterised via BOOT_NODE_VERSION so a
# workflow can pin to a specific release across reruns. Default is
# the latest tagged release at the time merobox shipped this image
# definition. The Calimero binary is downloaded at image-build time
# (not at container-startup) so the first container start in CI is
# fast and reproducible.

FROM debian:bookworm-slim AS download

ARG BOOT_NODE_VERSION=0.8.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The release asset is a bare ELF binary, not a tarball — `curl` and
# `chmod` are all we need; no extraction step.
RUN set -eux; \
    arch="$(uname -m)"; \
    case "${arch}" in \
        x86_64)  asset="boot-node-x86_64-unknown-linux" ;; \
        aarch64) asset="boot-node-aarch64-unknown-linux" ;; \
        *) echo "unsupported arch: ${arch}" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/calimero-network/boot-node/releases/download/${BOOT_NODE_VERSION}/${asset}"; \
    curl -fSL --retry 3 -o /tmp/boot-node "${url}"; \
    chmod +x /tmp/boot-node; \
    /tmp/boot-node --help >/dev/null 2>&1 || true

# Runtime stage: keep only the binary + minimal CA bundle. Boot-node
# doesn't need much at runtime (no DNS resolution to outside domains
# in the normal NAT-topology setup, but ca-certificates is cheap and
# helps if the operator ever adds an `--rendezvous-bootstrap-peer`
# pointing at a TLS endpoint).
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=download /tmp/boot-node /usr/local/bin/boot-node

# 4001 is the default libp2p listen port (boot-node's `--port` flag
# defaults to it too). Single port covers both TCP and QUIC since
# libp2p listens on both with the same number.
EXPOSE 4001/tcp 4001/udp

# Default to ephemeral keypair (`--dev`) so the container can be
# spawned with no extra setup. Tests that need a stable peer ID can
# override by mounting a keypair and passing `--private-key /path`.
CMD ["boot-node", "--dev"]
