"""`merobox cloud port`: how single v1 steps become release-v2 steps."""

from merobox.cloud_v2.port import Porter


def porter() -> Porter:
    return Porter({"nodes": {"count": 2, "prefix": "n"}, "steps": []}, {}, 3000)


def test_blob_steps_keep_their_node_file_and_outputs():
    p = porter()
    up, down = p.port_steps(
        [
            {
                "type": "upload_blob",
                "node": "n-1",
                "context_id": "{{ctx}}",
                "file_path": "workflows/a.pdf",
                "outputs": {"b": "blob_id"},
            },
            {
                "type": "download_blob",
                "node": "n-2",
                "blob_id": "{{b}}",
                "expected_size": 3,
                "expected_sha256": "{{h}}",
            },
        ]
    )
    assert up == {
        "type": "upload_blob",
        "account": "n-1",
        "app": "{{ctx}}",
        "file_path": "workflows/a.pdf",
        "outputs": {"b": "blob_id"},
    }
    # Without a context, the mesh's app.
    assert down["app"] == "{{context_id}}"
    assert down["expected_size"] == 3 and down["expected_sha256"] == "{{h}}"
    assert not p.dropped


def test_a_node_local_miss_has_no_cluster_counterpart():
    p = porter()
    assert (
        p.port_steps(
            [
                {
                    "type": "download_blob",
                    "node": "n-2",
                    "blob_id": "{{b}}",
                    "expected_failure": True,
                }
            ]
        )
        == []
    )
    assert p.dropped["download_blob (a node-local miss)"] == 1


def test_waits_keep_v1s_duration():
    # A name cannot tell a wait for peers from one for a lease to run out:
    # hyperfeed's 18 s lease failed when every wait was cut to a second.
    sync, lease = porter().port_steps(
        [
            {"type": "wait", "name": "Let node-2 sync", "seconds": 10},
            {"type": "wait", "name": "Settle the feed", "seconds": 18},
        ]
    )
    assert sync["seconds"] == 10.0
    assert lease["seconds"] == 18.0


def test_an_open_invitation_joins_the_context_it_names():
    p = porter()
    steps = p.port_steps(
        [
            {
                "type": "invite_open",
                "node": "n-1",
                "context_id": "{{ctx}}",
                "outputs": {"inv": "invitation"},
            },
            {
                "type": "join_open",
                "node": "n-2",
                "invitation": "{{inv}}",
                "outputs": {"c2": "contextId", "k2": "memberPublicKey"},
            },
        ]
    )
    assert steps[0] == {"type": "set", "values": {"inv": "{{ctx}}"}}
    assert steps[1] == {
        "type": "whoami",
        "account": "n-2",
        "outputs": {"k2": "publicKey"},
    }
    assert steps[2] == {"type": "set", "values": {"c2": "{{ctx}}"}}


def test_a_paired_device_is_another_passkey_of_the_account():
    p = porter()
    pair, call = p.port_steps(
        [
            {
                "type": "account_pair",
                "node": "n-2",
                "holder": "n-1",
                "outputs": {"ok": "keyDelivered"},
            },
            {"type": "call", "node": "n-2", "context_id": "{{c}}", "method": "m"},
        ]
    )
    assert pair == {
        "type": "add_device",
        "account": "n-1",
        "device": "n-2",
        "outputs": {"ok": "keyDelivered"},
    }
    # The paired node acts with its own session, on the holder's account.
    assert call["account"] == "n-2"


def test_a_fuzzy_soak_keeps_its_operations_as_accounts():
    (step,) = porter().port_steps(
        [
            {
                "type": "fuzzy_test",
                "duration_minutes": 15,
                "nodes": [{"name": "n-1"}, {"name": "n-2"}],
                "operations": [
                    {
                        "name": "set",
                        "weight": 3,
                        "steps": [
                            {
                                "type": "call",
                                "node": "{{random_node}}",
                                "context_id": "{{c}}",
                                "method": "set",
                                "args": {"key": "k_{{random_int(1, 9)}}"},
                            }
                        ],
                    }
                ],
            }
        ]
    )
    assert step["type"] == "fuzzy_test" and step["accounts"] == ["n-1", "n-2"]
    (call,) = step["operations"][0]["steps"]
    assert call["account"] == "{{random_account}}" and call["app"] == "{{c}}"


