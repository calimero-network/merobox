# Open Invitation Workflow – Node Log Analysis

## Join step 500: NEAR contract version mismatch (Feb 2026)

When the **Join via Open Invitation** step runs, the client POSTs to the join-open endpoint on the invitee node (e.g. node 2). The node returns **500 Internal Server Error**. Node 2 logs show the root cause:

- The node calls the NEAR context-config contract during the join flow.
- The contract **panics** with:  
  `unknown variant 'CommitOpenInvitation', expected one of 'Add', 'UpdateApplication', 'AddMembers', 'RemoveMembers', 'Grant', 'Revoke', 'UpdateProxyContract'`  
  and similarly for `RevealOpenInvitation`.

So the **NEAR context-config WASM** deployed by merobox (from `~/.merobox/contracts/`) must support open-invitation actions; older versions (e.g. 0.5.0) do not. Merobox now defaults to **0.6.0**; set `CALIMERO_CONTRACTS_VERSION` if you need a different release. The merod binary (e.g. 3e23d21) expects a contract that supports `CommitOpenInvitation` / `RevealOpenInvitation`. **Fix:** use a NEAR context-config contract build that includes open-invitation support, or use a merod version that matches the deployed contract API.

Node 2 also logs **"Got 0 peers"** and **"No peers to sync with"** (peer discovery in Docker e2e can be flaky), but the immediate failure is the contract panic, which causes the 500.

---

## Why Node 2 Returned "bar" Instead of "baz"

Analysis of `merobox logs open-inv-node-1` and `open-inv-node-2` after a failed run (assertion "All Members See Updated Value 'baz'").

---

## 1. Root hashes (state)

| Node   | Root hash (after workflow steps) |
|--------|-----------------------------------|
| Node 1 | `9jKw9wgmF5o5ijpzssSS3UrA8KNjLByC3Rek1Y1GeodQ` (state after **set baz**) |
| Node 2 | `CaijD2zGfdzcnvZ2fDiyPdjeL1ppaFG6qESFZjdi7Yme` (state after join, still **bar**) |

So at the time of the failing assertion, Node 1 had applied the update and Node 2 had not yet applied the corresponding DAG delta.

---

## 2. Sync path on Node 2

When Node 2 syncs with Node 1 it sees a different root and chooses a protocol:

- **Protocol selected:** `HashComparison` (CIP §2.3).
- **Log:** `HashComparison not yet implemented, requesting deltas`
- So the node **falls back** to requesting the DAG head delta from the peer instead of using HashComparison.

So: **HashComparison is not implemented**; sync still proceeds via the delta-request fallback.

---

## 3. Delta application on Node 2

Node 2 **does** eventually apply the “baz” delta:

- **Log:** `Requesting DAG head delta from peer`
- **Log:** `Concurrent branch detected - applying` (merge with local state)
- **Log:** `WASM sync completed execution` / `Merge produced new hash`
- **Log:** `Persisted applied delta to database`
- **Log:** `Successfully added DAG head delta` at **08:13:29.274Z**
- **Log:** `Sync with peer completed successfully` … `took=134.671375ms`

So sync and delta apply **do** complete on Node 2, and the state would show "baz" after that.

---

## 4. Timing vs workflow

Workflow sequence:

1. **Update value** (set baz) on Node 1.
2. **Wait for broadcast** – 5 s.
3. **Wait** – 3 s.
4. **Get Update – Node 1** → returns "baz".
5. **Get Update – Node 2** → returned "bar" (failure).

So the client calls **Get on Node 2** at about **8 seconds** after the set. Sync on Node 2 is **interval-based** (e.g. “Performing interval sync”, “Scheduled sync”). So:

- The “baz” delta is applied on Node 2 at **08:13:29.274**.
- If the workflow’s **Get Node 2** request is handled **before** that moment (or before the next sync cycle runs), Node 2 still has root `CaijD2z...` and returns **"bar"**.

So the failure is **timing**: the fixed 5+3 s wait does not guarantee that Node 2’s **next sync cycle** has run and applied the delta before we read. With `wait_for_sync` (root-hash agreement), the workflow would wait until Node 2’s root matches Node 1’s, then run the assertion.

---

## 5. Other log details

- **“Members revision was not changed, skipping sync”**  
  Node 2 sometimes skips sync because it thinks the member set hasn’t changed. That can delay when it pulls the latest DAG head.

- **“Some deltas could not be loaded - they will remain pending until parents arrive”**  
  Node 2 reports **unloadable_deltas** (parent ordering / missing parents). That can add delay or require another sync round before the “baz” delta becomes applicable.

- **HashComparison not implemented**  
  When roots differ, the preferred path (HashComparison) is not implemented; the fallback (request deltas → apply) works but is the path actually used and can be slower or depend on sync interval.

---

## 6. Summary

