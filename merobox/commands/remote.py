"""
Remote node management commands for merobox.

This module provides CLI commands for managing remote Calimero nodes:
- merobox remote login <url> - Authenticate with a remote node
- merobox remote logout <url|--all> - Remove cached authentication
- merobox remote status - List registered nodes and cached tokens
- merobox remote test <url> - Test connectivity and auth with a node
- merobox remote register <name> <url> - Register a remote node
- merobox remote unregister <name> - Unregister a remote node
"""

import asyncio
from datetime import datetime
from typing import Optional

import aiohttp
import click
from rich import box
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from merobox.commands.auth import (
    AUTH_METHOD_API_KEY,
    AUTH_METHOD_NONE,
    AUTH_METHOD_USER_PASSWORD,
    AuthenticationError,
    AuthManager,
    run_with_shared_session_cleanup,
)
from merobox.commands.constants import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)
from merobox.commands.node_resolver import ADMIN_HEALTH_ENDPOINT
from merobox.commands.remote_nodes import RemoteNodeManager

console = Console()


def run_async(coro):
    """Run an async function and clean up shared auth sessions."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(run_with_shared_session_cleanup(coro))


@click.group(name="remote")
def remote():
    """Manage remote Calimero nodes."""
    pass


@remote.command(name="login")
@click.argument("url_or_name")
@click.option(
    "--username",
    "-u",
    help="Username for authentication (can also use MEROBOX_USERNAME env var).",
)
@click.option(
    "--password",
    "-p",
    help="Password for authentication (can also use MEROBOX_PASSWORD env var).",
)
@click.option(
    "--api-key",
    "-k",
    help="API key for authentication (can also use MEROBOX_API_KEY env var).",
)
@click.option(
    "--method",
    "-m",
    type=click.Choice(["user_password", "api_key"]),
    default="user_password",
    help="Authentication method to use.",
)
def login(
    url_or_name: str,
    username: Optional[str],
    password: Optional[str],
    api_key: Optional[str],
    method: str,
):
    """
    Authenticate with a remote Calimero node.

    URL_OR_NAME can be either a registered node name or a direct URL.

    Examples:
        merobox remote login https://node.example.com
        merobox remote login my-node --username admin
        merobox remote login https://node.example.com --api-key <key>
    """
    remote_manager = RemoteNodeManager()
    auth_manager = AuthManager()

    # Determine the actual URL and node name
    if remote_manager.is_url(url_or_name):
        url = url_or_name.rstrip("/")
        # Check if registered
        entry = remote_manager.get_by_url(url)
        node_name = entry.name if entry else remote_manager.get_stable_node_name(url)
    else:
        # It's a name - look up in registry
        entry = remote_manager.get(url_or_name)
        if entry:
            url = entry.url
            node_name = entry.name
            # Use registered auth method as default if available
            if not api_key and entry.auth.method == AUTH_METHOD_API_KEY:
                method = "api_key"
            if not username and entry.auth.username:
                username = entry.auth.username
        else:
            console.print(
                f"[red]Node '{url_or_name}' is not registered. "
                f"Use a URL or register the node first.[/red]"
            )
            return

    console.print(f"[cyan]Authenticating with {url}...[/cyan]")

    # Handle authentication based on method
    if method == "api_key" or api_key:
        if not api_key:
            api_key = Prompt.ask(
                "[cyan]Enter API key[/cyan]",
                password=True,
            )
        if api_key:
            from merobox.commands.auth import AuthToken

            token = AuthToken(
                access_token=api_key,
                refresh_token=None,
                expires_at=None,
                node_url=url,
                auth_method=AUTH_METHOD_API_KEY,
                username=None,
            )
            if auth_manager.save_token(token, node_name):
                console.print(f"[green]✓ API key saved for {url}[/green]")
            else:
                console.print("[red]Failed to save API key[/red]")
        return

    # User/password authentication
    if not username:
        username = Prompt.ask("[cyan]Username[/cyan]")
    if not password:
        password = Prompt.ask("[cyan]Password[/cyan]", password=True)

    if not username or not password:
        console.print("[red]Username and password are required[/red]")
        return

    # Perform authentication
    async def do_login():
        try:
            token = await auth_manager.authenticate(url, username, password)
            auth_manager.save_token(token, node_name)
            return True
        except AuthenticationError as e:
            console.print(f"[red]Authentication failed: {e}[/red]")
            return False

    success = run_async(do_login())
    if success:
        console.print(f"[green]✓ Successfully authenticated with {url}[/green]")


@remote.command(name="logout")
@click.argument("url_or_name", required=False)
@click.option("--all", "logout_all", is_flag=True, help="Logout from all remote nodes.")
def logout(url_or_name: Optional[str], logout_all: bool):
    """
    Remove cached authentication for a remote node.

    URL_OR_NAME can be either a registered node name or a direct URL.

    Examples:
        merobox remote logout https://node.example.com
        merobox remote logout my-node
        merobox remote logout --all
    """
    auth_manager = AuthManager()
    remote_manager = RemoteNodeManager()

    if logout_all:
        count = auth_manager.delete_all_tokens()
        console.print(f"[green]✓ Removed {count} cached token(s)[/green]")
        return

    if not url_or_name:
        console.print("[red]Please specify a node URL/name or use --all[/red]")
        return

    # Determine node name for cache
    if remote_manager.is_url(url_or_name):
        entry = remote_manager.get_by_url(url_or_name)
        node_name = (
            entry.name if entry else remote_manager.get_stable_node_name(url_or_name)
        )
    else:
        node_name = url_or_name

    if auth_manager.delete_token(node_name):
        console.print(f"[green]✓ Logged out from {url_or_name}[/green]")
    else:
        console.print(f"[yellow]No cached credentials for {url_or_name}[/yellow]")


@remote.command(name="status")
def status():
    """
    Show status of registered remote nodes and cached tokens.

    Displays:
    - Registered remote nodes with their URLs and auth configuration
    - Cached authentication tokens and their expiration status
    """
    remote_manager = RemoteNodeManager()
    auth_manager = AuthManager()

    # List registered nodes
    nodes = remote_manager.list_all()

    if nodes:
        table = Table(
            title="Registered Remote Nodes",
            box=box.ROUNDED,
        )
        table.add_column("Name", style="cyan")
        table.add_column("URL", style="blue")
        table.add_column("Auth Method", style="yellow")
        table.add_column("Username", style="green")
        table.add_column("Description", style="white")

        for node in nodes:
            table.add_row(
                node.name,
                node.url,
                node.auth.method,
                node.auth.username or "-",
                node.description or "-",
            )

        console.print(table)
        console.print()
    else:
        console.print("[yellow]No remote nodes registered[/yellow]")
        console.print(
            "[cyan]Use 'merobox remote register <name> <url>' to register a node[/cyan]"
        )
        console.print()

    # List cached tokens
    tokens = auth_manager.list_cached_tokens()

    if tokens:
        token_table = Table(
            title="Cached Authentication Tokens",
            box=box.ROUNDED,
        )
        token_table.add_column("Node", style="cyan")
        token_table.add_column("URL", style="blue")
        token_table.add_column("Auth Method", style="yellow")
        token_table.add_column("Username", style="green")
        token_table.add_column("Status", style="white")

        for node_name, token in tokens:
            # Determine status
            if token.expires_at:
                expires_dt = datetime.fromtimestamp(token.expires_at)
                if token.is_expired():
                    status_str = (
                        f"[red]Expired ({expires_dt.strftime('%Y-%m-%d %H:%M')})[/red]"
                    )
                else:
                    status_str = f"[green]Valid until {expires_dt.strftime('%Y-%m-%d %H:%M')}[/green]"
            else:
                status_str = "[yellow]No expiry (API key or unknown)[/yellow]"

            token_table.add_row(
                node_name,
                token.node_url,
                token.auth_method,
                token.username or "-",
                status_str,
            )

        console.print(token_table)
    else:
        console.print("[yellow]No cached authentication tokens[/yellow]")
        console.print("[cyan]Use 'merobox remote login <url>' to authenticate[/cyan]")


@remote.command(name="test")
@click.argument("url_or_name")
@click.option("--username", "-u", help="Username for authentication.")
@click.option("--password", "-p", help="Password for authentication.")
@click.option("--api-key", "-k", help="API key for authentication.")
def test(
    url_or_name: str,
    username: Optional[str],
    password: Optional[str],
    api_key: Optional[str],
):
    """
    Test connectivity and authentication with a remote node.

    URL_OR_NAME can be either a registered node name or a direct URL.
    Tests:
    1. Network connectivity
    2. Auth requirement detection
    3. Authentication (if credentials provided or cached)
    4. Admin API access

    Examples:
        merobox remote test https://node.example.com
        merobox remote test my-node
        merobox remote test https://node.example.com --username admin --password secret
    """
    remote_manager = RemoteNodeManager()
    auth_manager = AuthManager()

    # Resolve URL
    if remote_manager.is_url(url_or_name):
        url = url_or_name.rstrip("/")
        entry = remote_manager.get_by_url(url)
        node_name = entry.name if entry else remote_manager.get_stable_node_name(url)
        is_registered = entry is not None
    else:
        entry = remote_manager.get(url_or_name)
        if entry:
            url = entry.url
            node_name = entry.name
            is_registered = True
        else:
            console.print(
                f"[red]Node '{url_or_name}' is not registered and is not a URL[/red]"
            )
            return

    console.print(f"[bold]Testing connection to {url}[/bold]")
    console.print(f"  Node name: {node_name}")
    console.print(f"  Registered: {'Yes' if is_registered else 'No'}")
    console.print()

    async def run_tests():
        results = []

        # Test 1: Network connectivity
        console.print("[cyan]1. Testing network connectivity...[/cyan]")
        health_url = f"{url}{ADMIN_HEALTH_ENDPOINT}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    health_url,
                    timeout=aiohttp.ClientTimeout(
                        total=DEFAULT_READ_TIMEOUT,
                        connect=DEFAULT_CONNECTION_TIMEOUT,
                    ),
                ) as response:
                    status = response.status
                    results.append(("Network", True, f"Reachable (HTTP {status})"))
                    console.print(f"   [green]✓ Reachable (HTTP {status})[/green]")

                    # Test 2: Auth detection
                    # Note: This is informational, not pass/fail - use None for status
                    console.print(
                        "[cyan]2. Checking authentication requirement...[/cyan]"
                    )
                    if status == 200:
                        results.append(("Auth Detection", None, "No auth required"))
                        console.print("   [green]✓ No authentication required[/green]")
                        auth_required = False
                    elif status in (401, 403):
                        results.append(("Auth Detection", None, "Auth required"))
                        console.print("   [yellow]⚠ Authentication required[/yellow]")
                        auth_required = True
                    else:
                        results.append(
                            ("Auth Detection", None, f"Unknown (HTTP {status})")
                        )
                        console.print(
                            f"   [yellow]? Could not determine (HTTP {status})[/yellow]"
                        )
                        auth_required = False

        except aiohttp.ClientError as e:
            results.append(("Network", False, str(e)))
            console.print(f"   [red]✗ Network error: {e}[/red]")
            return results
        except asyncio.TimeoutError:
            results.append(("Network", False, "Timeout"))
            console.print("   [red]✗ Connection timeout[/red]")
            return results

        # Test 3: Authentication
        console.print("[cyan]3. Testing authentication...[/cyan]")

        # Check for cached token
        cached_token = auth_manager.get_cached_token(node_name)

        if api_key:
            # Use provided API key
            results.append(("Auth Method", True, "API Key (provided)"))
            console.print("   [green]✓ Using provided API key[/green]")
            auth_token = api_key
        elif cached_token and not cached_token.is_expired():
            # Use cached token
            results.append(
                ("Auth Method", True, f"Cached ({cached_token.auth_method})")
            )
            console.print(
                f"   [green]✓ Using cached token ({cached_token.auth_method})[/green]"
            )
            auth_token = cached_token.access_token
        elif username and password:
            # Authenticate with provided credentials
            try:
                token = await auth_manager.authenticate(url, username, password)
                auth_manager.save_token(token, node_name)
                results.append(("Auth Method", True, "User/Password (authenticated)"))
                console.print("   [green]✓ Authenticated successfully[/green]")
                auth_token = token.access_token
            except AuthenticationError as e:
                results.append(("Authentication", False, str(e)))
                console.print(f"   [red]✗ Authentication failed: {e}[/red]")
                auth_token = None
        elif cached_token and cached_token.is_expired() and cached_token.refresh_token:
            # Try to refresh expired token
            try:
                new_token = await auth_manager.refresh(url, cached_token)
                auth_manager.save_token(new_token, node_name)
                results.append(("Auth Method", True, "Cached (refreshed)"))
                console.print("   [green]✓ Token refreshed successfully[/green]")
                auth_token = new_token.access_token
            except AuthenticationError as e:
                results.append(("Auth Refresh", False, str(e)))
                console.print(f"   [yellow]⚠ Token refresh failed: {e}[/yellow]")
                auth_token = None
        else:
            if auth_required:
                results.append(("Authentication", None, "No credentials available"))
                console.print("   [yellow]⚠ No credentials available[/yellow]")
                console.print(
                    "   [cyan]Tip: Use --username/--password or --api-key, or run 'merobox remote login'[/cyan]"
                )
            else:
                results.append(("Authentication", None, "Not required"))
                console.print("   [green]✓ Not required[/green]")
            auth_token = None

        # Test 4: Authenticated API access
        if auth_token or not auth_required:
            console.print("[cyan]4. Testing API access...[/cyan]")

            headers = {}
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        health_url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(
                            total=DEFAULT_READ_TIMEOUT,
                            connect=DEFAULT_CONNECTION_TIMEOUT,
                        ),
                    ) as response:
                        if response.status == 200:
                            results.append(("API Access", True, "Success"))
                            console.print("   [green]✓ API access successful[/green]")
                        else:
                            results.append(
                                ("API Access", False, f"HTTP {response.status}")
                            )
                            console.print(
                                f"   [red]✗ API returned HTTP {response.status}[/red]"
                            )
            except Exception as e:
                results.append(("API Access", False, str(e)))
                console.print(f"   [red]✗ API access failed: {e}[/red]")
        else:
            results.append(("API Access", None, "Skipped (no auth)"))
            console.print("[cyan]4. API access test skipped (no credentials)[/cyan]")

        return results

    results = run_async(run_tests())

    # Summary
    console.print()
    console.print("[bold]Summary:[/bold]")
    all_passed = all(r[1] is True or r[1] is None for r in results)
    if all_passed:
        console.print("[green]✓ All tests passed[/green]")
    else:
        failed = [r[0] for r in results if r[1] is False]
        console.print(f"[red]✗ Failed tests: {', '.join(failed)}[/red]")


@remote.command(name="register")
@click.argument("name")
@click.argument("url")
@click.option(
    "--auth-method",
    "-m",
    type=click.Choice(["user_password", "api_key", "none"]),
    default="user_password",
    help="Authentication method for this node.",
)
@click.option("--username", "-u", help="Default username for user_password auth.")
@click.option("--description", "-d", help="Human-readable description for this node.")
def register(
    name: str,
    url: str,
    auth_method: str,
    username: Optional[str],
    description: Optional[str],
):
    """
    Register a remote node with a friendly name.

    This allows you to use the name instead of the full URL in other commands.

    Examples:
        merobox remote register prod https://prod.example.com
        merobox remote register dev https://dev.example.com --auth-method none
        merobox remote register staging https://staging.example.com -u admin -d "Staging environment"
    """
    remote_manager = RemoteNodeManager()

    # Validate URL
    if not url.startswith(("http://", "https://")):
        console.print("[red]URL must start with http:// or https://[/red]")
        return

    # Map CLI auth method to constants
    method_map = {
        "user_password": AUTH_METHOD_USER_PASSWORD,
        "api_key": AUTH_METHOD_API_KEY,
        "none": AUTH_METHOD_NONE,
    }

    success = remote_manager.register(
        name=name,
        url=url,
        auth_method=method_map[auth_method],
        username=username,
        description=description,
    )

    if success:
        console.print()
        console.print(f"[cyan]You can now use '{name}' in place of the URL:[/cyan]")
        console.print(f"  merobox remote login {name}")
        console.print(f"  merobox remote test {name}")


@remote.command(name="unregister")
@click.argument("name")
@click.option(
    "--remove-token",
    is_flag=True,
    help="Also remove cached authentication token for this node.",
)
def unregister(name: str, remove_token: bool):
    """
    Unregister a remote node.

    Examples:
        merobox remote unregister my-node
        merobox remote unregister my-node --remove-token
    """
    remote_manager = RemoteNodeManager()
    auth_manager = AuthManager()

    # Check if registered
    if not remote_manager.exists(name):
        console.print(f"[yellow]Node '{name}' is not registered[/yellow]")
        return

    # Unregister
    if remote_manager.unregister(name):
        if remove_token:
            if auth_manager.delete_token(name):
                console.print(
                    f"[green]✓ Also removed cached token for '{name}'[/green]"
                )


@remote.command(name="list")
def list_nodes():
    """
    List all registered remote nodes.

    Alias for 'merobox remote status' but shows only registered nodes.
    """
    remote_manager = RemoteNodeManager()

    nodes = remote_manager.list_all()

    if not nodes:
        console.print("[yellow]No remote nodes registered[/yellow]")
        console.print(
            "[cyan]Use 'merobox remote register <name> <url>' to register a node[/cyan]"
        )
        return

    table = Table(
        title="Registered Remote Nodes",
        box=box.ROUNDED,
    )
    table.add_column("Name", style="cyan")
    table.add_column("URL", style="blue")
    table.add_column("Auth Method", style="yellow")
    table.add_column("Username", style="green")
    table.add_column("Description", style="white")

    for node in nodes:
        table.add_row(
            node.name,
            node.url,
            node.auth.method,
            node.auth.username or "-",
            node.description or "-",
        )

    console.print(table)
