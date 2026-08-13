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
from unittest.mock import MagicMock, patch

import pytest

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


def _execute(step, payload, status=200, token=None, workflow_results=None):
    """Run the step against a canned response, returning (result, get, results)."""
    auth = MagicMock()
    auth.get_cached_token.return_value = (
        MagicMock(access_token=token) if token else None
    )
    response = MagicMock()
    response.status_code = status
    response.text = payload if isinstance(payload, str) else json.dumps(payload)

    results = workflow_results if workflow_results is not None else {}
    with (
        patch("merobox.commands.bootstrap.steps.base.AuthManager", return_value=auth),
        patch(
            "merobox.commands.bootstrap.steps.api_assertion.requests.get",
            return_value=response,
        ) as get,
    ):
        return _run(step.execute(results, {})), get, results


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
        auth = MagicMock()
        auth.get_cached_token.return_value = None
        response = MagicMock()
        response.status_code = 200
        response.text = json.dumps(_body())
        with (
            patch(
                "merobox.commands.bootstrap.steps.base.AuthManager", return_value=auth
            ),
            patch(
                "merobox.commands.bootstrap.steps.api_assertion.requests.get",
                return_value=response,
            ) as get,
        ):
            _run(step.execute({}, {"ns": "deadbeef"}))
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
        auth = MagicMock()
        auth.get_cached_token.return_value = None
        response = MagicMock()
        response.status_code = 200
        response.text = json.dumps(_body(fleetCompletedAt="42"))
        with (
            patch(
                "merobox.commands.bootstrap.steps.base.AuthManager", return_value=auth
            ),
            patch(
                "merobox.commands.bootstrap.steps.api_assertion.requests.get",
                return_value=response,
            ),
        ):
            assert _run(step.execute({}, {"stamp": "42"})) is True

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

    def test_expected_failure_accepts_a_refused_request(self):
        step = _step(expected_failure=True, expected_error="HTTP 403")
        result, _, _ = _execute(step, "forbidden", status=403)
        assert result is True

    def test_expected_error_pins_which_refusal_counts(self, capsys):
        step = _step(expected_failure=True, expected_error="HTTP 403")
        result, _, _ = _execute(step, "missing", status=404)
        assert result is False
        assert "HTTP 403" in capsys.readouterr().out

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
