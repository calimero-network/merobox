import json

import base58
import ed25519
from py_near.account import Account
from py_near.transactions import create_function_call_action


class NearDevnetClient:
    """
    Wrapper around py-near to handle Devnet operations.
    """

    def __init__(self, rpc_url, account_id, private_key):
        self.rpc_url = rpc_url
        self.account_id = account_id
        self.account = Account(account_id, private_key, rpc_url)

    async def create_account(self, new_account_id, initial_balance=50):
        """
        Creates a new account, funds it, and returns the credentials.
        """
        # Generate a new keypair using ed25519 directly
        sk_obj, pk_obj = ed25519.create_keypair()

        # Convert objects to bytes
        sk_bytes = sk_obj.to_bytes()
        pk_bytes = pk_obj.to_bytes()

        # Format for NEAR (ed25519:<base58>)
        public_key_b58 = base58.b58encode(pk_bytes).decode("utf-8")
        secret_key_b58 = base58.b58encode(sk_bytes).decode("utf-8")

        public_key = f"ed25519:{public_key_b58}"
        secret_key = f"ed25519:{secret_key_b58}"

        # Create the account on-chain using the root account
        # amount is in NEAR (converted to yoctoNEAR 10^24)
        amount_yocto = initial_balance * 10**24

        await self.account.create_account(new_account_id, public_key, amount_yocto)

        return {
            "account_id": new_account_id,
            "public_key": public_key,
            "secret_key": secret_key,
        }

    async def deploy_contract(self, wasm_path):
        """Reads WASM from disk and deploys to this account."""
        with open(wasm_path, "rb") as f:
            code = f.read()

        await self.account.deploy_contract(code)

    async def call(
        self, contract_id, method_name, args, gas=100_000_000_000_000, deposit=0
    ):
        """Executes a function call using manual action construction to support raw bytes."""

        # Explicitly handle argument encoding
        if isinstance(args, dict):
            args = json.dumps(args).encode("utf-8")
        elif isinstance(args, str):
            args = args.encode("utf-8")
        # If args is already bytes, leave it as is

        # Create the FunctionCall Action
        action = create_function_call_action(method_name, args, gas, deposit)

        return await self.account.sign_and_submit_tx(contract_id, [action])

    async def close(self):
        """Close the underlying aiohttp session."""
        # py-near stores the provider in '_provider'
        provider = getattr(self.account, "_provider", None)

        if provider:
            # py-near stores the session in '_client'
            session = getattr(provider, "_client", None)
            if session and not session.closed:
                await session.close()
