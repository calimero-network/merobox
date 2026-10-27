# Workflows for release-v2 (Calimero Cloud)

A release-v2 cluster is one service: `merod-v2` nodes replicate every app by Raft,
and people reach it as clients (passkeys, API keys) rather than running nodes.
These workflows declare `backend: cloud-v2`. merobox starts the cluster with one
process per node, so a step can crash, freeze, restart or upgrade a node, and
drives it through calimero-client-py's `CloudClient`.

```bash
export MEROD_V2_BINARY=…/core/target/debug/merod-v2      # core, branch release-v2
export KV_STORE_WASM=…/kv_store.wasm
merobox cloud run workflow-examples/cloud-v2/kv-store-failover.yml
merobox bootstrap run workflow-examples/cloud-v2/orgs-and-keys.yml   # routes here too
```

| Workflow | What it shows |
| --- | --- |
| `kv-store-failover.yml` | A crash, a frozen node and a restart; replicas catch up |
| `frozen-leader.yml` | The app's leader stops answering; writes reroute within a 10 s budget |
| `orgs-and-keys.yml` | Organizations gate apps, scoped API keys, passkey confirmations, removal |
| `rolling-upgrade.yml` | Every node moved to another build in turn (`MEROD_V2_UPGRADE_BINARY`) |

## Format

```yaml
backend: cloud-v2
cluster: { nodes: 3, base_port: 2700 }   # node i on 127.0.0.1:base_port+i
accounts: [alice, bob]                   # each signed up with its own passkey
variables: { next: "{{env.NEXT ?? env.MEROD_V2_BINARY}}" }
steps: [...]
```

Placeholders: `{{var}}` (a whole-string placeholder keeps its type), dotted paths
into results (`{{res.events.0.kind}}`), fallbacks (`{{a ?? b}}`), `{{env.NAME}}`,
`{{accounts.<name>.account_id}}`, `{{ops_token}}`, and `{{iteration}}` /
`{{iteration_node}}` inside `repeat`.

Every step takes `name`, `outputs` (variable → path in its result),
`expected_error` (a refusal with that code is success), `expect` (the result
must equal this), `retry_for` (seconds to keep retrying) and `max_seconds`
(a latency budget). Steps that act as someone take `account` or `api_key`, and
optionally `node` (otherwise the first running node, skipping unreachable ones).

| Step | Fields |
| --- | --- |
| `deploy` | `wasm`, `app_name`, `init_args`, `org`, `access`, `confirm_methods` → `appId`, `bundle` |
| `upload_bundle` / `create_app` | `wasm` → `hash` / `bundle`, … → `appId` |
| `call` | `app`, `method`, `args`, `idempotency_key`, `confirm` (passkey approval) → `index`, `result`, `events` |
| `view` | `app`, `method`, `args`, `stale`, `min_index` → `index`, `result` |
| `wait_for` | like `view`, until the result `equals` / `contains` a value (`timeout`) |
| `events` | `app`, `from`, `count`, `timeout` → `events` |
| `whoami`, `create_api_key`, `create_org`, `create_team`, `invite`, `redeem_invite`, `remove_member`, `set_role` | accounts and organizations |
| `team_add` (`role`: `lead` / `member` / `readOnly`, `capabilities`), `team_remove`, `team_update` (`join`, `parent`, `metadata`, `name`), `team_get`, `team_list`, `team_join`, `team_delete` | teams: v1's subgroups |
| `capabilities` | `org`, `capabilities`, optional `member` and `team` (without `member`: the org's default) |
| `add_device` | `account`, `device`: another passkey on the account, which `device` signs in with |
| `fuzzy_test` | `operations` (`name`, `weight`, `steps`), `accounts`, `duration_minutes`, `success_threshold`: weighted random operations as `{{random_account}}`, with `{{random_int(a, b)}}`, `{{uuid}}`, `{{timestamp}}` and `{{fuzzy_<arg>}}`; `MEROBOX_FUZZY_SECONDS` overrides the duration |
| `http` | `method`, `path`, `body`: any API route |
| `ops` | `op`: `status`, `quotas`, `set_quotas`, `backup`, `move` (`app`, `replicas`) |
| `find_leader` | `app` → `leader`, `follower` |
| `upload_blob` | `app`, `file_path` (as given, else beside the workflow or above it) → `blobId` (base58), `blob_id`, `id` (hex), `size` |
| `download_blob` | `app`, `blob_id`, optional `expected_size` → `size`, `data` |
| `kill_node`, `pause_node`, `resume_node`, `start_node`, `restart_node` | `node`, `binary` (start/restart) |
| `wait`, `repeat`, `assert` | as in v1 workflows |
