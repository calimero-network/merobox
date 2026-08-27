"""CI must pin the core release it tests against, not resolve the newest one.

`requirements.txt` pins `calimero-client-py` and the release-binary lanes drive a
released `merod`. Those two ship from different repos on different schedules and
talk to each other over a wire format, so when they briefly disagree every PR
here goes red at once and none of them touched anything related.

That happened on 2026-08-21: client-py 0.6.31 published at 12:55 expecting
`namespaceId` on the join response, released core rc.24 did not send it, and
every namespace join failed until core rc.25 published at 15:19 and the
`--limit 1` resolution silently moved on. Two and a half hours of red CI with no
merobox commit involved in either direction — and because the first red run lands
on whatever merged that morning, the blame goes to the wrong place. The companion
to this test, `test_dependency_pins_agree`, guards the other half of the pair.

The `:edge` docker lane is exempt on purpose: its node is a moving image, so
pinning its `meroctl` beside it would create the same skew in the other
direction. That lane floats deliberately and is the one that notices core's wire
changes early.
"""

import re
from pathlib import Path

_CI = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
_RESOLVE_LATEST = re.compile(r"gh release list --repo calimero-network/core --limit 1")


def test_core_version_is_declared_once():
    text = _CI.read_text()
    pins = re.findall(r'^\s*CORE_VERSION:\s*"([^"]+)"', text, re.M)

    assert len(pins) == 1, (
        f"expected exactly one CORE_VERSION declaration, found {len(pins)}: "
        f"{pins}. More than one place to edit is how a pin half-moves."
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+(-rc\.\d+)?", pins[0]), (
        f"CORE_VERSION={pins[0]!r} does not look like a released core tag; "
        f"'latest' or a moving ref would defeat the point of pinning."
    )


def test_only_the_edge_lane_resolves_the_newest_core_release():
    text = _CI.read_text()
    floating = _RESOLVE_LATEST.findall(text)

    assert len(floating) == 1, (
        f"{len(floating)} lane(s) resolve the newest core release; exactly one "
        f"(the `:edge` docker lane, whose node is itself a moving image) is "
        f"meant to. A release-binary lane that resolves `--limit 1` will go red "
        f"the next time core and client-py disagree, on a PR that changed "
        f"neither — use $CORE_VERSION instead."
    )


def test_the_pinned_lanes_actually_use_the_pin():
    text = _CI.read_text()
    uses = re.findall(r'TAG="\$CORE_VERSION"', text)

    assert len(uses) >= 2, (
        f"expected the release-binary lanes to consume CORE_VERSION, found "
        f"{len(uses)} use(s). Declaring the pin without using it reads as pinned "
        f"while still floating."
    )
