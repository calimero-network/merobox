"""The client/node version-skew hint on otherwise opaque errors."""

from merobox.commands.result import explain_client_error, fail


class TestClientDecodeHint:
    def test_decode_failure_gets_a_cause_hint(self):
        # The node ACCEPTS the request and logs success; only the client's
        # parse of the response fails. Without a hint this reads like a node
        # fault and sends people into node logs that show it working.
        msg = explain_client_error("Client error: error decoding response body")
        assert "error decoding response body" in msg, "must keep the original text"
        assert "calimero-client-py" in msg
        assert "older than the node" in msg

    def test_unrelated_errors_are_untouched(self):
        for msg in [
            "Connection refused",
            "Invalid invitation JSON: key must be a string at line 1 column 2",
            "",
        ]:
            assert explain_client_error(msg) == msg

    def test_hint_reaches_the_message_steps_actually_print(self):
        # Steps render exception["message"], so the hint has to survive fail()
        # rather than only existing in the helper.
        result = fail(
            "join_namespace failed",
            error=RuntimeError("Client error: error decoding response body"),
        )
        assert "calimero-client-py" in result["exception"]["message"]
