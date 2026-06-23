"""
Unit tests for the TEE (mock-fleet) workflow steps.

Covers SetTeeAdmissionPolicyStep, TeeFleetJoinStep, AssertTeeMemberStep, and
AssertNotMemberStep — validation plus execute. The admin API is reached over
raw HTTP, so ``requests.request`` is mocked at the module level
(``merobox.commands.bootstrap.steps.tee.requests``) and the node→admin-url
resolution is stubbed.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.tee import (
    ZERO_MRTD,
    AssertNotMemberStep,
    AssertTeeMemberStep,
    SetTeeAdmissionPolicyStep,
    TeeFleetJoinStep,
)

_MODULE = "merobox.commands.bootstrap.steps.tee"
_ADMIN_URL = "http://localhost:9180"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _response(status_code=200, payload=None, text=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text if text is not None else json.dumps(payload or {})
    return resp


def _make_step(cls, **overrides):
    cfg = {"type": overrides.pop("type", "tee"), "node": "node-1"}
    cfg.update(overrides)
    step = cls(cfg, manager=MagicMock())
    # Identity dynamic-value resolution + a fixed admin URL, like other tests.
    step._resolve_dynamic_value = lambda v, *_a, **_k: v
    step._get_node_rpc_url = lambda _n: _ADMIN_URL
    return step


# =============================================================================
# SetTeeAdmissionPolicyStep
# =============================================================================


class TestSetTeeAdmissionPolicyValidation:
    def test_valid_config_passes(self):
        SetTeeAdmissionPolicyStep(
            {"type": "set_tee_admission_policy", "node": "node-1", "group_id": "g"},
            manager=MagicMock(),
        )

    def test_missing_group_id_raises(self):
        with pytest.raises(ValueError, match="group_id"):
            SetTeeAdmissionPolicyStep(
                {"type": "set_tee_admission_policy", "node": "node-1"},
                manager=MagicMock(),
            )

    def test_accept_mock_not_bool_raises(self):
        with pytest.raises(ValueError, match="accept_mock"):
            SetTeeAdmissionPolicyStep(
                {
                    "type": "set_tee_admission_policy",
                    "node": "node-1",
                    "group_id": "g",
                    "accept_mock": "yes",
                },
                manager=MagicMock(),
            )

    def test_allowed_mrtd_not_list_raises(self):
        with pytest.raises(ValueError, match="allowed_mrtd"):
            SetTeeAdmissionPolicyStep(
                {
                    "type": "set_tee_admission_policy",
                    "node": "node-1",
                    "group_id": "g",
                    "allowed_mrtd": "deadbeef",
                },
                manager=MagicMock(),
            )


class TestSetTeeAdmissionPolicyExecute:
    def test_default_body_accepts_mock_and_zero_mrtd(self):
        step = _make_step(
            SetTeeAdmissionPolicyStep,
            type="set_tee_admission_policy",
            group_id="gid",
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(200, {})
            result = _run(step.execute({}, {}))

        assert result is True
        method, url = req.request.call_args.args
        assert method == "PUT"
        assert url == (
            f"{_ADMIN_URL}/admin-api/groups/gid/settings/tee-admission-policy"
        )
        body = req.request.call_args.kwargs["json"]
        assert body["acceptMock"] is True
        assert body["allowedMrtd"] == [ZERO_MRTD]
        assert body["allowedMrtd"] == ["0" * 96]
        assert body["allowedRtmr0"] == []
        assert body["allowedRtmr1"] == []
        assert body["allowedRtmr2"] == []
        assert body["allowedRtmr3"] == []
        assert body["allowedTcbStatuses"] == []

    def test_overrides_are_respected(self):
        step = _make_step(
            SetTeeAdmissionPolicyStep,
            type="set_tee_admission_policy",
            group_id="gid",
            accept_mock=False,
            allowed_mrtd=["aa" * 48],
            allowed_rtmr0=["bb"],
            allowed_tcb_statuses=["UpToDate"],
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(200, {})
            result = _run(step.execute({}, {}))

        assert result is True
        body = req.request.call_args.kwargs["json"]
        assert body["acceptMock"] is False
        assert body["allowedMrtd"] == ["aa" * 48]
        assert body["allowedRtmr0"] == ["bb"]
        assert body["allowedTcbStatuses"] == ["UpToDate"]

    def test_non_200_fails(self):
        step = _make_step(
            SetTeeAdmissionPolicyStep,
            type="set_tee_admission_policy",
            group_id="gid",
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(400, text="bad policy")
            result = _run(step.execute({}, {}))
        assert result is False


# =============================================================================
# TeeFleetJoinStep
# =============================================================================


class TestTeeFleetJoinValidation:
    def test_valid_config_passes(self):
        TeeFleetJoinStep(
            {"type": "tee_fleet_join", "node": "node-1", "group_id": "g"},
            manager=MagicMock(),
        )

    def test_missing_group_id_raises(self):
        with pytest.raises(ValueError, match="group_id"):
            TeeFleetJoinStep(
                {"type": "tee_fleet_join", "node": "node-1"}, manager=MagicMock()
            )


class TestTeeFleetJoinExecute:
    def test_runs_fleet_join_and_parses_admitted_true(self):
        step = _make_step(TeeFleetJoinStep, type="tee_fleet_join", group_id="gid")
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200, {"status": "joined", "admitted": True, "group_id": "gid"}
            )
            workflow_results = {}
            result = _run(step.execute(workflow_results, {}))

        assert result is True
        method, url = req.request.call_args.args
        assert method == "POST"
        assert url == f"{_ADMIN_URL}/admin-api/tee/fleet-join"
        assert req.request.call_args.kwargs["json"] == {"group_id": "gid"}
        assert workflow_results["tee_fleet_join_admitted_node-1"] is True
        assert workflow_results["tee_fleet_join_node-1"]["admitted"] is True

    def test_parses_admitted_false_when_only_announced(self):
        step = _make_step(TeeFleetJoinStep, type="tee_fleet_join", group_id="gid")
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200, {"status": "announced", "admitted": False}
            )
            workflow_results = {}
            result = _run(step.execute(workflow_results, {}))

        # Step itself succeeds (the call worked); admitted flag is just False.
        assert result is True
        assert workflow_results["tee_fleet_join_admitted_node-1"] is False

    def test_non_200_fails(self):
        step = _make_step(TeeFleetJoinStep, type="tee_fleet_join", group_id="gid")
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(500, text="boom")
            result = _run(step.execute({}, {}))
        assert result is False


# =============================================================================
# AssertTeeMemberStep / AssertNotMemberStep
# =============================================================================

_TEE_IDENTITY = "tee-pubkey-1"


def _members_payload(*members):
    return {"members": list(members), "selfIdentity": "owner-key"}


class TestAssertTeeMember:
    def test_passes_when_member_present_with_default_role(self):
        step = _make_step(
            AssertTeeMemberStep,
            type="assert_tee_member",
            group_id="gid",
            identity=_TEE_IDENTITY,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload(
                    {"identity": "owner-key", "role": "Admin"},
                    {"identity": _TEE_IDENTITY, "role": "ReadOnlyTee"},
                ),
            )
            result = _run(step.execute({}, {}))

        assert result is True
        method, url = req.request.call_args.args
        assert method == "GET"
        assert url == f"{_ADMIN_URL}/admin-api/groups/gid/members"

    def test_fails_when_member_absent(self):
        step = _make_step(
            AssertTeeMemberStep,
            type="assert_tee_member",
            group_id="gid",
            identity=_TEE_IDENTITY,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200, _members_payload({"identity": "owner-key", "role": "Admin"})
            )
            result = _run(step.execute({}, {}))
        assert result is False

    def test_fails_when_role_mismatches(self):
        step = _make_step(
            AssertTeeMemberStep,
            type="assert_tee_member",
            group_id="gid",
            identity=_TEE_IDENTITY,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload({"identity": _TEE_IDENTITY, "role": "Member"}),
            )
            result = _run(step.execute({}, {}))
        assert result is False

    def test_custom_role_respected(self):
        step = _make_step(
            AssertTeeMemberStep,
            type="assert_tee_member",
            group_id="gid",
            identity=_TEE_IDENTITY,
            role="Member",
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload({"identity": _TEE_IDENTITY, "role": "Member"}),
            )
            result = _run(step.execute({}, {}))
        assert result is True


class TestAssertNotMember:
    def test_passes_when_absent(self):
        step = _make_step(
            AssertNotMemberStep,
            type="assert_not_member",
            group_id="gid",
            identity=_TEE_IDENTITY,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200, _members_payload({"identity": "owner-key", "role": "Admin"})
            )
            result = _run(step.execute({}, {}))
        assert result is True

    def test_fails_when_present(self):
        step = _make_step(
            AssertNotMemberStep,
            type="assert_not_member",
            group_id="gid",
            identity=_TEE_IDENTITY,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload({"identity": _TEE_IDENTITY, "role": "ReadOnlyTee"}),
            )
            result = _run(step.execute({}, {}))
        assert result is False
