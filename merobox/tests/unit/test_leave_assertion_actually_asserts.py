"""A captured `call` error carries a verdict an `assert` step can check.

The leave/rejoin workflows capture `error` from a refused read and assert
`is_set` on it, and `allow_failure` probes bind the same fields as None when the
call succeeds. These pin the export/`is_set` round trip in all four directions,
including the `error` vs `error_message` choice: a JSON-RPC error carrying
neither `message` nor `data` leaves `error_message` None.
"""

from merobox.commands.bootstrap.steps.assertion import AssertStep
from merobox.commands.bootstrap.steps.execute import ExecuteStep


def _execute_step(**extra):
    config = {
        "type": "call",
        "name": "node 2 cannot read the context it left",
        "node": "calimero-node-1",
        "context_id": "ctx1",
        "method": "get",
        "args": {"key": "msg_1"},
        "expected_failure": True,
        "outputs": {"left_err": "error"},
    }
    config.update(extra)
    return ExecuteStep(config, manager=None)


def _is_set(dynamic_values):
    step = AssertStep(
        {"type": "assert", "name": "guard", "statements": ["is_set({{left_err}})"]},
        manager=None,
    )
    passed, _detail = step._eval_statement("is_set({{left_err}})", {}, dynamic_values)
    return passed


def _export(error_info):
    dynamic_values = {}
    _execute_step()._export_error_variables(
        error_info, "calimero-node-1", dynamic_values
    )
    return dynamic_values


class TestTheCapturedErrorAsserts:
    """The captured error is what carries the verdict, not the step's own."""

    def test_jsonrpc_failure_binds_the_error_and_the_guard_passes(self):
        step = _execute_step()
        info = step._extract_error_info(
            {
                "error": {
                    "type": "FunctionCallError",
                    "code": -32000,
                    "message": "no identity",
                }
            },
            expected=True,
        )
        dynamic_values = _export(info)
        assert dynamic_values["left_err"] is not None
        assert _is_set(dynamic_values) is True

    def test_transport_failure_binds_the_error_too(self):
        """A refused read may never reach JSON-RPC; the guard must still pass."""
        step = _execute_step()
        info = step._extract_error_info(
            {"success": False, "error": "HTTP 500 Internal Server Error"}, expected=True
        )
        dynamic_values = _export(info)
        assert dynamic_values["left_err"] == "HTTP 500 Internal Server Error"
        assert _is_set(dynamic_values) is True

    def test_error_survives_a_jsonrpc_error_with_no_message(self):
        """Why the capture is `error` and not `error_message`.

        `_extract_jsonrpc_error_details` leaves `error_message` None when the
        error object carries neither `message` nor `data`. Guarding on that
        field would fail the run on a leave that worked — a false red reading
        as "the leave did not take effect". `error` is seeded in every failure
        branch, so it is the field that can carry the verdict.
        """
        step = _execute_step()
        info = step._extract_error_info(
            {"error": {"type": "FunctionCallError"}}, expected=True
        )
        assert info["error_message"] is None, "premise of this test no longer holds"
        assert info["error"] == {"type": "FunctionCallError"}
        assert _is_set(_export(info)) is True

    def test_unexpected_success_binds_none_and_the_guard_fails(self):
        """The case the whole file exists for.

        An `allow_failure` call that succeeds binds None, so the guard fails.
        """
        lenient_success = {
            "success": False,
            "expected": True,
            "error_code": None,
            "error_type": None,
            "error_message": None,
            "error": None,
        }
        dynamic_values = _export(lenient_success)
        assert "left_err" in dynamic_values, "the capture must bind even when None"
        assert dynamic_values["left_err"] is None
        assert _is_set(dynamic_values) is False
