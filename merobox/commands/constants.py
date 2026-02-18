"""
Constants and configuration values used across the merobox codebase.
"""

# API Endpoints
JSONRPC_ENDPOINT = "/jsonrpc"
ADMIN_API_BASE = "/admin-api"
ADMIN_API_APPLICATIONS = f"{ADMIN_API_BASE}/applications"
ADMIN_API_CONTEXTS = f"{ADMIN_API_BASE}/contexts"
ADMIN_API_CONTEXTS_INVITE = f"{ADMIN_API_BASE}/contexts/invite"
ADMIN_API_CONTEXTS_JOIN = f"{ADMIN_API_BASE}/contexts/join"
ADMIN_API_IDENTITY_CONTEXT = f"{ADMIN_API_BASE}/identity/context"
ADMIN_API_HEALTH = f"{ADMIN_API_BASE}/health"
ADMIN_API_NODE_INFO = f"{ADMIN_API_BASE}/node-info"

# Network ports
DEFAULT_RPC_PORT = 2528
DEFAULT_P2P_PORT = 2428
NEAR_SANDBOX_RPC_PORT = 3030

# Docker port binding strings (used in container port mappings)
RPC_PORT_BINDING = f"{DEFAULT_RPC_PORT}/tcp"
P2P_PORT_BINDING = f"{DEFAULT_P2P_PORT}/tcp"

# Default values
DEFAULT_CHAIN_ID = "testnet-1"
DEFAULT_PROTOCOL = "near"
DEFAULT_TIMEOUT = 30
NODE_READY_TIMEOUT = 60  # seconds to wait for nodes to be ready and accessible

# Retry and timeout configuration
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 1.0  # seconds
DEFAULT_RETRY_BACKOFF = 2.0  # exponential backoff multiplier
DEFAULT_CONNECTION_TIMEOUT = 10.0  # seconds
DEFAULT_READ_TIMEOUT = 30.0  # seconds

# Quick retry configuration
QUICK_RETRY_ATTEMPTS = 2
QUICK_RETRY_DELAY = 0.5  # seconds
QUICK_RETRY_BACKOFF = 1.5
QUICK_CONNECTION_TIMEOUT = 5.0  # seconds
QUICK_READ_TIMEOUT = 15.0  # seconds

# Persistent retry configuration
PERSISTENT_RETRY_ATTEMPTS = 5
PERSISTENT_RETRY_DELAY = 2.0  # seconds
PERSISTENT_RETRY_BACKOFF = 1.5
PERSISTENT_CONNECTION_TIMEOUT = 15.0  # seconds
PERSISTENT_READ_TIMEOUT = 60.0  # seconds

# Process and container management timeouts
CONTAINER_STOP_TIMEOUT = 10  # seconds
PROCESS_WAIT_TIMEOUT = 5  # seconds
NODE_STARTUP_WAIT = 3  # seconds for node to stabilize after start
SOCKET_CONNECTION_TIMEOUT = 1.5  # seconds for quick connection check
NUKE_STOP_TIMEOUT = 30  # seconds for nuke operations

# Polling and wait intervals
RPC_WAIT_TIMEOUT = 10  # seconds to wait for RPC to be ready
RPC_POLL_INTERVAL = 0.1  # seconds between RPC readiness checks
RPC_INITIAL_WAIT = 1.0  # seconds initial wait before polling
CLEANUP_WAIT = 0.5  # seconds after process cleanup
ASYNC_POLL_INTERVAL = 2  # seconds between async checks

# State sync retry configuration
STATE_RETRY_ATTEMPTS = 5
STATE_RETRY_DELAY = 3.0  # seconds
SYNC_RETRY_ATTEMPTS = 3
SYNC_RETRY_DELAY = 0.5  # seconds

# Health check timeout
HEALTH_CHECK_TIMEOUT = 10  # seconds

# Application installation timeout
INSTALL_TIMEOUT = 30  # seconds

# File lock timeout for contract downloads
CONTRACT_DOWNLOAD_LOCK_TIMEOUT = 300  # seconds
CONTRACT_DOWNLOAD_TIMEOUT = 30  # seconds

# Docker configuration
DEFAULT_IMAGE = "ghcr.io/calimero-network/merod:prerelease"
DEFAULT_NODE_PREFIX = "calimero-node"
DEFAULT_DATA_DIR_PREFIX = "data"

# Workflow node configuration - reserved keys
# These keys in the nodes config dict are configuration parameters, not node names
RESERVED_NODE_CONFIG_KEYS = {
    "count",
    "prefix",
    "base_port",
    "base_rpc_port",
    "chain_id",
    "image",
    "config_path",
    "use_image_entrypoint",
}

# Response field names (from API responses)
FIELD_APPLICATION_ID = "applicationId"
FIELD_CONTEXT_ID = "contextId"
FIELD_MEMBER_PUBLIC_KEY = "memberPublicKey"
FIELD_PUBLIC_KEY = "publicKey"
FIELD_IDENTITY_ID = "id"
FIELD_INVITATION = "invitation"
FIELD_RESULT = "result"
FIELD_OUTPUT = "output"
FIELD_DATA = "data"
FIELD_SUCCESS = "success"
FIELD_ERROR = "error"

# Workflow step types
STEP_INSTALL_APPLICATION = "install_application"
STEP_CREATE_CONTEXT = "create_context"
STEP_CREATE_IDENTITY = "create_identity"
STEP_INVITE_IDENTITY = "invite_identity"
STEP_JOIN_CONTEXT = "join_context"
STEP_CALL = "call"
STEP_WAIT = "wait"
STEP_REPEAT = "repeat"
STEP_GET_PROPOSAL = "get_proposal"
STEP_LIST_PROPOSALS = "list_proposals"
STEP_GET_PROPOSAL_APPROVERS = "get_proposal_approvers"

# Protocol types
PROTOCOL_NEAR = "near"

# Network types
NETWORK_MAINNET = "mainnet"
NETWORK_TESTNET = "testnet"
NETWORK_LOCAL = "local"

# Valid protocol networks mapping
VALID_NETWORKS = {
    PROTOCOL_NEAR: [NETWORK_MAINNET, NETWORK_TESTNET, NETWORK_LOCAL],
}

# Container data directory patterns
CONTAINER_DATA_DIR_PATTERNS = [
    "data/{prefix}-{node_num}-{chain_id}",
    "data/{node_name}",
]

# JSON-RPC method names
JSONRPC_METHOD_EXECUTE = "execute"

# Default metadata
DEFAULT_METADATA = b""

# Error messages
ERROR_NODE_NOT_RUNNING = "Node {node} is not running"
ERROR_NODE_NOT_FOUND = "Node {node} not found"
ERROR_INVALID_URL = "Invalid URL: {url}"
ERROR_INVALID_PORT = "Port must be between 1 and 65535"
ERROR_FILE_NOT_FOUND = "File not found: {path}"
ERROR_CONTAINER_DATA_DIR_NOT_FOUND = "Container data directory not found: {dir}"
