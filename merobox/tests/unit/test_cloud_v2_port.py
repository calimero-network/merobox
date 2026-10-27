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


def test_waits_for_sync_shrink_and_waits_for_time_stay():
    p = porter()
    sync, time_ = p.port_steps(
        [
            {"type": "wait", "name": "Let node-2 sync", "seconds": 10},
            {"type": "wait", "name": "Let the time come", "seconds": 18},
        ]
    )
    assert sync["seconds"] == 1.0
    assert time_["seconds"] == 18.0


def test_a_settle_is_not_a_wait_for_time():
    (step,) = porter().port_steps(
        [{"type": "wait", "name": "Settle before the mesh joins", "seconds": 12}]
    )
    assert step["seconds"] == 1.0


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
