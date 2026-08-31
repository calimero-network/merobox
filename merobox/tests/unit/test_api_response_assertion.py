"""
Unit tests for the assert_api_response workflow step.

`requests` is mocked with a canned admin-API body, since the property under test
is that the step asserts against the bytes the node sent rather than against a
typed DTO that may have dropped keys. The body shape is the migration-status
envelope: a camelCase payload under `data`, with the optional fields core marks
`skip_serializing_if = "Option::is_none"` genuinely absent rather than null.
"""

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from merobox.commands.bootstrap.steps.api_assertion import AssertApiResponseStep

_PATH = "/admin-api/namespaces/abc/migration-status"


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


def _body(**overrides):
    data = {
        "targetVersion": 2,
        "fleetCompletedAt": 1786635547,
        "rollup": {"total": 3, "migrated": 3, "allMigrated": True},
        "members": [{"peer": "p1", "report": {"schemaVersion": 2}}],
    }
    data.update(overrides)
    return {"data": data}


def _step(**extra):
    config = {
        "type": "assert_api_response",
        "name": "raw-migration-status",
        "node": "calimero-node-1",
        "path": _PATH,
        **extra,
    }
    return AssertApiResponseStep(config, manager=_manager())


def _execute(
    step,
    payload,
    status=200,
    token=None,
    workflow_results=None,
    dynamic=None,
    text=None,
    error=None,
):
    """Run the step against a canned response, returning (result, get, results).

    ``text`` overrides only the decoded view, so a test can simulate requests
    guessing the charset wrong while the transmitted bytes stay correct.
    ``error`` makes the request raise instead of answering.
    """
    auth = MagicMock()
    auth.get_cached_token.return_value = (
        MagicMock(access_token=token) if token else None
    )
    body = payload if isinstance(payload, str) else json.dumps(payload)
    response = MagicMock()
    response.status_code = status
    response.content = body.encode()
    response.text = body if text is None else text

    results = workflow_results if workflow_results is not None else {}
    with (
        patch("merobox.commands.bootstrap.steps.base.AuthManager", return_value=auth),
        patch(
            "merobox.commands.bootstrap.steps.api_assertion.requests.get",
            return_value=response,
            side_effect=error,
        ) as get,
    ):
        return _run(step.execute(results, dynamic or {})), get, results


_DEVICES = {
    "data": {
        "devices": [
            {"deviceId": "aa", "applications": ["app-a"], "namespaces": ["ns-a1"]},
            {"deviceId": "bb", "applications": [], "namespaces": ["ns-a1", "ns-b1"]},
        ]
    }
}


class TestWhereSelector:
    """`where` picks an element by identity, which is what a list body needs.

    Without it a device can only be reached by position, and position is decided
    by a key-ordered store scan rather than by anything a scenario controls.
    """

    def test_asserts_against_the_element_it_names(self):
        step = _step(where={"deviceId": "bb"}, match={"namespaces.1": "ns-b1"})
        result, _get, _results = _execute(step, _DEVICES)
        assert result is True

    def test_the_wrong_element_does_not_satisfy_it(self):
        # `aa` is bound in one namespace, `bb` in two. Asserting bb's shape
        # against aa has to fail, or `where` is decorative.
        step = _step(where={"deviceId": "aa"}, match={"namespaces.1": "ns-b1"})
        result, _get, _results = _execute(step, _DEVICES)
        assert result is False

    def test_no_matching_element_fails_rather_than_asserting_on_the_body(self):
        step = _step(where={"deviceId": "zz"}, match={"deviceId": "zz"})
        result, _get, _results = _execute(step, _DEVICES)
        assert result is False

    def test_a_placeholder_in_where_resolves(self):
        step = _step(
            where={"deviceId": "{{device}}"}, match={"applications.0": "app-a"}
        )
        result, _get, _results = _execute(step, _DEVICES, dynamic={"device": "aa"})
        assert result is True

    def test_without_where_the_whole_body_is_asserted(self):
        step = _step(match={"data.rollup.total": 3})
        result, _get, _results = _execute(step, _body())
        assert result is True


