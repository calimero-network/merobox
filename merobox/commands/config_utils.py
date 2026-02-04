"""
Configuration utilities for modifying Calimero node config files.

This module provides shared utilities for both DockerManager and BinaryManager
to configure Calimero nodes consistently.
"""

import stat
import uuid
from pathlib import Path
from typing import Optional, Union

import toml
from rich.console import Console

console = Console()


def set_nested_config(config: dict, key: str, value, log: bool = True) -> None:
    """
    Set nested configuration value using dot notation.

    Args:
        config: The configuration dictionary to modify
        key: Dot-separated key path (e.g., "bootstrap.nodes")
        value: The value to set
        log: Whether to log the change (default True)

    Raises:
        TypeError: If an intermediate key exists but is not a dict
    """
    keys = key.split(".")
    current = config
    for k in keys[:-1]:
        if k not in current:
            current[k] = {}
        elif not isinstance(current[k], dict):
            raise TypeError(f"Cannot set nested key: '{k}' is not a dict")
        current = current[k]

    current[keys[-1]] = value
    if log:
        console.print(f"[cyan]  {key} = {value}[/cyan]")


def apply_bootstrap_nodes(
    config_file: Union[Path, str],
    node_name: str,
    bootstrap_nodes: list[str],
) -> bool:
    """
    Apply bootstrap nodes configuration to a config file.

    Args:
        config_file: Path to the config.toml file (Path or str)
        node_name: Name of the node (for logging)
        bootstrap_nodes: List of bootstrap node addresses
    """
    try:
        config_path = Path(config_file)
        if not config_path.exists():
            console.print(f"[yellow]Config file not found: {config_file}[/yellow]")
            return False

        with open(config_path, encoding="utf-8") as f:
            config = toml.load(f)

        set_nested_config(config, "bootstrap.nodes", bootstrap_nodes)

        # Ensure file is writable
        config_path.chmod(config_path.stat().st_mode | stat.S_IWUSR)

        with open(config_path, "w", encoding="utf-8") as f:
            toml.dump(config, f)

        console.print(
            f"[green]✓ Applied bootstrap nodes to {node_name} ({len(bootstrap_nodes)} nodes)[/green]"
        )
        return True

    except Exception as e:
        console.print(
            f"[red]✗ Failed to apply bootstrap nodes to {node_name}: {e}[/red]"
        )
        return False


def apply_e2e_defaults(
    config_file: Union[Path, str],
    node_name: str,
    workflow_id: Optional[str] = None,
) -> bool:
    """
    Apply e2e-style defaults for reliable testing.

    Args:
        config_file: Path to the config.toml file (Path or str)
        node_name: Name of the node (for logging)
        workflow_id: Optional workflow ID for test isolation. Generated if not provided.
    """
    try:
        # Generate unique workflow ID if not provided
        if not workflow_id:
            workflow_id = str(uuid.uuid4())[:8]

        config_path = Path(config_file)
        if not config_path.exists():
            console.print(f"[yellow]Config file not found: {config_file}[/yellow]")
            return False

        # Load existing config
        with open(config_path, encoding="utf-8") as f:
            config = toml.load(f)

        # Apply e2e-style defaults for reliable testing
        # Note: Only bootstrap, discovery, and sync settings are configured here.
        # Protocol-specific config (Ethereum, ICP, etc.) should be handled separately.
        e2e_config = {
            # Disable bootstrap nodes for test isolation
            "bootstrap.nodes": [],
            # Use unique rendezvous namespace per workflow (like e2e tests)
            "discovery.rendezvous.namespace": f"calimero/merobox-tests/{workflow_id}",
            # Keep mDNS as backup (like e2e tests)
            "discovery.mdns": True,
            # Aggressive sync settings from e2e tests for reliable testing
            "sync.timeout_ms": 30000,  # 30s timeout (matches production)
            # 500ms between syncs (very aggressive for tests)
            "sync.interval_ms": 500,
            # 1s periodic checks (ensures rapid sync in tests)
            "sync.frequency_ms": 1000,
        }

        # Apply each configuration
        for key, value in e2e_config.items():
            set_nested_config(config, key, value)

        # Ensure file is writable
        config_path.chmod(config_path.stat().st_mode | stat.S_IWUSR)

        # Write back to file
        with open(config_path, "w", encoding="utf-8") as f:
            toml.dump(config, f)

        console.print(
            f"[green]✓ Applied e2e-style defaults to {node_name} (workflow: {workflow_id})[/green]"
        )
        return True

    except Exception as e:
        console.print(f"[red]✗ Failed to apply e2e defaults to {node_name}: {e}[/red]")
        return False


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
