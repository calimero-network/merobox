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
# SHA-256 checksums of the upstream GitHub-release binaries.
# Pinning per-arch defends against:
#   * a compromised release artifact (someone replacing the asset
#     post-publish without bumping the version),
#   * GitHub serving a truncated download (CDN edge issue),
#   * GitHub serving an HTML error page (transient 5xx wrapped as
#     200 OK in some flows — curl wouldn't catch it because the
#     bytes "look" like a valid response).
# Override per-arch via Docker build-args when bumping
# BOOT_NODE_VERSION. Get the canonical values with e.g.:
#   curl -sL https://github.com/calimero-network/boot-node/releases/download/${VER}/boot-node-x86_64-unknown-linux | shasum -a 256
ARG BOOT_NODE_SHA256_X86_64=c82b06e1c81a1cfcf1dce0fd38459e7242b905e72ac01bee2b95fb160072a924
ARG BOOT_NODE_SHA256_AARCH64=fa83e8f0547cbb85424a4ffbbc90879f346da0570957af6c8cb033319c60daa6

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The release asset is a bare ELF binary, not a tarball — `curl` and
# `chmod` are all we need; no extraction step.
RUN set -eux; \
    arch="$(uname -m)"; \
    case "${arch}" in \
        x86_64)  asset="boot-node-x86_64-unknown-linux"; expected_sha="${BOOT_NODE_SHA256_X86_64}" ;; \
        aarch64) asset="boot-node-aarch64-unknown-linux"; expected_sha="${BOOT_NODE_SHA256_AARCH64}" ;; \
        *) echo "unsupported arch: ${arch}" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/calimero-network/boot-node/releases/download/${BOOT_NODE_VERSION}/${asset}"; \
    # `--retry 3` alone only retries on transient errors (timeouts,
    # connection refused). GitHub release downloads from anonymous
    # IPs get rate-limited with HTTP 429 or 403, which curl treats
    # as a hard failure under `-f` — exit 22 — and skips the retry.
    # `--retry-all-errors` (curl 7.71+) extends the retry to HTTP
    # error responses, and `--retry-delay 5` gives the rate-limit
    # bucket time to refill. A user-agent string also helps avoid
    # heuristic anti-abuse blocks on bare default-UA curl. Bump
    # max-time to 120s so a slow CI link doesn't trip on the
    # 14MB binary.
    curl -fSL --retry 5 --retry-all-errors --retry-delay 5 \
         --max-time 120 \
         -H "User-Agent: merobox-boot-node-build/0.1" \
         -o /tmp/boot-node "${url}"; \
    # Verify the SHA-256 BEFORE chmod+exec. If the upstream is
    # compromised or the download is truncated, fail loudly here
    # rather than running a tampered binary inside the topology.
    actual_sha="$(sha256sum /tmp/boot-node | awk '{print $1}')"; \
    if [ "${actual_sha}" != "${expected_sha}" ]; then \
        echo "boot-node SHA-256 mismatch for ${asset}:" >&2; \
        echo "  expected: ${expected_sha}" >&2; \
        echo "  actual:   ${actual_sha}" >&2; \
        echo "Either the upstream release changed (bump BOOT_NODE_SHA256_${arch^^} to match) or the download is corrupt." >&2; \
        exit 1; \
    fi; \
    chmod +x /tmp/boot-node; \
    # Smoke-test the downloaded binary so the image fails to build
    # if the asset is for the wrong arch despite the matching hash
    # (e.g., someone bumped the arch-specific sha to the wrong
    # value). `--help` exits 0 on success and non-zero on missing
    # flag — we accept either; what we care about is that the
    # process actually launches, which `--version` would normally
    # cover but `boot-node --version` isn't supported, so we use
    # `--help` and accept any exit code other than the dynamic-
    # loader-failure ones (127, 128+N for signals, etc.). The
    # `[ $? -lt 127 ]` check is intentional: a wrong-arch binary
    # fails with 126 (Exec format error) under bash, dynamic-
    # loader errors yield 127, signals yield 128+; we treat any of
    # those as a build failure.
    /tmp/boot-node --help >/dev/null 2>&1; rc=$?; \
    if [ "$rc" -ge 126 ]; then \
        echo "boot-node smoke-test failed (exit $rc) — wrong arch?" >&2; exit 1; \
    fi

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
