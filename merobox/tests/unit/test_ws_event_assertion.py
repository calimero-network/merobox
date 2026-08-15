"""
Unit tests for the assert_ws_event workflow step.

The aiohttp layer is mocked with a socket that replays a scripted frame
sequence and then blocks, so the watch window really ends on the step's own
deadline rather than on the fixture running out. Frame shapes are the wire form
of calimero's GroupMigration events: a PascalCase `type` tag beside the hex
`groupId`, with camelCase fields under `data`.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import aiohttp
import pytest

from merobox.commands.bootstrap.steps.websocket import WebSocketEventAssertStep

# Long enough to let the scripted frames land, short enough that the tests
# exercising the timeout path stay fast.
_WINDOW = 0.3

_GROUP = "21" * 32
_SUBGROUP = "31" * 32


@pytest.fixture(autouse=True)
def _wide_console():
    """Rich soft-wraps at terminal width, splitting the strings asserted on."""
    from merobox.commands.utils import console

    original = console.width
    console.width = 400
    yield
    console.width = original


def _run(coro):
    """Run a coroutine on a dedicated loop, leaving a fresh current loop behind."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _manager():
    manager = MagicMock()
    manager.get_node_rpc_port.return_value = 2528
    return manager


class _FakeMsg:
    def __init__(self, data, type_=aiohttp.WSMsgType.TEXT):
        self.type = type_
        self.data = data