def test_reads_during_a_v1_partition_are_not_asserted():
    p = porter()
    steps = p.port_steps(
        [
            {"type": "stop_node", "node": "n-1"},
            {"type": "call", "node": "n-2", "context_id": "{{c}}", "method": "w"},
            {
                "type": "call",
                "node": "n-2",
                "context_id": "{{c}}",
                "method": "r",
                "outputs": {"seen": "result"},
            },
            {"type": "assert", "statements": ["not_contains({{seen}}, 'x')"]},
            {"type": "start_node", "node": "n-1"},
            {
                "type": "call",
                "node": "n-1",
                "context_id": "{{c}}",
                "method": "r",
                "outputs": {"after": "result"},
            },
            {"type": "assert", "statements": ["contains({{after}}, 'x')"]},
        ]
    )
    kinds = [s["type"] for s in steps]
    assert kinds == ["call", "call", "call", "assert"], kinds
    # Writes made then may be refused where the other side's are seen.
    assert steps[0].get("allow_failure") is True
    assert "allow_failure" not in steps[2]
    assert steps[-1]["statements"] == ["contains({{after}}, 'x')"]


def mpk_map() -> dict:
    return {
        "res/app-v1.mpk": {"": "v1.wasm", "_package": "com.x", "_mpk": "out/x-1.mpk"},
        "dist/com.x.mpk": {"": "v2.wasm", "_package": "com.x", "_mpk": "out/x-2.mpk"},
    }


def test_a_signed_bundle_installs_as_a_package_and_upgrades_by_version():
    p = Porter({"nodes": {"count": 2, "prefix": "n"}, "steps": []}, mpk_map(), 3000)
    steps = p.port_steps(
        [
            {
                "type": "install_application",
                "node": "n-1",
                "path": "res/app-v1.mpk",
                "outputs": {"app_v1": "applicationId"},
            },
            # v1 installed on each node; one install serves the cluster.
            {"type": "install_application", "node": "n-2", "path": "res/app-v1.mpk"},
            {
                "type": "create_namespace",
                "node": "n-1",
                "outputs": {"ns": "namespaceId"},
            },
            {
                "type": "create_context",
                "node": "n-1",
                "application_id": "{{app_v1}}",
                "group_id": "{{ns}}",
                "outputs": {"ctx": "contextId"},
            },
            {
                "type": "install_application",
                "node": "n-1",
                "path": "dist/com.x.mpk",
                "outputs": {"app_v2": "applicationId"},
            },
            {
                "type": "upgrade_group",
                "node": "n-1",
                "group_id": "{{ns}}",
                "target_application_id": "{{app_v2}}",
            },
            {
                "type": "get_migration_status",
                "node": "n-1",
                "namespace_id": "{{ns}}",
                "outputs": {"migrated": "migrated"},
            },
        ]
    )
    installs = [s for s in steps if s["type"] == "install_package"]
    assert [s["mpk"] for s in installs] == ["out/x-1.mpk", "out/x-2.mpk"]
    assert installs[0]["outputs"] == {"app_v1": "package", "app_v1_version": "version"}
    deploy = next(s for s in steps if s["type"] == "deploy")
    assert (
        deploy["package"] == "{{app_v1}}" and deploy["version"] == "{{app_v1_version}}"
    )
    assert "wasm" not in deploy
    (up,) = [s for s in steps if s["type"] == "upgrade_app"]
    assert up["app"] == "{{ctx}}" and up["version"] == "{{app_v2_version}}"
    (status,) = [s for s in steps if s["type"] == "migration_status"]
    assert status["apps"] == ["{{ctx}}"] and status["outputs"] == {
        "migrated": "migrated"
    }
    assert not p.dropped


def test_without_a_signed_bundle_the_wasm_is_uploaded():
    p = Porter({"nodes": {"count": 1}, "steps": []}, {"a.mpk": {"": "a.wasm"}}, 3000)
    up, _set = p.port_steps(
        [
            {
                "type": "install_application",
                "node": "calimero-node-1",
                "path": "a.mpk",
                "outputs": {"app": "applicationId"},
            }
        ]
    )
    assert up["type"] == "upload_bundle" and up["wasm"] == "a.wasm"


def test_member_capabilities_are_read_from_the_teams_row_or_the_orgs():
    p = porter()
    p.teams["folder"] = "ns"
    on_team, on_org = p.port_steps(
        [
            {
                "type": "get_member_capabilities",
                "node": "n-1",
                "group_id": "{{folder}}",
                "member_id": "{{bob}}",
                "outputs": {"caps": "capabilities"},
            },
            {
                "type": "get_member_capabilities",
                "node": "n-1",
                "group_id": "{{ns}}",
                "member_id": "{{bob}}",
            },
        ]
    )
    assert on_team["type"] == "member_capabilities" and on_team["team"] == "{{folder}}"
    assert on_team["outputs"] == {"caps": "capabilities"}
    assert "team" not in on_org


def wf(steps: list) -> dict:
    return {"nodes": {"count": 1}, "steps": steps}


