"""The default merod image resolves, and it is spelled once.

The default deliberately FLOATS: `ghcr.io/calimero-network/merod:prerelease`
is an alias ghcr re-points at every core prerelease, and core publishes it so
merobox can follow it. merobox drives merod's admin API directly, so the two
move as a pair; a hand-bumped release tag would be stale within days, and
merobox is not in core's `.github/fleet.json`, so nothing would move it.

What must never come back is an EPHEMERAL tag. ghcr garbage-collects bare
commit tags and expires `pr-<N>` when the PR closes — merobox#1. The sample
workflow `merobox bootstrap create-sample` generates carried
`merod:6a47604` until it started 404ing, so the starter workflow could not
pull its own node.

The second test is the one with teeth. Pinning or repointing the constant
would have changed nothing on its own, because nothing imported it: the value
actually reaching Docker was a literal duplicated across three other modules,
and that is how the dead `6a47604` survived a year after #1 was closed.
"""

import ast
import pathlib
import re

from merobox.commands.constants import DEFAULT_IMAGE

# Tags ghcr keeps: the three published aliases, and release tags like
# `0.11.0-rc.32` / `0.11.0`. Anything else — `6a47604`, `pr-412` — is a tag
# that stops resolving.
DURABLE_TAG = re.compile(r"^(prerelease|edge|latest|\d+\.\d+\.\d+(-rc\.\d+)?)$")

MEROBOX_PKG = pathlib.Path(__file__).resolve().parents[2]

# The NAT primitive's boot node follows core's master on purpose and says so
# at its own definition.
SPELLS_ITS_OWN_IMAGE = {MEROBOX_PKG / "topology" / "nat.py"}


def test_default_image_names_a_tag_ghcr_keeps():
    repo, _, tag = DEFAULT_IMAGE.partition(":")
    assert repo == "ghcr.io/calimero-network/merod", DEFAULT_IMAGE
    assert DURABLE_TAG.match(tag), (
        f"DEFAULT_IMAGE is {DEFAULT_IMAGE!r}. ghcr garbage-collects commit "
        "tags and expires pr-<N> tags, so a default spelled that way stops "
        "resolving and the node cannot be pulled (merobox#1). Use a published "
        "alias or a release tag."
    )


def test_no_module_spells_a_merod_image_beside_the_constant():
    """One spelling, so a fix to the default reaches every caller.

    Three modules carried their own literal, which is why `DEFAULT_IMAGE` was
    dead code and why the sample config's tag could rot unnoticed.
    """
    offenders = []
    for path in MEROBOX_PKG.rglob("*.py"):
        if path in SPELLS_ITS_OWN_IMAGE or "tests" in path.parts:
            continue
        if path.name == "constants.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "calimero-network/merod:" in node.value:
                    rel = path.relative_to(MEROBOX_PKG)
                    offenders.append(f"{rel}:{node.lineno} {node.value!r}")
    assert (
        not offenders
    ), "merod image spelled outside constants.DEFAULT_IMAGE:\n  " + "\n  ".join(
        offenders
    )


def test_the_call_sites_use_the_constant():
    """A literal-free module could still have stopped consuming the default."""
    for rel in (
        "commands/manager.py",
        "commands/bootstrap/steps/script.py",
        "commands/bootstrap/config.py",
    ):
        source = (MEROBOX_PKG / rel).read_text()
        assert "DEFAULT_IMAGE" in source, f"{rel} no longer uses DEFAULT_IMAGE"
