# Bounty Analysis: Remove Hardcoded Ethereum Secret Key

**Bounty Title:** Remove hardcoded Ethereum secret key from constants.py  
**Category:** Security  
**Severity:** High  
**Estimated Time:** ~45 min  

---

## 1. Why Does This Issue Exist?

### Root Cause

The file `merobox/commands/constants.py` (lines 121-132) contains hardcoded Ethereum credentials intended for local development with Anvil (Foundry's local Ethereum devnet):

```python
# Ethereum local devnet configuration (Anvil defaults)
# Reference: https://getfoundry.sh/anvil/overview#getting-started
# Anvil provides 10 default accounts with pre-funded balances for testing
# These are the first account's credentials from Anvil's default account list
ETHEREUM_LOCAL_CONTRACT_ID = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
# Anvil default account #0 (first account)
# Address: 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
# Private key: 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
ETHEREUM_LOCAL_ACCOUNT_ID = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
ETHEREUM_LOCAL_SECRET_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
)
```

### Context and Design Rationale

These are **well-known, publicly documented Anvil test keys**. Anvil (and previously Ganache) ships with deterministic accounts for local testing—these keys are intentionally public and appear in Foundry documentation. The design intent is to provide zero-configuration local blockchain testing.

**Historical context from CHANGELOG.md (v0.2.9):**
> "Ethereum Relayer Timeouts: Resolved by using local Anvil devnet instead of public Sepolia testnet"

This was added to improve test reliability by avoiding public testnet congestion.

---

## 2. What Might We Be Missing?

### Related Code / Duplication Issues

**Finding 1: Inline hardcoding in `binary_manager.py` (lines 937-942)**

There's a **separate, inline hardcoded instance** of the same key that doesn't use the constant:

```python
# File: merobox/commands/binary_manager.py, lines 936-942
"context.config.ethereum.network": "sepolia",  # ← Note: says "sepolia" but points to local!
"context.config.ethereum.contract_id": "0x5FbDB2315678afecb367f032d93F642f64180aa3",
"context.config.ethereum.signer": "self",
"context.config.signer.self.ethereum.sepolia.rpc_url": "http://127.0.0.1:8545",
"context.config.signer.self.ethereum.sepolia.account_id": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
"context.config.signer.self.ethereum.sepolia.secret_key": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
```

**Issues with this code:**
1. Key is hardcoded inline rather than using `ETHEREUM_LOCAL_SECRET_KEY` constant
2. Network is labeled "sepolia" (a public testnet) but actually points to local Anvil (`127.0.0.1:8545`)
3. Comment says "same as e2e tests" but introduces inconsistency

**Finding 2: Proper usage in `manager.py` (lines 1608-1614)**

The Docker-based manager **correctly** uses the constants:

```python
"context.config.ethereum.network": NETWORK_LOCAL,
"context.config.ethereum.contract_id": ETHEREUM_LOCAL_CONTRACT_ID,
"context.config.signer.self.ethereum.local.rpc_url": eth_rpc_url,
"context.config.signer.self.ethereum.local.account_id": ETHEREUM_LOCAL_ACCOUNT_ID,
"context.config.signer.self.ethereum.local.secret_key": ETHEREUM_LOCAL_SECRET_KEY,
```

### Similar Patterns

- **NEAR devnet:** Uses `secret_key` parameter passed dynamically (see `config_utils.py` lines 20-65). This is more secure as keys flow through function parameters rather than constants.
- **ICP devnet:** Only has `ICP_LOCAL_CONTRACT_ID` hardcoded—no secret key (line 136).

### Tests Affected

Tests use mock/placeholder values like `"sk"` or `"secret_key"` and don't rely on the actual Anvil key:
- `test_binary_manager.py` - uses `"secret_key": "sk"`
- `test_docker_manager.py` - uses `"secret_key": "sk"`
- `test_config_utils.py` - uses variable `sec_key`

**No tests would break** from refactoring the constant.

---

## 3. Case for Fixing

### Impact if We Fix It

| Aspect | Benefit |
|--------|---------|
| **Security posture** | Eliminates a common security anti-pattern that triggers SAST/DAST tools |
| **Maintainability** | Single source of truth if key ever needs to change |
| **Clarity** | Clear separation between "local test defaults" and "configurable production values" |
| **Consistency** | `binary_manager.py` would use constants like `manager.py` already does |

### Risk if We Leave It

| Risk | Severity | Likelihood |
|------|----------|------------|
| **Direct exploitation** | None - these are well-known test keys with no real funds | N/A |
| **Credential scanning alerts** | Medium - security scanners will flag this as high severity | High |
| **Copy-paste accidents** | Medium - developers might copy this pattern with real keys | Medium |
| **Sepolia mislabeling confusion** | Low - could cause debugging headaches | Medium |

**The actual security risk is LOW** because these are intentionally public Anvil test accounts. However, the **pattern is problematic** because:
1. It normalizes hardcoded secrets in codebases
2. Security scanners will continuously flag this
3. The inline duplication in `binary_manager.py` creates maintenance burden

### Recommended Approach

**Option A: Minimal Fix (Recommended)**

1. Keep the constants but add explicit `# SECURITY: Well-known Anvil test keys - NEVER use in production` comments
2. Fix `binary_manager.py` to use the constants instead of inline values
3. Fix the misleading "sepolia" network label to "local"
4. Add a runtime warning if these keys are used with non-localhost RPC URLs

**Changes required:**
- `constants.py`: Add security warning comments (~5 lines)
- `binary_manager.py`: Replace inline values with constants, fix network name (~10 lines)

**Option B: Environment Variable Approach**

1. Default to Anvil keys but allow `ETHEREUM_LOCAL_SECRET_KEY` env var override
2. Log a warning when using default test keys

```python
ETHEREUM_LOCAL_SECRET_KEY = os.environ.get(
    "MEROBOX_ETH_LOCAL_SECRET_KEY",
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # Anvil default
)
```

**Option C: Full Refactor (Higher effort)**

Move all blockchain credentials to a config file (e.g., `~/.merobox/credentials.toml`) with local devnet defaults.

---

## Verdict: Fix Now (Option A)

**Recommendation:** Implement Option A (Minimal Fix) now.

**Reasoning:**
1. The "security risk" is actually low since these are intentionally public test keys
2. The real issues are: (a) inline duplication in `binary_manager.py`, and (b) misleading "sepolia" labeling
3. Adding clear documentation comments addresses security scanner concerns
4. This is a 15-30 minute fix with high confidence and zero risk of breaking changes
5. Option B (env vars) could be a follow-up enhancement but isn't strictly necessary for test keys

**Specific action items:**
1. Add explicit security warning comments to `constants.py` (lines 121-132)
2. Update `binary_manager.py` (lines 936-942) to use `ETHEREUM_LOCAL_*` constants
3. Fix network name from "sepolia" to "local" in `binary_manager.py`
4. Update CHANGELOG.md to document the fix

**Estimated actual time:** 20-30 minutes (including testing)
