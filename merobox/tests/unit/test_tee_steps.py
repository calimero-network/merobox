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
    ZERO_MEASUREMENT,
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

    @pytest.mark.parametrize("mode", ["replica", "relay"])
    def test_valid_mode_passes(self, mode):
        SetTeeAdmissionPolicyStep(
            {
                "type": "set_tee_admission_policy",
                "node": "node-1",
                "group_id": "g",
                "mode": mode,
            },
            manager=MagicMock(),
        )

    @pytest.mark.parametrize("mode", ["Relay", "relayer", "", None, True])
    def test_invalid_mode_raises(self, mode):
        with pytest.raises(
            ValueError, match="'mode' must be one of 'replica', 'relay'"
        ):
            SetTeeAdmissionPolicyStep(
                {
                    "type": "set_tee_admission_policy",
                    "node": "node-1",
                    "group_id": "g",
                    "mode": mode,
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
        # Defaulted, unlike RTMR0: core requires at least one value for each of
        # RTMR3 (rc.42) and RTMR1/RTMR2 (rc.45, core#4062), so an empty default
        # is a 400 on every workflow that takes this step rather than a
        # permissive policy.
        assert body["allowedRtmr1"] == [ZERO_MEASUREMENT]
        assert body["allowedRtmr2"] == [ZERO_MEASUREMENT]
        assert body["allowedRtmr3"] == [ZERO_MEASUREMENT]
        assert body["allowedRtmr3"] == ["0" * 96]
        assert body["allowedTcbStatuses"] == []

    @pytest.mark.parametrize("field", ["allowed_rtmr1", "allowed_rtmr2"])
    def test_rtmr1_and_rtmr2_defaults_can_still_be_overridden(self, field):
        step = _make_step(
            SetTeeAdmissionPolicyStep,
            type="set_tee_admission_policy",
            group_id="gid",
            **{field: ["dd" * 48]},
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(200, {})
            assert _run(step.execute({}, {})) is True

        wire = "allowedRtmr" + field[-1]
        assert req.request.call_args.kwargs["json"][wire] == ["dd" * 48]

    def test_rtmr3_default_can_still_be_overridden(self):
        """The default must not become a floor.

        A workflow pinning a real image's RTMR3 has to get exactly that, or the
        all-zero mock value would silently widen a policy meant to be narrow.
        """
        step = _make_step(
            SetTeeAdmissionPolicyStep,
            type="set_tee_admission_policy",
            group_id="gid",
            allowed_rtmr3=["cc" * 48],
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(200, {})
            assert _run(step.execute({}, {})) is True

        body = req.request.call_args.kwargs["json"]
        assert body["allowedRtmr3"] == ["cc" * 48]
        assert ZERO_MEASUREMENT not in body["allowedRtmr3"]

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

    def test_mode_omitted_sends_no_mode(self):
        """Core before 0.11.0-rc.62 rejects a body carrying `mode` at all."""
        step = _make_step(
            SetTeeAdmissionPolicyStep,
            type="set_tee_admission_policy",
            group_id="gid",
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(200, {})
            assert _run(step.execute({}, {})) is True

        assert "mode" not in req.request.call_args.kwargs["json"]

    @pytest.mark.parametrize("mode", ["replica", "relay"])
    def test_mode_is_sent_when_set(self, mode):
        step = _make_step(
            SetTeeAdmissionPolicyStep,
            type="set_tee_admission_policy",
            group_id="gid",
            mode=mode,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(200, {})
            assert _run(step.execute({}, {})) is True

        body = req.request.call_args.kwargs["json"]
        assert body["mode"] == mode
        assert f'"mode": "{mode}"' in json.dumps(body)

    def test_mode_rejected_by_old_node_names_the_version(self, capsys):
        step = _make_step(
            SetTeeAdmissionPolicyStep,
            type="set_tee_admission_policy",
            group_id="gid",
            mode="relay",
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                400, text="unknown field `mode`, expected one of `allowedMrtd`"
            )
            assert _run(step.execute({}, {})) is False
        assert "0.11.0-rc.62" in capsys.readouterr().out

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
        # The admin API deserializes camelCase (serde rename_all = "camelCase"),
        # so the fleet-join body field must be `groupId`, not `group_id`.
        assert req.request.call_args.kwargs["json"] == {"groupId": "gid"}
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

    def test_waits_as_long_as_core_client_does(self):
        # Core's client gives fleet-join 3 min (FLEET_JOIN_REQUEST_TIMEOUT,
        # core#4455): the node answers only after a direct admission request
        # (~35 s), the admission window (30 s) and its context joins. A shorter
        # read timeout gives up on a join the node is still completing, which
        # the 60 s this step used to wait could do.
        step = _make_step(TeeFleetJoinStep, type="tee_fleet_join", group_id="gid")
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(200, {"admitted": True})
            _run(step.execute({}, {}))

        _connect, read = req.request.call_args.kwargs["timeout"]
        assert read >= 180

    def test_admitter_addrs_are_sent_camel_case(self):
        addr = "/ip4/10.0.0.1/tcp/2428/p2p/12D3KooWRelay"
        step = _make_step(
            TeeFleetJoinStep,
            type="tee_fleet_join",
            group_id="gid",
            admitter_addrs=[addr],
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(200, {"admitted": True})
            assert _run(step.execute({}, {})) is True
        assert req.request.call_args.kwargs["json"] == {
            "groupId": "gid",
            "admitterAddrs": [addr],
        }

    def test_admitter_nodes_resolve_to_loopback_from_config(self, tmp_path):
        from merobox.commands.binary_manager import BinaryManager

        config = tmp_path / "config.toml"
        config.write_text(
            '[identity]\npeer_id = "12D3KooWRelay"\n\n'
            '[swarm]\nlisten = ["/ip4/0.0.0.0/udp/7380/quic-v1", '
            '"/ip4/0.0.0.0/tcp/7380"]\n'
        )
        manager = MagicMock(spec=BinaryManager)
        manager.node_config_files = {"relay": str(config)}
        step = _make_step(
            TeeFleetJoinStep,
            type="tee_fleet_join",
            group_id="gid",
            admitter_nodes=["relay"],
        )
        step.manager = manager
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(200, {"admitted": True})
            assert _run(step.execute({}, {})) is True
        assert req.request.call_args.kwargs["json"]["admitterAddrs"] == [
            "/ip4/127.0.0.1/tcp/7380/p2p/12D3KooWRelay"
        ]

    def test_admitter_nodes_outside_binary_mode_fail_without_calling(self):
        step = _make_step(
            TeeFleetJoinStep,
            type="tee_fleet_join",
            group_id="gid",
            admitter_nodes=["relay"],
        )
        with patch(f"{_MODULE}.requests") as req:
            assert _run(step.execute({}, {})) is False
        req.request.assert_not_called()

    def test_admitter_addrs_must_be_a_list_of_strings(self):
        with pytest.raises(ValueError, match="admitter_addrs"):
            TeeFleetJoinStep(
                {
                    "type": "tee_fleet_join",
                    "node": "node-1",
                    "group_id": "g",
                    "admitter_addrs": "/ip4/1.2.3.4/tcp/1/p2p/x",
                },
                manager=MagicMock(),
            )


# =============================================================================
# AssertTeeMemberStep / AssertNotMemberStep
# =============================================================================

# An ACCOUNT, not a signing key: the member listing reports accounts, and
# passing a key is precisely the mistake that made ten TEE scenarios red.
_TEE_ACCOUNT = "aa" * 32


def _members_payload(*members):
    return {"members": list(members)}


class TestAssertTeeMemberValidation:
    @pytest.mark.parametrize(
        "role", ["Admin", "Member", "ReadOnly", "ReadOnlyTee", "RelayTee", "{{r}}"]
    )
    def test_known_role_passes(self, role):
        AssertTeeMemberStep(
            {
                "type": "assert_tee_member",
                "node": "node-1",
                "group_id": "g",
                "account": _TEE_ACCOUNT,
                "role": role,
            },
            manager=MagicMock(),
        )

    def test_unknown_role_raises(self):
        with pytest.raises(ValueError, match="'role' must be one of"):
            AssertTeeMemberStep(
                {
                    "type": "assert_tee_member",
                    "node": "node-1",
                    "group_id": "g",
                    "account": _TEE_ACCOUNT,
                    "role": "RelayTEE",
                },
                manager=MagicMock(),
            )


class TestAssertTeeMember:
    def test_passes_when_member_present_with_default_role(self):
        step = _make_step(
            AssertTeeMemberStep,
            type="assert_tee_member",
            group_id="gid",
            account=_TEE_ACCOUNT,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload(
                    {"identity": "bb" * 32, "role": "Admin"},
                    {"identity": _TEE_ACCOUNT, "role": "ReadOnlyTee"},
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
            account=_TEE_ACCOUNT,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200, _members_payload({"identity": "bb" * 32, "role": "Admin"})
            )
            result = _run(step.execute({}, {}))
        assert result is False

    def test_fails_when_role_mismatches(self):
        step = _make_step(
            AssertTeeMemberStep,
            type="assert_tee_member",
            group_id="gid",
            account=_TEE_ACCOUNT,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload({"identity": _TEE_ACCOUNT, "role": "Member"}),
            )
            result = _run(step.execute({}, {}))
        assert result is False

    def test_default_role_accepts_relay_tee(self):
        step = _make_step(
            AssertTeeMemberStep,
            type="assert_tee_member",
            group_id="gid",
            account=_TEE_ACCOUNT,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload({"identity": _TEE_ACCOUNT, "role": "RelayTee"}),
            )
            assert _run(step.execute({}, {})) is True

    def test_relay_tee_role_respected(self):
        step = _make_step(
            AssertTeeMemberStep,
            type="assert_tee_member",
            group_id="gid",
            account=_TEE_ACCOUNT,
            role="RelayTee",
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload({"identity": _TEE_ACCOUNT, "role": "RelayTee"}),
            )
            assert _run(step.execute({}, {})) is True

    def test_explicit_role_does_not_accept_the_other_tee_role(self):
        """Pinning a role must tell a replica from a relay."""
        step = _make_step(
            AssertTeeMemberStep,
            type="assert_tee_member",
            group_id="gid",
            account=_TEE_ACCOUNT,
            role="RelayTee",
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload({"identity": _TEE_ACCOUNT, "role": "ReadOnlyTee"}),
            )
            assert _run(step.execute({}, {})) is False

    def test_custom_role_respected(self):
        step = _make_step(
            AssertTeeMemberStep,
            type="assert_tee_member",
            group_id="gid",
            account=_TEE_ACCOUNT,
            role="Member",
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload({"identity": _TEE_ACCOUNT, "role": "Member"}),
            )
            result = _run(step.execute({}, {}))
        assert result is True


class TestAssertNotMember:
    def test_passes_when_absent(self):
        step = _make_step(
            AssertNotMemberStep,
            type="assert_not_member",
            group_id="gid",
            account=_TEE_ACCOUNT,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200, _members_payload({"identity": "bb" * 32, "role": "Admin"})
            )
            result = _run(step.execute({}, {}))
        assert result is True

    def test_fails_when_present(self):
        step = _make_step(
            AssertNotMemberStep,
            type="assert_not_member",
            group_id="gid",
            account=_TEE_ACCOUNT,
        )
        with patch(f"{_MODULE}.requests") as req:
            req.request.return_value = _response(
                200,
                _members_payload({"identity": _TEE_ACCOUNT, "role": "ReadOnlyTee"}),
            )
            result = _run(step.execute({}, {}))
        assert result is False
