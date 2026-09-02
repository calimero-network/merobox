"""The workflow behind the integration fixture must ask for its discovery.

`merod` leaves mDNS off unless asked for, so a workflow that sets no `mdns`
and no bootstrap peers gets nodes that never find each other: the namespace
join finds no reachable peer, gets no group key, and returns 500, taking every
test that shares the session fixture with it.

The fixture inherited working discovery from an older merod default rather
than requesting it, so a core-side default flip turned this repo red with no
change of its own. Asking explicitly is what makes that impossible.

Asking for mDNS turned out not to be enough on its own, which is the second
test here. `merod init` writes the public dev boot node into
`bootstrap.nodes`, so a workflow that does not clear it has both nodes dial
out to that network and reserve relay circuits on it. Rendezvous publishes
each node's EXTERNAL addresses — those circuits — so a node learns its sibling
only as a relay path and never as the bridge address it can actually reach;
`identify` cannot help, because it reports listen addresses over an
established connection, which is what is missing. mDNS is then the only route
left, and on a busy bridge its packets fail to parse. See
calimero-network/core#3801.
"""

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_CONFTEST = _ROOT / "example-project" / "conftest.py"


def _fixture_workflow() -> tuple[Path, dict]:
    """The workflow `@run_workflow` loads, resolved against example-project/."""
    match = re.search(r'@run_workflow\(\s*"([^"]+)"', _CONFTEST.read_text())
    assert match, "no @run_workflow(...) literal found in example-project/conftest.py"
    path = (_CONFTEST.parent / match.group(1)).resolve()
    return path, yaml.safe_load(path.read_text())


def test_fixture_workflow_exists():
    path, _ = _fixture_workflow()
    assert path.is_file()


def test_fixture_workflow_asks_for_discovery():
    path, config = _fixture_workflow()
    nodes = config.get("nodes") or {}
    reachable = nodes.get("mdns") is True or bool(config.get("restart"))
    assert reachable, (
        f"{path.relative_to(_ROOT)} must set 'mdns: true' on its nodes (or wire "
        f"bootstrap peers via 'restart: true'); merod leaves mDNS off unless asked, "
        f"so relying on the default means the nodes never discover each other"
    )


def test_fixture_workflow_is_isolated_from_the_public_network():
    """The fixture must not inherit `merod init`'s public boot node.

    Leaving it in place makes mDNS the only route to a sibling's bridge
    address, because everything else a node learns about its sibling comes
    from rendezvous — which publishes relay circuits through that same public
    network. `e2e_mode` clears `bootstrap.nodes` so the cluster wiring supplies
    bridge addresses instead; an explicit `bootstrap_nodes` list is the other
    acceptable answer, since that is also a deliberate choice rather than an
    inherited default.
    """
    path, config = _fixture_workflow()

    isolated = (
        config.get("e2e_mode") is True or config.get("bootstrap_nodes") is not None
    )
    assert isolated, (
        f"{path.relative_to(_ROOT)} must set 'e2e_mode: true' (or an explicit "
        f"'bootstrap_nodes') so the cluster does not inherit merod init's public "
        f"dev boot node; inheriting it leaves mDNS as the only path to a "
        f"sibling's directly dialable address"
    )
    assert not config.get("preserve_default_bootstrap"), (
        f"{path.relative_to(_ROOT)} sets 'preserve_default_bootstrap', which "
        f"keeps the public boot node even under e2e_mode — the isolation this "
        f"test exists for is then not in effect"
    )
