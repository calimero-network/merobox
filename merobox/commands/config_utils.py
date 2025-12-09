"""
Configuration utilities for modifying Calimero node config files.
"""

from pathlib import Path

import toml
from rich.console import Console

console = Console()


def apply_near_devnet_config_to_file(
    config_file: Path,
    node_name: str,
    rpc_url: str,
    contract_id: str,
    account_id: str,
    pub_key: str,
    secret_key: str,
) -> bool:
    """
    Inject local NEAR devnet configuration into a specific config.toml file.

    Args:
        config_file: Path to the config.toml file
        node_name: Name of the node (for logging)
        rpc_url: The RPC URL to inject
        contract_id: The Context Config contract ID
        account_id: The NEAR account ID for this node
        pub_key: The public key for this node
        secret_key: The secret key for this node
    """
    if not config_file.exists():
        console.print(f"[red]Config file not found: {config_file}[/red]")
        return False

    try:
        with open(config_file) as f:
            config = toml.load(f)

        # Helper to ensure keys exist
        def ensure_keys(d, keys):
            dictionary = d
            for k in keys:
                if k not in dictionary:
                    dictionary[k] = {}
                dictionary = dictionary[k]
            return dictionary

        # Update Context Config
        ensure_keys(config, ["context", "config", "near"])
        config["context"]["config"]["near"]["network"] = "local"
        config["context"]["config"]["near"]["contract_id"] = contract_id
        config["context"]["config"]["near"]["signer"] = "self"

        # Update Signer Config
        # Path: context.config.signer.self.near.local
        signer_cfg = ensure_keys(
            config, ["context", "config", "signer", "self", "near", "local"]
        )
        signer_cfg["rpc_url"] = rpc_url
        signer_cfg["account_id"] = account_id
        signer_cfg["public_key"] = pub_key
        signer_cfg["secret_key"] = secret_key

        # Write back to file
        with open(config_file, "w") as f:
            toml.dump(config, f)

        console.print(
            f"[green]✓ Injected Local NEAR Devnet config for {node_name}[/green]"
        )
        return True
    except Exception as e:
        console.print(
            f"[red]✗ Failed to apply NEAR devnet config to {node_name}: {e}[/red]"
        )
        return False