def test_a_port_that_keeps_none_of_its_checks_is_not_applicable():
    out = Porter(
        wf(
            [
                {
                    "type": "call",
                    "node": "calimero-node-1",
                    "context_id": "c",
                    "method": "m",
                },
                {
                    "type": "assert_log_present",
                    "node": "calimero-node-1",
                    "pattern": "x",
                },
                {
                    "type": "assert_log_absent",
                    "node": "calimero-node-1",
                    "pattern": "y",
                },
            ]
        ),
        {},
        3000,
    ).port()
    assert "keeps 0" in out["not_applicable"]


def test_a_port_that_drops_what_it_reads_is_not_applicable():
    out = Porter(
        wf(
            [
                {"type": "create_namespace", "node": "calimero-node-1"},
                {"type": "list_namespaces", "node": "calimero-node-1"},
                {"type": "delete_namespace", "node": "calimero-node-1"},
            ]
        ),
        {},
        3000,
    ).port()
    assert "list_namespaces" in out["not_applicable"]


def test_a_port_that_runs_the_app_and_keeps_its_checks_applies():
    out = Porter(
        wf(
            [
                {
                    "type": "call",
                    "node": "calimero-node-1",
                    "context_id": "c",
                    "method": "get",
                    "outputs": {"r": "result"},
                },
                {"type": "assert", "statements": ["is_set({{r}})"]},
            ]
        ),
        {},
        3000,
    ).port()
    assert "not_applicable" not in out


def test_reads_while_peers_are_partitioned_are_not_asserted():
    # partition_peers / heal_peers, disconnect_node / connect_node and
    # pause_container / unpause_container cut a v1 node off as stop_node did.
    for start, end, who in (
        ("partition_peers", "heal_peers", {"node": "n-1", "peers": ["n-2"]}),
        ("disconnect_node", "connect_node", {"node": "n-2"}),
        ("pause_container", "unpause_container", {"container": "n-2"}),
    ):
        p = porter()
        steps = p.port_steps(
            [
                {"type": start, **who},
                {"type": "call", "node": "n-1", "context_id": "{{c}}", "method": "w"},
                {
                    "type": "call",
                    "node": "n-2",
                    "context_id": "{{c}}",
                    "method": "r",
                    "outputs": {"seen": "result"},
                },
                {"type": "json_assert", "statements": ["equal({{seen}}, 'old')"]},
                {"type": end, **who},
                {
                    "type": "call",
                    "node": "n-2",
                    "context_id": "{{c}}",
                    "method": "r",
                    "outputs": {"after": "result"},
                },
                {"type": "json_assert", "statements": ["equal({{after}}, 'new')"]},
            ]
        )
        kinds = [s["type"] for s in steps]
        assert kinds == ["call", "call", "call", "json_assert"], (start, kinds)
        assert steps[0].get("allow_failure") is True
        assert "outputs" in steps[2]
        assert steps[-1]["statements"] == ["equal({{after}}, 'new')"]
        assert p.dropped[start] == 1 and p.dropped[end] == 1


def test_a_network_fault_ends_after_its_duration_of_waits():
    p = porter()
    read = {
        "type": "call",
        "node": "n-2",
        "context_id": "{{c}}",
        "method": "r",
        "outputs": {"seen": "result"},
    }
    steps = p.port_steps(
        [
            {"type": "inject_network_fault", "container": "n-2", "duration": 20},
            {**read, "outputs": {"during": "result"}},
            {"type": "wait", "seconds": 10},
            {**read, "outputs": {"still": "result"}},
            {"type": "wait", "seconds": 15},
            {**read, "outputs": {"after": "result"}},
        ]
    )
    assert len(steps) == 5
    # What node-2 read while its link dropped packets is its side of it.
    assert {"during", "still"} <= p.dropped_vars
    assert "after" not in p.dropped_vars


def test_parallel_groups_run_one_after_another():
    p = porter()
    steps = p.port_steps(
        [
            {
                "type": "parallel",
                "failure_mode": "fail-slow",
                "groups": [
                    {
                        "name": "a",
                        "steps": [
                            {
                                "type": "call",
                                "node": "n-1",
                                "context_id": "{{c}}",
                                "method": "claim",
                                "args": {"by": "a"},
                            }
                        ],
                    },
                    {
                        "name": "b",
                        "steps": [
                            {
                                "type": "call",
                                "node": "n-2",
                                "context_id": "{{c}}",
                                "method": "claim",
                                "args": {"by": "b"},
                            }
                        ],
                    },
                ],
            }
        ]
    )
    assert [(s["type"], s["account"], s["args"]) for s in steps] == [
        ("call", "n-1", {"by": "a"}),
        ("call", "n-2", {"by": "b"}),
    ]
    assert not any("parallel" in k for k in p.dropped)