class TestNotMatchAndContains:
    """The two shapes core's revoke and relink assertions need.

    A revoked device is checked by what it is NOT - the spent id, and the holder's
    account - and a relink's scope is a set the node builds by scan order, so
    asserting a position would fail on a reordering that changed nothing.
    """

    _IDENTITY = {"data": {"accountId": "own-account", "deviceId": "fresh-device"}}

    def test_not_match_passes_when_the_value_differs(self):
        step = _step(
            not_match={"data.deviceId": "spent-device", "data.accountId": "holder"}
        )
        result, _get, _results = _execute(step, self._IDENTITY)
        assert result is True

    def test_not_match_fails_when_the_value_is_the_forbidden_one(self):
        step = _step(not_match={"data.deviceId": "fresh-device"})
        result, _get, _results = _execute(step, self._IDENTITY)
        assert result is False

    def test_not_match_fails_when_the_key_is_absent(self):
        # Absent is not "different": a renamed field would otherwise read as a
        # passing negative assertion forever.
        step = _step(not_match={"data.nope": "anything"})
        result, _get, _results = _execute(step, self._IDENTITY)
        assert result is False

    def test_contains_ignores_order(self):
        body = {"data": {"applications": ["app-b", "app-a"]}}
        step = _step(contains={"data.applications": ["app-a", "app-b"]})
        result, _get, _results = _execute(step, body)
        assert result is True

    def test_contains_fails_on_a_missing_entry(self):
        body = {"data": {"applications": ["app-a"]}}
        step = _step(contains={"data.applications": ["app-a", "app-b"]})
        result, _get, _results = _execute(step, body)
        assert result is False

    def test_contains_refuses_a_non_list(self):
        step = _step(contains={"data.accountId": ["own-account"]})
        result, _get, _results = _execute(step, self._IDENTITY)
        assert result is False

    def test_they_count_as_assertions(self):
        # Without this the "you asserted nothing" guard would reject a step whose
        # only assertion is a negative one.
        _step(not_match={"data.deviceId": "x"})
        _step(contains={"data.applications": ["a"]})


class TestRetries:
    """Retry covers the states no barrier can wait on.

    An application install writes no DAG state, so no hash moves when it lands;
    and a paired device is a member of nothing, so `wait_for_sync` has no group
    state to read from it. Both are "ask again until it is true".
    """

    def _responses(self, bodies):
        made = []
        for body in bodies:
            response = MagicMock()
            response.status_code = 200
            response.content = json.dumps(body).encode()
            response.text = json.dumps(body)
            made.append(response)
        return made

    def _run_with(self, step, bodies):
        auth = MagicMock()
        auth.get_cached_token.return_value = None
        with (
            patch(
                "merobox.commands.bootstrap.steps.base.AuthManager", return_value=auth
            ),
            patch(
                "merobox.commands.bootstrap.steps.api_assertion.requests.get",
                side_effect=self._responses(bodies),
            ) as get,
        ):
            return _run(step.execute({}, {})), get

    def test_passes_on_a_later_attempt(self):
        stub = {"data": {"apps": [{"id": "app-a", "size": 0}]}}
        installed = {"data": {"apps": [{"id": "app-a", "size": 782803}]}}
        step = _step(
            where={"id": "app-a"},
            match={"size": 782803},
            retries=3,
            interval=0.01,
        )
        result, get = self._run_with(step, [stub, stub, installed])
        assert result is True
        assert get.call_count == 3

    def test_stops_at_the_first_success(self):
        installed = {"data": {"apps": [{"id": "app-a", "size": 782803}]}}
        step = _step(
            where={"id": "app-a"}, match={"size": 782803}, retries=5, interval=0.01
        )
        result, get = self._run_with(step, [installed, installed, installed])
        assert result is True
        assert get.call_count == 1

    def test_gives_up_after_the_budget(self):
        stub = {"data": {"apps": [{"id": "app-a", "size": 0}]}}
        step = _step(
            where={"id": "app-a"}, match={"size": 782803}, retries=3, interval=0.01
        )
        result, get = self._run_with(step, [stub, stub, stub])
        assert result is False
        assert get.call_count == 3

    def test_a_single_attempt_is_the_default(self):
        stub = {"data": {"apps": [{"id": "app-a", "size": 0}]}}
        step = _step(where={"id": "app-a"}, match={"size": 782803})
        result, get = self._run_with(step, [stub])
        assert result is False
        assert get.call_count == 1

    @pytest.mark.parametrize("field", ["retries", "interval"])
    def test_a_non_positive_budget_is_a_scenario_bug(self, field):
        with pytest.raises(ValueError, match=field):
            _step(present=["data"], **{field: 0})