class _ScriptedWS:
    """Replays scripted frames, then blocks so the step's deadline ends the window."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []
        self.closed = False

    async def send_str(self, message):
        self.sent.append(message)

    async def receive(self):
        if self._frames:
            frame = self._frames.pop(0)
            if isinstance(frame, _Delayed):
                await asyncio.sleep(frame.after)
                return frame.frame
            return frame
        await asyncio.sleep(3600)

    async def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def ws_connect(self, url):
        return self._ws


def _ack(group_ids=(_GROUP,)):
    """The subscribe acknowledgement: the ids the node actually subscribed."""
    return _FakeMsg(
        json.dumps({"id": 1, "result": {"contextIds": [], "groupIds": list(group_ids)}})
    )


class _Delayed:
    """A scripted frame that only arrives after `after` seconds."""

    def __init__(self, after, frame):
        self.after = after
        self.frame = frame


def _delayed(after, frame):
    return _Delayed(after, frame)


def _event(event_type, data, group=_GROUP):
    return _FakeMsg(
        json.dumps(
            {"id": None, "result": {"groupId": group, "type": event_type, "data": data}}
        )
    )


def _migration_started(from_version="1.0.0", to_version="2.0.0", to_state_version=2):
    return _event(
        "MigrationStarted",
        {
            "fromVersion": from_version,
            "toVersion": to_version,
            "toStateVersion": to_state_version,
            "localContextsTotal": 7,
        },
    )


def _migration_completed(to_version="2.0.0"):
    return _event(
        "MigrationCompleted", {"toVersion": to_version, "completedAt": 1_700_000_000}
    )


def _migration_progress():
    return _event(
        "MigrationProgress",
        {"migrated": 1, "inProgress": 1, "unknown": 0, "failed": 0, "total": 2},
    )


def _step(**extra):
    config = {
        "type": "assert_ws_event",
        "name": "ws-event",
        "node": "calimero-node-2",
        "group_id": _GROUP,
        "event": "MigrationStarted",
        "timeout_seconds": _WINDOW,
        **extra,
    }
    return WebSocketEventAssertStep(config, manager=_manager())


def _execute(step, frames, workflow_results=None, session=None, token=None):
    """Run the step against a socket replaying `frames`, returning (result, ws).

    `session` overrides the transport for the tests that need a slow or
    refusing connect; `token` seeds the auth cache for the redaction tests.
    """
    ws = _ScriptedWS(frames)
    auth = MagicMock()
    auth.get_cached_token.return_value = (
        MagicMock(access_token=token) if token else None
    )
    with (
        patch(
            "merobox.commands.bootstrap.steps.base.AuthManager",
            return_value=auth,
        ),
        patch(
            "merobox.commands.bootstrap.steps.websocket.aiohttp.ClientSession",
            return_value=session if session is not None else _FakeSession(ws),
        ),
    ):
        return (
            _run(
                step.execute(
                    workflow_results if workflow_results is not None else {}, {}
                )
            ),
            ws,
        )


class _SlowHandshake(_FakeSession):
    """A session whose connect takes `delay` seconds to complete."""

    def __init__(self, ws, delay):
        super().__init__(ws)
        self._delay = delay

    async def ws_connect(self, url):
        await asyncio.sleep(self._delay)
        return self._ws


class TestPositiveAssertion:
    def test_matching_event_satisfies_the_step(self):
        result, ws = _execute(_step(), [_ack(), _migration_started()])
        assert result is True
        # The subscription rides groupIds, hex-encoded like the admin API.
        assert json.loads(ws.sent[0]) == {
            "id": 1,
            "method": "subscribe",
            "params": {"groupIds": [_GROUP]},
        }

    def test_event_of_another_type_does_not_satisfy_it(self, capsys):
        result, _ = _execute(
            _step(), [_ack(), _migration_progress(), _migration_completed()]
        )
        assert result is False
        combined = capsys.readouterr().out
        # The two events that did arrive have to be in the report.
        assert "MigrationProgress" in combined
        assert "MigrationCompleted" in combined

    def test_nested_payload_field_is_matched(self):
        step = _step(event="MigrationCompleted", match={"data.toVersion": "2.0.0"})
        result, _ = _execute(step, [_ack(), _migration_completed(to_version="2.0.0")])
        assert result is True

    def test_a_wrong_nested_field_value_does_not_match(self, capsys):
        step = _step(event="MigrationCompleted", match={"data.toVersion": "2.0.0"})
        result, _ = _execute(step, [_ack(), _migration_completed(to_version="1.9.0")])
        assert result is False
        combined = capsys.readouterr().out
        # The near miss must name the path, what was wanted, and what came.
        assert "data.toVersion" in combined
        assert "'2.0.0'" in combined
        assert "'1.9.0'" in combined

    def test_an_absent_path_does_not_satisfy_a_null_match(self, capsys):
        # Absent and present-and-null are different answers, so `match: {x: null}`
        # may not be satisfied by a frame that never carries x.
        step = _step(event="MigrationCompleted", match={"data.rolledBackAt": None})
        result, _ = _execute(step, [_ack(), _migration_completed()])
        assert result is False
        assert "data.rolledBackAt: expected None, but the key is absent" in (
            capsys.readouterr().out
        )

    def test_an_explicit_null_satisfies_a_null_match(self):
        step = _step(event="MigrationCompleted", match={"data.rolledBackAt": None})
        result, _ = _execute(
            step, [_ack(), _event("MigrationCompleted", {"rolledBackAt": None})]
        )
        assert result is True

    def test_a_string_that_looks_like_a_literal_is_compared_as_a_string(self):
        # The frame is compared verbatim: a field whose value is the string
        # "null" must not be coerced into the null that `match: {p: null}`
        # asserts, which would undo the absent-versus-null distinction above.
        result, _ = _execute(
            _step(event="MigrationCompleted", match={"data.hlc": None}),
            [_ack(), _event("MigrationCompleted", {"hlc": "null"})],
        )
        assert result is False

    def test_matching_is_a_subset_so_unlisted_fields_are_ignored(self):
        # Only toStateVersion is asserted; the frame carries three more fields.
        step = _step(match={"data.toStateVersion": 2})
        result, _ = _execute(step, [_ack(), _migration_started(to_state_version=2)])
        assert result is True

    def test_the_matched_event_is_stored_for_later_steps(self):
        results = {}
        _execute(_step(), [_ack(), _migration_started()], workflow_results=results)
        assert results["ws_event_calimero-node-2"]["type"] == "MigrationStarted"


class TestTimeoutReporting:
    def test_timeout_names_what_actually_arrived(self, capsys):
        result, _ = _execute(_step(), [_ack(), _migration_progress()])
        assert result is False
        combined = capsys.readouterr().out
        assert "MigrationStarted" in combined, "the expectation should be named"
        assert "1 event(s) arrived" in combined
        # The whole frame is replayed, not just the type, so a mismatch is
        # diagnosable from CI output alone.
        assert "inProgress" in combined

    def test_timeout_with_an_empty_stream_says_so(self, capsys):
        result, _ = _execute(_step(), [_ack()])
        assert result is False
        assert "no events arrived on the subscription" in capsys.readouterr().out


class TestNegativeAssertion:
    def test_absent_passes_when_the_event_never_arrives(self):
        step = _step(event="CascadeProgress", absent=True)
        result, _ = _execute(step, [_ack(), _migration_progress()])
        assert result is True

    def test_absent_fails_when_the_event_does_arrive(self, capsys):
        step = _step(event="CascadeProgress", absent=True)
        cascade = _event(
            "CascadeProgress",
            {
                "subgroupId": _SUBGROUP,
                "localContextsSwapped": 1,
                "localContextsTotal": 2,
            },
        )
        result, _ = _execute(step, [_ack(), cascade])
        assert result is False
        combined = capsys.readouterr().out
        assert "expected exactly 0 'CascadeProgress'" in combined
        assert "but 1 arrived" in combined
        assert _SUBGROUP in combined, "the offending frame must be printed"

    def test_absent_respects_match_so_a_different_payload_is_not_a_violation(self):
        # "no MigrationCompleted to 3.0.0" must not trip on a completion to 2.0.0.
        step = _step(
            event="MigrationCompleted", match={"data.toVersion": "3.0.0"}, absent=True
        )
        result, _ = _execute(step, [_ack(), _migration_completed(to_version="2.0.0")])
        assert result is True


class TestExactCount:
    def test_exactly_one_passes_when_exactly_one_arrives(self):
        step = _step(event="MigrationCompleted", count=1)
        result, _ = _execute(step, [_ack(), _migration_completed()])
        assert result is True

    def test_a_second_arrival_fails_the_exactly_one_assertion(self, capsys):
        # The guarantee core holds is that convergence fires MigrationCompleted
        # once; a duplicate is the regression this catches.
        step = _step(event="MigrationCompleted", count=1)
        result, _ = _execute(
            step, [_ack(), _migration_completed(), _migration_completed()]
        )
        assert result is False
        combined = capsys.readouterr().out
        assert "expected exactly 1 'MigrationCompleted'" in combined
        assert "but 2 arrived" in combined
        # Both arrivals are replayed and marked as the ones that were counted.
        assert combined.count("match >") == 2

    def test_no_arrival_fails_the_exactly_one_assertion(self, capsys):
        step = _step(event="MigrationCompleted", count=1)
        result, _ = _execute(step, [_ack(), _migration_progress()])
        assert result is False
        combined = capsys.readouterr().out
        assert "but 0 arrived" in combined
        # The non-matching event still has to be replayed, unmarked.
        assert "MigrationProgress" in combined
        assert combined.count("match >") == 0

    def test_count_two_passes_on_two_arrivals(self):
        step = _step(event="MigrationProgress", count=2)
        result, _ = _execute(
            step, [_ack(), _migration_progress(), _migration_progress()]
        )
        assert result is True

    def test_count_zero_is_the_same_assertion_as_absent(self):
        step = _step(event="CascadeProgress", count=0)
        result, _ = _execute(step, [_ack(), _migration_progress()])
        assert result is True

    def test_the_default_form_tolerates_a_second_arrival(self):
        # Without `count`, "at least one" stays the default and stops early, so
        # a repeat must not turn a passing assertion red.
        result, _ = _execute(
            _step(), [_ack(), _migration_started(), _migration_started()]
        )
        assert result is True


class TestUnobservedWindow:
    """A window that was never watched must fail both directions, not pass one."""

    def test_denied_subscription_fails_the_positive_assertion(self, capsys):
        # The node drops ids the caller may not observe, so the ack comes back
        # with no groupIds at all.
        result, _ = _execute(_step(), [_ack(group_ids=())])
        assert result is False
        assert "refused a subscription" in capsys.readouterr().out

    def test_an_ack_naming_only_another_group_is_a_denial(self, capsys):
        # A non-empty ack is not proof: this group stays unwatched, so the
        # window was never observed.
        result, _ = _execute(_step(), [_ack(group_ids=(_SUBGROUP,))])
        assert result is False
        assert "refused a subscription" in capsys.readouterr().out

    def test_an_ack_naming_only_another_group_fails_absent_too(self, capsys):
        # The vacuous pass this guards: nothing arrives because nothing is
        # being watched, which is not evidence of absence.
        step = _step(event="CascadeProgress", absent=True)
        result, _ = _execute(step, [_ack(group_ids=(_SUBGROUP,))])
        assert result is False
        assert "refused a subscription" in capsys.readouterr().out

    def test_an_ack_listing_the_group_among_others_is_accepted(self):
        result, _ = _execute(
            _step(), [_ack(group_ids=(_SUBGROUP, _GROUP)), _migration_started()]
        )
        assert result is True

    def test_denied_subscription_fails_the_negative_assertion_too(self, capsys):
        step = _step(event="CascadeProgress", absent=True)
        result, _ = _execute(step, [_ack(group_ids=())])
        assert result is False
        combined = capsys.readouterr().out
        assert "refused a subscription" in combined

    def test_an_unacknowledged_subscription_fails(self, capsys):
        step = _step(absent=True)
        result, _ = _execute(step, [])
        assert result is False
        assert "never acknowledged" in capsys.readouterr().out

    def test_an_early_close_fails_the_negative_assertion(self, capsys):
        step = _step(event="CascadeProgress", absent=True)
        closed = _FakeMsg(None, type_=aiohttp.WSMsgType.CLOSED)
        result, _ = _execute(step, [_ack(), closed])
        assert result is False
        assert "the connection ended" in capsys.readouterr().out


class TestTokenRedaction:
    """The JWT rides in the URL's query string, which aiohttp errors echo."""

    def test_a_transport_error_echoing_the_url_is_masked(self, capsys):
        class _Refusing(_FakeSession):
            async def ws_connect(self, url):
                raise aiohttp.ClientError(f"Cannot connect to {url}")

        result, _ = _execute(_step(), [], session=_Refusing(None), token="acc.jwt.tok")
        assert result is False
        out = capsys.readouterr().out
        assert "acc.jwt.tok" not in out
        assert "token=***" in out

    def test_the_watched_url_is_never_printed_in_full(self, capsys):
        result, _ = _execute(
            _step(), [_ack(), _migration_started()], token="acc.jwt.tok"
        )
        assert result is True
        assert "acc.jwt.tok" not in capsys.readouterr().out