| Cause | Effect |
|-------|--------|
| Sync is **interval-based** on Node 2 | The “baz” delta may not be applied before the workflow’s Get. |
| **HashComparison not yet implemented** | Sync uses delta-request fallback; no earlier convergence via HashComparison. |
| Fixed **5+3 s wait** instead of **wait_for_sync** | No guarantee that Node 2’s root matches Node 1’s when we assert. |
| **Unloadable_deltas** / “Members revision … skipping sync” | Can delay or complicate when Node 2 applies the update. |

**Conclusion:** Node 2 **does** eventually apply the “baz” update (logs show “Successfully added DAG head delta” and sync completed in ~134 ms once run). The assertion fails because the workflow reads Node 2 **before** that sync has run and been applied. Using **wait_for_sync** (or a longer/adjusted wait) would align the assertion with actual sync and make the test pass consistently.

---

## 7. Core repo: bug and fix

**Finding:** When a node receives a `StateDelta` (e.g. the "baz" update) but **cannot apply it** because of missing parents, it calls `request_missing_deltas()` to ask the source peer for those deltas. It does **not** trigger an immediate full sync with that peer. Convergence then depends on the next **interval sync** (default 10s) and the **minimum interval** since last sync (5s), so the node can stay behind for many seconds even though it already knows a peer has new state.

**Fix (in core):** In `core/crates/node/src/handlers/state_delta.rs`, when a delta is **pending due to missing parents**, after `request_missing_deltas()` we now **trigger an immediate sync** with the source peer via `node_clients.node.sync(Some(&context_id), Some(&source)).await`. That makes the sync manager run a full DAG heads exchange with that peer on the next loop iteration instead of waiting for the interval.

**Relevant code (core):**
- Sync config: `core/crates/node/src/sync/config.rs` — `DEFAULT_SYNC_FREQUENCY_SECS` (10s), `DEFAULT_SYNC_INTERVAL_SECS` (5s).
- "Members revision was not changed": `core/crates/context/primitives/src/client/sync.rs` — only skips **member list** sync, not DAG sync.
- Trigger on heartbeat: `core/crates/node/src/handlers/network_event.rs` — already triggers sync when "Peer has DAG heads we don't have"; the new trigger in `state_delta` covers the case where we received a **delta** we couldn't apply (missing parents).

---

## 8. "Join via Open Invitation - Node 2" failing (no peers to sync with)

**Symptom:** Workflow fails at step "Join via Open Invitation - Node 2" with `join_context_via_open_invitation failed`.

**Node-2 logs (summary):**

1. **Join flow starts:**  
   `Successfully submitted the revealed invitation payload` → `join_context: starting join flow` → `Members revision changed, synchronizing member list...` → `Blob doesn't exist locally, using regular installation`.

2. **Sync needs peers:**  
   The context client on node-2 then tries to sync the member list with other nodes but has **no connected peers**:  
   `No peers found yet, mesh may still be forming, retrying...` → `Sync failed, applying exponential backoff` with `error=No peers to sync with for context 2oFVJfRZ99r...`.

3. **P2P connection failures:**  
   - **To other nodes (172.17.0.2, 172.17.0.4):**  
     `failed to authenticate packet`, `BadRecordMac`, `DecryptError`, `Handshake failed: Failed to upgrade client connection`.  
     So node-2 sees node-1/node-3 on the Docker network but QUIC/TLS handshakes fail (crypto/identity mismatch or protocol issue).  
   - **To default bootstrap (63.181.86.34):**  
     `HandshakeTimedOut` or `DecryptError` — external bootstrap is not usable or not reached.

4. **Gossipsub mesh empty:**  
   `HEARTBEAT: Mesh low. Topic contains: 0 needs: 6`, `RANDOM PEERS: Got 0 peers`, `Updating mesh, new mesh: {}`.

**Root cause:**  
Node-2 never gets any peer connections. So when the join flow needs to "synchronize member list" with the rest of the context, it has no peers to sync with, the operation fails or times out, and the admin API returns an error → `join_context_via_open_invitation failed`.

**Why no peers:**

- **Without `--e2e-mode`:** The workflow was run without `--e2e-mode`. Nodes then use default bootstrap (public 63.181.86.34), which is unreachable or times out from this environment. They do not use a local rendezvous/mDNS setup that would let the three Docker nodes discover each other.
- **Inter-node TLS errors:** Even when node-2 tries to connect to 172.17.0.2 (node-1) and 172.17.0.4 (node-3), the connections fail with `BadRecordMac` / `DecryptError`. That points to TLS/identity or protocol mismatch between nodes (e.g. different chain_id, keys, or QUIC version), not just discovery.

**Recommendation:**

- Run the open-invitation workflow with **`--e2e-mode`** so that:
  - Bootstrap nodes are disabled and a **unique rendezvous namespace** per run is used (nodes discover each other via rendezvous/mDNS in the Docker network).
  - E2e-style sync and test isolation settings are applied.
- Example:
  ```bash
  merobox bootstrap run workflow-examples/workflow-open-invitation-example.yml --e2e-mode --verbose
  ```
- If the failure persists, the **inter-node TLS/QUIC errors** (BadRecordMac, DecryptError) need to be investigated in the core/merod side (identity, chain_id, or transport config when multiple nodes run in Docker).