class TestConcurrency:
    def test_the_request_does_not_block_the_event_loop(self):
        # `requests` is synchronous, so without the thread hand-off a
        # `parallel:` sibling - including assert_ws_event, whose verdict is a
        # deadline - makes no progress for the length of the request.
        step = _step(present=["data"])
        auth = MagicMock()
        auth.get_cached_token.return_value = None
        response = MagicMock()
        response.status_code = 200
        response.content = json.dumps(_body()).encode()

        def slow_get(*args, **kwargs):
            time.sleep(0.3)
            return response

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        async def both():
            beat = asyncio.ensure_future(ticker())
            try:
                return await step.execute({}, {})
            finally:
                beat.cancel()

        with (
            patch(
                "merobox.commands.bootstrap.steps.base.AuthManager", return_value=auth
            ),
            patch(
                "merobox.commands.bootstrap.steps.api_assertion.requests.get",
                new=slow_get,
            ),
        ):
            assert _run(both()) is True
        # Exactly 0 if the loop was blocked: nothing before the call awaits, so
        # the ticker never starts. A count threshold would only time the runner.
        assert ticks > 0


class TestRequest:
    def test_the_url_is_built_from_the_node_and_the_path(self):
        _, get, _ = _execute(_step(present=["data"]), _body())
        assert get.call_args[0][0] == f"http://localhost:2528{_PATH}"

    def test_a_path_without_a_leading_slash_reaches_the_same_url(self):
        step = _step(path=_PATH.lstrip("/"), present=["data"])
        _, get, _ = _execute(step, _body())
        assert get.call_args[0][0] == f"http://localhost:2528{_PATH}"

    def test_the_path_resolves_placeholders(self):
        step = _step(path="/admin-api/namespaces/{{ns}}/x", present=["data"])
        _, get, _ = _execute(step, _body(), dynamic={"ns": "deadbeef"})
        assert get.call_args[0][0].endswith("/admin-api/namespaces/deadbeef/x")

    def test_a_cached_token_is_attached_as_a_bearer_header(self):
        _, get, _ = _execute(_step(present=["data"]), _body(), token="acc.jwt.tok")
        assert get.call_args[1]["headers"] == {"Authorization": "Bearer acc.jwt.tok"}

    def test_no_cached_token_sends_no_authorization_header(self):
        # A node running without auth issues no token; that is not an error here,
        # the response status is the auth signal.
        _, get, _ = _execute(_step(present=["data"]), _body())
        assert get.call_args[1]["headers"] == {}

    def test_an_explicit_token_overrides_the_cache(self):
        step = _step(present=["data"], token="explicit.jwt")
        _, get, _ = _execute(step, _body(), token="cached.jwt")
        assert get.call_args[1]["headers"] == {"Authorization": "Bearer explicit.jwt"}

    def test_the_raw_body_is_stored_for_later_steps(self):
        _, _, results = _execute(_step(present=["data"]), _body())
        assert results["api_response_calimero-node-1"] == _body()


class TestMatch:
    def test_a_matching_field_passes(self):
        step = _step(match={"data.fleetCompletedAt": 1786635547})
        result, _, _ = _execute(step, _body())
        assert result is True

    def test_a_field_the_typed_client_would_drop_is_still_asserted(self):
        # The whole point: an additive core field lands in the body and the
        # assertion sees it, with no client-py release in the chain.
        step = _step(match={"data.newlyAddedField": "hello"})
        result, _, _ = _execute(step, _body(newlyAddedField="hello"))
        assert result is True

    def test_a_mismatched_value_fails_and_prints_what_arrived(self, capsys):
        step = _step(match={"data.fleetCompletedAt": 1786635547})
        result, _, _ = _execute(step, _body(fleetCompletedAt=1786635548))
        assert result is False
        out = capsys.readouterr().out
        assert "data.fleetCompletedAt: expected 1786635547, got 1786635548" in out
        # The whole body is replayed, so CI output alone diagnoses the failure.
        assert '"allMigrated": true' in out
        assert '"schemaVersion": 2' in out

    def test_a_nested_and_indexed_path_is_matched(self):
        step = _step(match={"data.members.0.report.schemaVersion": 2})
        result, _, _ = _execute(step, _body())
        assert result is True

    def test_a_match_value_resolves_placeholders(self):
        step = _step(match={"data.fleetCompletedAt": "{{stamp}}"})
        body = _body(fleetCompletedAt="42")
        assert _execute(step, body, dynamic={"stamp": "42"})[0] is True

    def test_unlisted_fields_are_ignored(self):
        # A subset test: the body carries four more keys than are asserted.
        step = _step(match={"data.targetVersion": 2})
        result, _, _ = _execute(step, _body())
        assert result is True

    def test_every_missed_path_is_reported_not_just_the_first(self, capsys):
        step = _step(match={"data.targetVersion": 9, "data.fleetCompletedAt": 1})
        result, _, _ = _execute(step, _body())
        assert result is False
        out = capsys.readouterr().out
        assert "missed 2 of 2 assertion(s)" in out
        assert "data.targetVersion" in out
        assert "data.fleetCompletedAt" in out