class TestWatchWindow:
    def test_the_window_is_measured_from_the_acknowledgement(self):
        # A slow handshake must not eat the window: `absent` would then call
        # the shortfall evidence of absence, and a positive assertion would
        # time out for a reason that has nothing to do with the stream.
        ws = _ScriptedWS([_ack(), _delayed(0.6, _migration_started())])
        result, _ = _execute(
            _step(timeout_seconds=1.0), [], session=_SlowHandshake(ws, 0.6)
        )
        assert result is True

    def test_a_slow_handshake_is_not_reported_as_an_unacknowledged_one(self, capsys):
        # The acknowledgement gets its own window too: charging the handshake
        # to it would blame the node for a connect merobox waited out.
        ws = _ScriptedWS([_delayed(0.4, _ack()), _migration_started()])
        result, _ = _execute(
            _step(timeout_seconds=1.0), [], session=_SlowHandshake(ws, 0.8)
        )
        assert result is True
        assert "never acknowledged" not in capsys.readouterr().out


class TestUnresolvedPlaceholder:
    def test_it_is_reported_as_this_step_s_failure(self, capsys):
        # Same contract as assert / json_assert: a placeholder that never
        # bound is a failed assertion, not an exception for the executor.
        result, _ = _execute(
            _step(group_id="{{never_bound}}"), [_ack(), _migration_started()]
        )
        assert result is False
        assert "✗ assert_ws_event on calimero-node-2" in capsys.readouterr().out

    def test_a_match_value_that_never_bound_is_reported_too(self, capsys):
        result, _ = _execute(
            _step(match={"data.toVersion": "{{never_bound}}"}),
            [_ack(), _migration_started()],
        )
        assert result is False
        assert "✗ assert_ws_event on calimero-node-2" in capsys.readouterr().out


class TestValidation:
    def test_group_id_and_event_are_required(self):
        with pytest.raises(ValueError):
            WebSocketEventAssertStep(
                {"type": "assert_ws_event", "node": "calimero-node-1"},
                manager=_manager(),
            )

    def test_a_non_finite_timeout_is_rejected(self):
        # inf survives the positive check but leaves the deadline unreachable.
        with pytest.raises(ValueError):
            _step(timeout_seconds=float("inf"))

    def test_match_must_be_a_dict_of_paths(self):
        with pytest.raises(ValueError):
            _step(match={"": "x"})

    def test_count_and_absent_are_mutually_exclusive(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            _step(count=1, absent=True)

    def test_a_negative_count_is_rejected(self):
        with pytest.raises(ValueError):
            _step(count=-1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
