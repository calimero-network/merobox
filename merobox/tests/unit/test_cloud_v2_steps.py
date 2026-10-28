"""Step dispatch against a fake client: which call a step makes, and how a
`412 CONFIRM_REQUIRED` is answered. No cluster, no calimero_client_py."""

import pytest

from merobox.cloud_v2.workflow import Runner, WorkflowError


class FakeError(Exception):
    pass


class FakeClient:
    def __init__(self, confirm=()):
        self.calls = []
        self.confirm = set(confirm)

    def _answer(self, method, path, body=None):
        self.calls.append((method, path, body))
        if (method, path) in self.confirm:
            raise FakeError(412, "confirm", "CONFIRM_REQUIRED")
        return {"ok": method}

    def get(self, path):
        return self._answer("GET", path)

    def post(self, path, body=None):
        return self._answer("POST", path, body)

    def put(self, path, body):
        return self._answer("PUT", path, body)

    def delete(self, path):
        return self._answer("DELETE", path)

    def post_confirmed(self, path, body, passkey):
        self.calls.append(("POST+confirm", path, passkey))
        return {"ok": "confirmed"}

    def delete_confirmed(self, path, passkey):
        self.calls.append(("DELETE+confirm", path, passkey))
        return {"ok": "confirmed"}


def runner(client):
    r = Runner.__new__(Runner)
    r._CloudError = FakeError
    r.passkeys = {"alice": "alice-pk"}
    r.by_key = {"k1": "alice"}
    r._with_client = lambda step, f: f(client)
    r.cluster = None
    return r


def test_an_http_put_is_sent_as_a_put():
    cl = FakeClient()
    runner(cl)._run_one(
        {
            "type": "http",
            "method": "PUT",
            "path": "/auth/aliases/a",
            "body": {"app": "x"},
            "account": "alice",
        }
    )
    assert cl.calls == [("PUT", "auth/aliases/a", {"app": "x"})]


def test_an_http_delete_the_node_asks_to_confirm_is_answered_with_the_passkey():
    cl = FakeClient(confirm={("DELETE", "orgs/o1")})
    out = runner(cl)._run_one(
        {"type": "http", "method": "delete", "path": "orgs/o1", "account": "alice"}
    )
    assert out == {"ok": "confirmed"}
    assert cl.calls[-1] == ("DELETE+confirm", "orgs/o1", "alice-pk")


def test_a_step_acting_by_key_confirms_with_that_accounts_passkey():
    cl = FakeClient(confirm={("POST", "apps/a/upgrade")})
    runner(cl)._run_one(
        {"type": "upgrade_app", "app": "a", "version": "2", "as_key": "k1"}
    )
    assert cl.calls[-1] == ("POST+confirm", "apps/a/upgrade", "alice-pk")


def test_an_upgrade_names_another_package_and_service():
    cl = FakeClient()
    runner(cl)._run_one(
        {
            "type": "upgrade_app",
            "app": "a",
            "package": "com.x",
            "service": "svc",
            "version": "2",
            "account": "alice",
        }
    )
    assert cl.calls == [
        (
            "POST",
            "apps/a/upgrade",
            {"version": "2", "package": "com.x", "service": "svc"},
        )
    ]


def test_an_unknown_http_method_is_refused_not_sent_as_a_post():
    cl = FakeClient()
    with pytest.raises(WorkflowError, match="unsupported method"):
        runner(cl)._run_one(
            {"type": "http", "method": "patch", "path": "x", "account": "alice"}
        )
    assert cl.calls == []