class TestPresence:
    def test_a_present_key_satisfies_present(self):
        result, _, _ = _execute(_step(present=["data.fleetCompletedAt"]), _body())
        assert result is True

    def test_an_absent_key_fails_present(self, capsys):
        # `skip_serializing_if` omits the key entirely, so this is what a
        # never-completed fleet actually looks like on the wire.
        body = _body()
        del body["data"]["fleetCompletedAt"]
        result, _, _ = _execute(_step(present=["data.fleetCompletedAt"]), body)
        assert result is False
        out = capsys.readouterr().out
        assert (
            "data.fleetCompletedAt: expected the key to be present, it is absent" in out
        )
        assert '"targetVersion": 2' in out, "the body must still be printed"

    def test_an_absent_key_satisfies_absent(self):
        body = _body()
        del body["data"]["fleetCompletedAt"]
        result, _, _ = _execute(_step(absent=["data.fleetCompletedAt"]), body)
        assert result is True

    def test_a_present_key_fails_absent(self, capsys):
        result, _, _ = _execute(_step(absent=["data.fleetCompletedAt"]), _body())
        assert result is False
        out = capsys.readouterr().out
        assert "expected the key to be absent, it is present with 1786635547" in out


class TestNullIsNotAbsent:
    """`skip_serializing_if` makes omission meaningful, so the two must differ."""

    def test_present_and_null_satisfies_present(self):
        result, _, _ = _execute(
            _step(present=["data.fleetCompletedAt"]), _body(fleetCompletedAt=None)
        )
        assert result is True

    def test_present_and_null_violates_absent(self, capsys):
        result, _, _ = _execute(
            _step(absent=["data.fleetCompletedAt"]), _body(fleetCompletedAt=None)
        )
        assert result is False
        assert "it is present with None" in capsys.readouterr().out

    def test_matching_null_passes_on_an_explicit_null(self):
        result, _, _ = _execute(
            _step(match={"data.fleetCompletedAt": None}), _body(fleetCompletedAt=None)
        )
        assert result is True

    def test_matching_null_fails_on_an_absent_key(self, capsys):
        body = _body()
        del body["data"]["fleetCompletedAt"]
        result, _, _ = _execute(_step(match={"data.fleetCompletedAt": None}), body)
        assert result is False
        # The report has to say "absent", not "got None", or the two states read
        # identically in CI - the exact conflation this step exists to remove.
        assert "expected None, but the key is absent" in capsys.readouterr().out


