# merobox merod-with-iproute2 wrapper for NAT-topology clients
#
# The stock merod image (`ghcr.io/calimero-network/merod:*`) is
# based on `debian:bookworm-slim` and does not ship `iproute2`.
# That works fine for normal cluster-mode workflows where the
# default Docker bridge handles routing. NAT topology needs each
# client container to install a default route pointing at the
# NAT gateway's LAN-side IP — and `ip route add` requires the
# `iproute2` package.
#
# Rather than reach into the client's network namespace from a
# sidecar (the `network=container:<client>` netns-sharing trick),
# we extend the stock merod image with iproute2 once and reuse it
# for every NAT-topology client. Build cost is one apt-install
# layer; runtime cost is zero. The image is built on first use
# by `merobox/topology/nat.py::_build_image_if_missing` and
# cached locally as `merobox/merod-nat-client:local`.
#
# `BASE_IMAGE` build-arg lets a workflow pin to a specific merod
# release (e.g. `merod:0.10.1-rc.42`) without rebuilding the
# wrapper recipe. Default tracks the same edge tag the workflows
# use directly.

ARG BASE_IMAGE=ghcr.io/calimero-network/merod:edge

FROM ${BASE_IMAGE}

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Leave entrypoint / CMD inherited from the base merod image so the
# wrapper drops in transparently. The container starts as merod,
# with iproute2 available for the post-start route injection.