class TestTransportFailures:
    def test_a_non_2xx_fails_and_prints_the_status_and_body(self, capsys):
        result, _, _ = _execute(_step(present=["data"]), "nope", status=404)
        assert result is False
        out = capsys.readouterr().out
        assert "returned HTTP 404" in out
        assert "nope" in out

    def test_another_2xx_status_is_a_served_request(self):
        # The contract is "a non-2xx status fails", so a route answering 201
        # has served the request and its body is what gets asserted on.
        result, _, _ = _execute(_step(present=["data"]), _body(), status=201)
        assert result is True

    def test_a_3xx_status_still_fails(self, capsys):
        result, _, _ = _execute(_step(present=["data"]), _body(), status=302)
        assert result is False
        assert "returned HTTP 302" in capsys.readouterr().out

    def test_a_non_json_body_fails(self, capsys):
        result, _, _ = _execute(_step(present=["data"]), "<html>502</html>")
        assert result is False
        assert "non-JSON body" in capsys.readouterr().out

    def test_a_utf8_body_is_read_from_the_bytes_not_the_guessed_text(self):
        # requests guesses the charset whenever the response omits it, so
        # `.text` can arrive mojibaked while the bytes are intact.
        reason = "quorum perdu à 3 nœuds"
        # ensure_ascii=False, or the body is pure ASCII escapes and both the
        # bytes and the mis-decoded text would agree.
        raw = json.dumps(_body(reason=reason), ensure_ascii=False)
        step = _step(match={"data.reason": reason})
        assert _execute(step, raw, text=raw.encode().decode("latin-1"))[0] is True

    def test_an_unreachable_node_fails_the_step_rather_than_raising(self, capsys):
        step = _step(present=["data"])
        result, _, _ = _execute(
            step, _body(), error=requests.ConnectionError("refused")
        )
        assert result is False
        assert "refused" in capsys.readouterr().out

    def test_a_bug_in_the_step_is_not_reported_as_a_request_failure(self):
        # A TypeError from this step's own plumbing must reach the runner, not
        # `expected_failure`, or a negative test turns green on a defect.
        step = _step(expected_failure=True, present=["data"])
        with pytest.raises(TypeError):
            _execute(step, _body(), error=TypeError("headers is not a mapping"))

    def test_expected_failure_accepts_a_refused_request(self):
        step = _step(expected_failure=True, expected_error="HTTP 403")
        result, _, _ = _execute(step, "forbidden", status=403)
        assert result is True

    def test_expected_error_pins_which_refusal_counts(self, capsys):
        step = _step(expected_failure=True, expected_error="HTTP 403")
        result, _, _ = _execute(step, "missing", status=404)
        assert result is False
        assert "HTTP 403" in capsys.readouterr().out

    def test_a_missed_assertion_is_not_an_expected_failure(self, capsys):
        # `expected_failure` pins the request outcome, never the verdicts: a
        # miss satisfying it would make a typo'd path a green negative test.
        step = _step(expected_failure=True, match={"data.targetVersion": 9})
        result, _, _ = _execute(step, _body())
        assert result is False
        assert "missed 1 of 1 assertion(s)" in capsys.readouterr().out

    def test_an_unresolved_placeholder_is_reported_as_this_step_s_failure(self, capsys):
        # It fails either way, but through the step's own report rather than
        # as an exception the executor reformats, matching assert/json_assert.
        step = _step(match={"data.targetVersion": "{{never_bound}}"})
        result, _, _ = _execute(step, _body())
        assert result is False
        assert "✗ assert_api_response on calimero-node-1" in capsys.readouterr().out

    def test_an_unresolved_placeholder_in_the_path_is_reported_too(self, capsys):
        step = _step(path="/admin-api/{{never_bound}}/x", present=["data"])
        result, _, _ = _execute(step, _body())
        assert result is False
        assert "✗ assert_api_response on calimero-node-1" in capsys.readouterr().out

    def test_expected_failure_fails_when_the_request_succeeds(self, capsys):
        result, _, _ = _execute(_step(expected_failure=True), _body())
        assert result is False
        assert (
            "expected_failure was set but the step succeeded" in capsys.readouterr().out
        )


class TestValidation:
    def test_node_and_path_are_required(self):
        with pytest.raises(ValueError):
            AssertApiResponseStep(
                {"type": "assert_api_response", "node": "calimero-node-1"},
                manager=_manager(),
            )

    def test_a_step_with_no_assertion_is_rejected(self):
        with pytest.raises(ValueError, match="asserts nothing"):
            _step()

    def test_a_negative_test_may_assert_nothing_but_the_refusal(self):
        _step(expected_failure=True, expected_error="HTTP 404")

    def test_an_empty_path_entry_is_rejected(self):
        with pytest.raises(ValueError, match="non-empty dotted paths"):
            _step(match={"": 1})

    def test_a_non_string_present_entry_is_rejected(self):
        with pytest.raises(ValueError):
            _step(present=[1])

    def test_match_must_be_a_dict(self):
        with pytest.raises(ValueError, match="match"):
            _step(match=["data"])
