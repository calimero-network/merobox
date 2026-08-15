"""
Base step class for all workflow steps.
"""

import ast
import json
import re
from typing import TYPE_CHECKING, Any, Callable, Optional

from rich.markup import escape

from merobox.commands.auth import AuthManager
from merobox.commands.errors import OutputCaptureError, UnresolvedPlaceholderError
from merobox.commands.utils import LOG_LEVEL_VERBOSE, console, get_node_rpc_url, vprint

if TYPE_CHECKING:
    from merobox.commands.node_resolver import NodeResolver

# Tells an absent key apart from one present with a null value, which a capture
# is entitled to export.
_MISSING = object()


def _describe_keys(data: Any) -> str:
    """The keys a failed capture could have named, for the error message."""
    if not isinstance(data, dict):
        return f"the response is {type(data).__name__}, not an object"
    if not data:
        return "the response is empty"
    keys = sorted(data)
    return ", ".join(keys[:10]) + ("..." if len(keys) > 10 else "")


class BaseStep:
    """Base class for all workflow steps."""

    # Assertion steps set this: a `{{placeholder}}` that never resolved is a
    # broken workflow, not a string to compare against.
    strict_placeholders = False

    def __init__(
        self,
        config: dict[str, Any],
        manager: object | None = None,
        resolver: Optional["NodeResolver"] = None,
        auth_mode: Optional[str] = None,
    ):
        self.config = config

        self.manager = manager
        self.resolver = resolver
        # Auth mode for embedded auth support (e.g., "embedded", "proxy")
        self.auth_mode = auth_mode
        # Define which variables this step can export and their mapping
        self.exportable_variables = self._get_exportable_variables()
        # Validate required fields before proceeding
        self._validate_required_fields()
        # Validate field types
        self._validate_field_types()
        # Kept out of _validate_field_types() so a subclass override cannot drop
        # it. The raw value stands in until an executor binds any placeholders.
        self._validate_expected_error()
        self._expected_error = self.config.get("expected_error")

    def _get_exportable_variables(self) -> list[tuple[str, str, str]]:
        """
        Define which variables this step can export.
        Returns a list of tuples: (source_field, target_key, description)

        Override this method in subclasses to specify exportable variables.
        """
        return []

    def _get_required_fields(self) -> list[str]:
        """
        Define which fields are required for this step.
        Override this method in subclasses to specify required fields.

        Returns:
            List of required field names
        """
        return []

    def _validate_required_fields(self) -> None:
        """
        Validate that all required fields are present in the configuration.
        Raises ValueError if any required fields are missing.
        """
        required_fields = self._get_required_fields()
        missing_fields = []

        for field in required_fields:
            if field not in self.config or self.config[field] is None:
                missing_fields.append(field)

        if missing_fields:
            step_name = self.config.get(
                "name", f'Unnamed {self.config.get("type", "Unknown")} step'
            )
            step_type = self.config.get("type", "Unknown")
            raise ValueError(
                f"Step '{step_name}' (type: {step_type}) is missing required fields: {', '.join(missing_fields)}. "
                f"Required fields: {', '.join(required_fields)}"
            )

    def _validate_field_types(self) -> None:
        """
        Validate that fields have the correct types.
        Override this method in subclasses to add type validation.
        Use the _validate_* helper methods for comprehensive validation.
        """
        pass

    # =========================================================================
    # Field Validation Helper Methods
    # =========================================================================
    # These methods provide comprehensive validation for common field types.
    # Use them in subclass _validate_field_types() implementations.
    # =========================================================================

    def _get_step_name(self) -> str:
        """Get the step name for error messages."""
        return self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )

    def _validate_string_field(
        self,
        field_name: str,
        *,
        required: bool = True,
        allow_empty: bool = False,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        pattern: Optional[str] = None,
        pattern_description: Optional[str] = None,
    ) -> None:
        """
        Validate a string field with comprehensive checks.

        Args:
            field_name: Name of the field to validate
            required: If True, field must exist (default True)
            allow_empty: If False, empty strings are rejected (default False)
            min_length: Minimum string length (after stripping whitespace)
            max_length: Maximum string length
            pattern: Regex pattern the value must match
            pattern_description: Human-readable description of the pattern

        Raises:
            ValueError: If validation fails
        """
        step_name = self._get_step_name()
        value = self.config.get(field_name)

        # Check if field exists
        if value is None:
            if required:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' is required but not provided"
                )
            return  # Optional field not present, skip further validation

        # Type check
        if not isinstance(value, str):
            raise ValueError(f"Step '{step_name}': '{field_name}' must be a string")

        # Empty string check
        if not allow_empty and not value.strip():
            raise ValueError(
                f"Step '{step_name}': '{field_name}' cannot be empty or whitespace-only"
            )

        # Length checks
        stripped_len = len(value.strip())
        if min_length is not None and stripped_len < min_length:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be at least {min_length} "
                f"characters (got {stripped_len})"
            )
        if max_length is not None and len(value) > max_length:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be at most {max_length} "
                f"characters (got {len(value)})"
            )

        # Pattern check
        if pattern is not None:
            if not re.match(pattern, value):
                desc = pattern_description or f"pattern '{pattern}'"
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' must match {desc} (got '{value}')"
                )

    def _validate_integer_field(
        self,
        field_name: str,
        *,
        required: bool = True,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
        positive: bool = False,
        non_negative: bool = False,
    ) -> None:
        """
        Validate an integer field with comprehensive checks.

        Args:
            field_name: Name of the field to validate
            required: If True, field must exist (default True)
            min_value: Minimum allowed value (inclusive)
            max_value: Maximum allowed value (inclusive)
            positive: If True, value must be > 0
            non_negative: If True, value must be >= 0

        Raises:
            ValueError: If validation fails
        """
        step_name = self._get_step_name()
        value = self.config.get(field_name)

        # Check if field exists
        if value is None:
            if required:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' is required but not provided"
                )
            return  # Optional field not present

        # Type check - allow both int and float that are whole numbers
        if isinstance(value, bool):
            # bool is subclass of int, reject it explicitly
            raise ValueError(f"Step '{step_name}': '{field_name}' must be an integer")
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' must be an integer (got {value})"
                )
            value = int(value)
        elif not isinstance(value, int):
            raise ValueError(f"Step '{step_name}': '{field_name}' must be an integer")

        # Positive check
        if positive and value <= 0:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be a positive integer (got {value})"
            )

        # Non-negative check
        if non_negative and value < 0:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be a non-negative integer (got {value})"
            )

        # Range checks
        if min_value is not None and value < min_value:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be at least {min_value} (got {value})"
            )
        if max_value is not None and value > max_value:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be at most {max_value} (got {value})"
            )

    def _validate_port_field(
        self,
        field_name: str,
        *,
        required: bool = True,
        allow_privileged: bool = True,
    ) -> None:
        """
        Validate a port number field.

        Args:
            field_name: Name of the field to validate
            required: If True, field must exist (default True)
            allow_privileged: If True, allow ports 1-1023 (default True)

        Raises:
            ValueError: If validation fails
        """
        step_name = self._get_step_name()
        value = self.config.get(field_name)

        # Check if field exists
        if value is None:
            if required:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' is required but not provided"
                )
            return

        # Type check - reject booleans explicitly (bool is subclass of int)
        if isinstance(value, bool):
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be a valid port number"
            )

        # Accept whole-number floats (consistent with _validate_integer_field)
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' must be an integer port number (got {value})"
                )
            value = int(value)
        elif not isinstance(value, int):
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be an integer port number"
            )

        # Port range validation with specific error messages
        min_port = 1 if allow_privileged else 1024

        if value < min_port:
            if not allow_privileged and 1 <= value < 1024:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' must be a non-privileged port "
                    f"(1024-65535, got {value})"
                )
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be a valid port number "
                f"({min_port}-65535, got {value})"
            )

        if value > 65535:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be a valid port number "
                f"({min_port}-65535, got {value})"
            )

    def _validate_number_field(
        self,
        field_name: str,
        *,
        required: bool = True,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        positive: bool = False,
        non_negative: bool = False,
    ) -> None:
        """
        Validate a numeric field (int or float) with comprehensive checks.

        Args:
            field_name: Name of the field to validate
            required: If True, field must exist (default True)
            min_value: Minimum allowed value (inclusive)
            max_value: Maximum allowed value (inclusive)
            positive: If True, value must be > 0
            non_negative: If True, value must be >= 0

        Raises:
            ValueError: If validation fails
        """
        step_name = self._get_step_name()
        value = self.config.get(field_name)

        # Check if field exists
        if value is None:
            if required:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' is required but not provided"
                )
            return

        # Type check
        if isinstance(value, bool):
            raise ValueError(f"Step '{step_name}': '{field_name}' must be a number")
        if not isinstance(value, (int, float)):
            raise ValueError(f"Step '{step_name}': '{field_name}' must be a number")

        # Positive check
        if positive and value <= 0:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be a positive number (got {value})"
            )

        # Non-negative check
        if non_negative and value < 0:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be a non-negative number (got {value})"
            )

        # Range checks
        if min_value is not None and value < min_value:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be at least {min_value} (got {value})"
            )
        if max_value is not None and value > max_value:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be at most {max_value} (got {value})"
            )

    def _validate_boolean_field(
        self,
        field_name: str,
        *,
        required: bool = True,
    ) -> None:
        """
        Validate a boolean field.

        Args:
            field_name: Name of the field to validate
            required: If True, field must exist (default True)

        Raises:
            ValueError: If validation fails
        """
        step_name = self._get_step_name()
        value = self.config.get(field_name)

        # Check if field exists
        if value is None:
            if required:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' is required but not provided"
                )
            return

        # Type check
        if not isinstance(value, bool):
            raise ValueError(f"Step '{step_name}': '{field_name}' must be a boolean")

    def _validate_list_field(
        self,
        field_name: str,
        *,
        required: bool = True,
        allow_empty: bool = False,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        element_type: Optional[type] = None,
        element_validator: Optional[Callable[[Any, int, str], None]] = None,
        unique_elements: bool = False,
    ) -> None:
        """
        Validate a list field with comprehensive checks.

        Args:
            field_name: Name of the field to validate
            required: If True, field must exist (default True)
            allow_empty: If False, empty lists are rejected (default False)
            min_length: Minimum list length
            max_length: Maximum list length
            element_type: If provided, all elements must be of this type
            element_validator: Optional callback to validate each element.
                Signature: (element: Any, index: int, field_name: str) -> None.
                Should raise ValueError if validation fails.
            unique_elements: If True, all elements must be unique.
                Note: For unhashable elements, uses O(n²) pairwise comparison.

        Raises:
            ValueError: If validation fails
        """
        step_name = self._get_step_name()
        value = self.config.get(field_name)

        # Check if field exists
        if value is None:
            if required:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' is required but not provided"
                )
            return

        # Type check
        if not isinstance(value, list):
            raise ValueError(f"Step '{step_name}': '{field_name}' must be a list")

        # Empty check
        if not allow_empty and len(value) == 0:
            raise ValueError(f"Step '{step_name}': '{field_name}' cannot be empty")

        # Length checks
        if min_length is not None and len(value) < min_length:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must have at least {min_length} "
                f"elements (got {len(value)})"
            )
        if max_length is not None and len(value) > max_length:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must have at most {max_length} "
                f"elements (got {len(value)})"
            )

        # Element type check
        if element_type is not None:
            for i, elem in enumerate(value):
                if not isinstance(elem, element_type):
                    raise ValueError(
                        f"Step '{step_name}': '{field_name}[{i}]' must be a "
                        f"{element_type.__name__} (got {type(elem).__name__})"
                    )

        # Element validator check
        if element_validator is not None:
            for i, elem in enumerate(value):
                try:
                    element_validator(elem, i, field_name)
                except ValueError:
                    raise
                except Exception as e:
                    raise ValueError(
                        f"Step '{step_name}': '{field_name}[{i}]' validation failed: {e}"
                    ) from e

        # Uniqueness check
        if unique_elements:
            # Handle unhashable elements by comparing directly
            try:
                if len(value) != len(set(value)):
                    raise ValueError(
                        f"Step '{step_name}': '{field_name}' must contain unique elements"
                    )
            except TypeError:
                # Elements are not hashable, do pairwise comparison
                for i in range(len(value)):
                    for j in range(i + 1, len(value)):
                        if value[i] == value[j]:
                            raise ValueError(
                                f"Step '{step_name}': '{field_name}' must contain unique "
                                f"elements (duplicate at indices {i} and {j})"
                            ) from None

    def _validate_dict_field(
        self,
        field_name: str,
        *,
        required: bool = True,
        allow_empty: bool = True,
        required_keys: Optional[list[str]] = None,
        allowed_keys: Optional[list[str]] = None,
    ) -> None:
        """
        Validate a dictionary field with comprehensive checks.

        Args:
            field_name: Name of the field to validate
            required: If True, field must exist (default True)
            allow_empty: If False, empty dicts are rejected (default True)
            required_keys: List of keys that must be present
            allowed_keys: If provided, only these keys are allowed

        Raises:
            ValueError: If validation fails
        """
        step_name = self._get_step_name()
        value = self.config.get(field_name)

        # Check if field exists
        if value is None:
            if required:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' is required but not provided"
                )
            return

        # Type check
        if not isinstance(value, dict):
            raise ValueError(f"Step '{step_name}': '{field_name}' must be a dictionary")

        # Empty check
        if not allow_empty and len(value) == 0:
            raise ValueError(f"Step '{step_name}': '{field_name}' cannot be empty")

        # Required keys check
        if required_keys:
            missing_keys = [key for key in required_keys if key not in value]
            if missing_keys:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' is missing required keys: "
                    f"{', '.join(missing_keys)}"
                )

        # Allowed keys check
        if allowed_keys is not None:
            invalid_keys = [key for key in value.keys() if key not in allowed_keys]
            if invalid_keys:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' contains invalid keys: "
                    f"{', '.join(invalid_keys)}. Allowed keys: {', '.join(allowed_keys)}"
                )

    def _validate_json_string_field(
        self,
        field_name: str,
        *,
        required: bool = True,
    ) -> None:
        """
        Validate a field that should contain a valid JSON string.

        Args:
            field_name: Name of the field to validate
            required: If True, field must exist (default True)

        Raises:
            ValueError: If validation fails
        """
        step_name = self._get_step_name()
        value = self.config.get(field_name)

        # Check if field exists
        if value is None:
            if required:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' is required but not provided"
                )
            return

        # Type check
        if not isinstance(value, str):
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be a JSON string"
            )

        # JSON parsing check
        try:
            json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Step '{step_name}': '{field_name}' must be valid JSON: {e}"
            ) from e

    def _validate_enum_field(
        self,
        field_name: str,
        allowed_values: list[Any],
        *,
        required: bool = True,
        case_sensitive: bool = True,
    ) -> None:
        """
        Validate a field against a list of allowed values.

        Args:
            field_name: Name of the field to validate
            allowed_values: List of valid values
            required: If True, field must exist (default True)
            case_sensitive: If False, string comparisons are case-insensitive

        Raises:
            ValueError: If validation fails
        """
        step_name = self._get_step_name()
        value = self.config.get(field_name)

        # Check if field exists
        if value is None:
            if required:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' is required but not provided"
                )
            return

        # Comparison
        if not case_sensitive and isinstance(value, str):
            value_lower = value.lower()
            allowed_lower = [
                v.lower() if isinstance(v, str) else v for v in allowed_values
            ]
            if value_lower not in allowed_lower:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' must be one of "
                    f"{allowed_values} (got '{value}')"
                )
        else:
            if value not in allowed_values:
                raise ValueError(
                    f"Step '{step_name}': '{field_name}' must be one of "
                    f"{allowed_values} (got '{value}')"
                )

    def _get_node_rpc_url(self, node_name: str) -> str:
        """
        Get the RPC URL for a node using the resolver or fallback to manager.

        This method handles both:
        - Remote nodes (via NodeResolver with auth)
        - Local docker/binary nodes (via NodeResolver or legacy get_node_rpc_url)

        Args:
            node_name: The node name or reference to resolve

        Returns:
            The RPC URL for the node

        Raises:
            Exception: If the node cannot be resolved
        """
        resolved = self._resolve_node(node_name)
        if resolved:
            return resolved.url
        # Legacy fallback for local nodes
        if self.manager is not None:
            return get_node_rpc_url(node_name, self.manager)
        raise Exception(f"Cannot resolve node '{node_name}': no manager available.")

    def _resolve_node(self, node_name: str) -> Optional[Any]:
        """
        Resolve a node and return the ResolvedNode object (includes URL, stable name, and token).

        This method handles both:
        - Remote nodes (via NodeResolver with auth)
        - Local docker/binary nodes (via NodeResolver or legacy get_node_rpc_url)

        Args:
            node_name: The node name or reference to resolve

        Returns:
            ResolvedNode if resolved via resolver, None if using legacy fallback

        Raises:
            Exception: If the node cannot be resolved
        """
        # Try resolver first (handles both remote and local nodes)
        if self.resolver is not None:
            try:
                # Let the resolver handle credential resolution internally
                # via _resolve_credentials() which looks up the registry
                resolved = self.resolver.resolve_sync(
                    node_name,
                    prompt_for_credentials=True,
                    skip_auth=False,
                )
                return resolved
            except Exception as e:
                # Check if this is a remote node - if so, don't fallback
                if self.resolver.is_registered_remote(node_name):
                    raise Exception(
                        f"Failed to resolve remote node '{node_name}': {e}"
                    ) from e
                if self.resolver.is_url(node_name):
                    raise Exception(f"Failed to resolve URL '{node_name}': {e}") from e
                # Log the resolver error but try fallback for local nodes
                console.print(
                    f"[yellow]Warning: Resolver failed for {node_name}: "
                    f"{escape(str(e))}. Trying legacy resolution...[/yellow]"
                )

        # Fallback to legacy get_node_rpc_url (only for local nodes)
        if self.manager is not None:
            return None  # Legacy path - no ResolvedNode
        else:
            # No manager available - this is likely a remote-only workflow
            # and we shouldn't try to create a DockerManager
            raise Exception(
                f"Cannot resolve node '{node_name}': no manager available. "
                f"For remote nodes, ensure the node is configured in remote_nodes."
            )

    def _resolve_node_for_client(self, node_name: str) -> tuple[str, Optional[str]]:
        """
        Resolve a node and return (rpc_url, client_node_name) for client creation.

        This consolidates the common pattern of resolving a node and determining
        whether to pass the node_name to the client for token caching.

        Args:
            node_name: The node name or reference to resolve

        Returns:
            Tuple of (rpc_url, client_node_name) where:
            - rpc_url: The RPC URL for the node
            - client_node_name: The node name for token caching (None if no auth needed)

        Raises:
            Exception: If the node cannot be resolved
        """
        resolved = self._resolve_node(node_name)
        if resolved:
            rpc_url = resolved.url
            # Pass node_name for authenticated nodes OR when embedded auth is enabled
            client_node_name = (
                resolved.node_name
                if (resolved.auth_required or self.auth_mode == "embedded")
                else None
            )
        else:
            # Legacy path for local nodes
            rpc_url = self._get_node_rpc_url(node_name)
            # If embedded auth mode is enabled, pass node_name so client can use cached tokens
            client_node_name = node_name if self.auth_mode == "embedded" else None

        return rpc_url, client_node_name

    def _resolve_node_target(self, node_name: str) -> tuple[str, str]:
        """Resolve a node to its RPC URL and the name its token is cached under."""
        resolved = self._resolve_node(node_name)
        if resolved:
            return resolved.url, resolved.node_name
        return self._get_node_rpc_url(node_name), node_name

    def _resolve_token(
        self,
        cache_node_name: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> Optional[str]:
        """Resolve the JWT to attach: explicit ``token`` field wins, else cache.

        A node running without auth issues no token, so None is a normal answer
        and not an error - the request's own outcome is the auth signal.
        """
        explicit = self.config.get("token")
        if explicit:
            return self._resolve_dynamic_value(
                explicit, workflow_results, dynamic_values
            )
        cached = AuthManager().get_cached_token(cache_node_name)
        return cached.access_token if cached else None

    def _parse_json(self, value: Any) -> Any:
        """Parse JSON string to Python object using a robust multi-strategy parser.

        This is the single source of truth for JSON parsing in the base step.
        It implements the following strategies in order of precedence:

        1. STANDARD JSON: Direct json.loads() parsing
        2. PYTHON LITERALS: Uses ast.literal_eval() for Python-style dicts/lists
           (e.g., "{'key': 'value'}" with single quotes)
        3. TRAILING COMMAS: Cleans trailing commas before JSON parsing
           (e.g., '{"key": "value",}')
        4. SUBSTRING EXTRACTION: Extracts first valid JSON object/array from
           text containing other content (e.g., "prefix {\"key\": 1} suffix")

        Args:
            value: The value to parse. Non-strings are returned unchanged.

        Returns:
            Parsed Python object (dict, list, str, int, float, bool, None)
            or the original value if all parsing strategies fail.

        Warning:
            Strategy 4 (substring extraction) is permissive and may extract
            unintended JSON from mixed content. When processing untrusted input,
            validate the extracted data matches expected schema before use.

        Note:
            For value extraction with path traversal, use _get_value() which
            calls this method internally at each path segment.
        """
        if not isinstance(value, str):
            return value

        s = value.strip()
        if not s:
            return value

        # Strategy 1: Standard JSON parsing
        try:
            return json.loads(s)
        except Exception:
            pass

        # Strategy 2: Python-style literals (single quotes, etc.)
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, (dict, list, str, int, float, bool, type(None))):
                return parsed
        except Exception:
            pass

        # Strategy 3: JSON with trailing commas
        if "{" in s or "[" in s:
            try:
                cleaned = re.sub(r",(\s*[}\]])", r"\1", s)
                return json.loads(cleaned)
            except Exception:
                pass

        # Strategy 4: Extract JSON substring from noisy input
        if "{" in s or "[" in s:
            try:
                candidate = self._find_json_substring(s)
                if candidate:
                    return json.loads(candidate)
            except Exception:
                pass

        return value

    def _find_json_substring(self, text: str) -> Optional[str]:
        """Extract first complete JSON object or array from text.

        Used by _parse_json() as Strategy 5 for extracting JSON from noisy input.
        Finds balanced braces/brackets to identify complete JSON structures.

        Args:
            text: Input text potentially containing JSON embedded in other content.

        Returns:
            The first complete JSON object or array string found, or None.
        """
        for match in re.finditer(r"[\{\[]", text):
            pos = match.start()
            opening = match.group()
            closing = "}" if opening == "{" else "]"
            stack = [opening]

            for i in range(pos + 1, len(text)):
                if text[i] == opening:
                    stack.append(opening)
                elif text[i] == closing:
                    stack.pop()
                    if not stack:
                        return text[pos : i + 1]
        return None

    def _get_value(self, obj: Any, path: str, default: Any = None) -> Any:
        """Unified value extraction with automatic JSON parsing.

        This is the primary method for extracting values from nested structures.
        It consolidates JSON parsing (via _parse_json) with path traversal,
        eliminating the need for separate JSON parsing calls.

        Works for both simple keys and nested paths:
        - Simple key: "field" -> obj["field"]
        - Nested path: "result.data.value" -> obj["result"]["data"]["value"]
        - Array index: "items.0.id" -> obj["items"][0]["id"]
        - Mixed: "result.items.0.name" -> obj["result"]["items"][0]["name"]

        JSON strings are automatically parsed at each path segment, so:
        - obj = {"result": '{"data": "value"}'}
        - _get_value(obj, "result.data") returns "value"

        Args:
            obj: The object to extract from (dict, list, or JSON string)
            path: Key or dot-separated path to the desired field
            default: Returned when the path cannot be resolved. Pass a sentinel
                to tell an unresolvable path apart from a resolved null.

        Returns:
            The value at the specified path (with JSON parsing applied),
            or `default` if the path cannot be resolved.

        Note:
            Use this method instead of dict.get() + _parse_json() for
            consistent JSON-aware value extraction.
        """
        if not isinstance(path, str) or not path:
            return default

        current = obj
        segments = path.split(".")

        for segment in segments:
            current = self._parse_json(current)

            # Handle array index
            if isinstance(current, list) and segment.isdigit():
                idx = int(segment)
                if 0 <= idx < len(current):
                    current = current[idx]
                    continue
                return default

            # Handle dict key
            if isinstance(current, dict) and segment in current:
                current = current[segment]
                continue

            return default

        return self._parse_json(current) if isinstance(current, str) else current

    def _export_variable(
        self,
        dynamic_values: dict[str, Any],
        source_field: str,
        target_key: str,
        value: Any,
        description: str = None,
    ) -> None:
        """
        Export a variable to dynamic_values with explicit documentation.

        Args:
            dynamic_values: The dynamic values dictionary to update
            source_field: The source field name from the API response
            target_key: The target key in dynamic_values
            value: The value to export
            description: Optional description of what this variable represents
        """
        if value is not None:
            dynamic_values[target_key] = value
            desc_text = f" ({description})" if description else ""
            console.print(
                f"[blue]📝 Exported {source_field} → {target_key}: {value}{desc_text}[/blue]"
            )
        else:
            console.print(
                f"[yellow]⚠️  Could not export {source_field} → {target_key} (value is None)[/yellow]"
            )

    @staticmethod
    def _unwrap_response(response_data: Any) -> tuple[bool, Any]:
        """An error report carries its fields at the top level while a success
        payload nests them under `data`, so both export passes unwrap by it."""
        if not isinstance(response_data, dict):
            return False, response_data
        is_error_info = response_data.get("success") is False and any(
            key in response_data
            for key in ["error_code", "error_type", "error_message"]
        )
        if is_error_info:
            return True, response_data
        return False, response_data.get("data", response_data)

    def _export_variables_from_response(
        self,
        response_data: dict[str, Any],
        node_name: str,
        dynamic_values: dict[str, Any],
    ) -> None:
        """
        Export variables automatically based on exportable_variables configuration.
        This method is called for backward compatibility when no custom outputs are specified.

        Args:
            response_data: The API response data
            node_name: The name of the node
            dynamic_values: The dynamic values dictionary to update
        """
        if not self.exportable_variables:
            return

        _, actual_data = self._unwrap_response(response_data)

        for source_field, target_key_template, description in self.exportable_variables:
            # Replace placeholders in target_key_template
            target_key = target_key_template.replace("{node_name}", node_name)

            # Skip if this variable has already been exported by custom outputs
            if target_key in dynamic_values:
                continue

            # Extract value from response
            if isinstance(actual_data, dict):
                value = actual_data.get(source_field)
            else:
                value = None

            # Export the variable
            self._export_variable(
                dynamic_values, source_field, target_key, value, description
            )

    def _export_custom_outputs(
        self,
        response_data: dict[str, Any],
        node_name: str,
        dynamic_values: dict[str, Any],
        protected_keys: Optional[set[str]] = None,
    ) -> None:
        """
        Export variables based on custom outputs configuration specified by the user.

        A capture the response cannot satisfy raises: the placeholder would stay
        unbound, and an unbound one reads as the literal "{{name}}".

        Args:
            response_data: The API response data
            node_name: The name of the node
            dynamic_values: The dynamic values dictionary to update
            protected_keys: Set of keys that should not be overwritten (e.g., error field exports)
        """
        outputs_config = self.config.get("outputs", {})
        if not outputs_config:
            return

        if protected_keys is None:
            protected_keys = set()

        is_error_info, actual_data = self._unwrap_response(response_data)

        vprint(
            f"[cyan]📝 Exporting variables from {node_name} response:[/cyan]",
            level=LOG_LEVEL_VERBOSE,
        )

        for exported_variable, assigned_var in outputs_config.items():
            source, target_key, value = self._resolve_capture(
                exported_variable, assigned_var, actual_data, node_name
            )

            # Before the bind is required: a protected key is already bound by
            # the error pass, so the second payload owes it nothing.
            if target_key in protected_keys:
                console.print(
                    f"[yellow]⚠️  Skipped export to protected key '{target_key}' (error field export)[/yellow]"
                )
                continue

            if value is _MISSING:
                # An error report carries the error fields and never the call's
                # own, so `expected_failure` captures bind one half per outcome.
                if is_error_info:
                    vprint(
                        f"[yellow]   ⚠️  {exported_variable}: '{source}' not in the "
                        f"response, left unset[/yellow]",
                        level=LOG_LEVEL_VERBOSE,
                    )
                    continue
                raise OutputCaptureError(
                    f"Step '{self._get_step_name()}': output '{exported_variable}' "
                    f"captures '{source}', which the response does not have. "
                    f"Available: {_describe_keys(actual_data)}",
                    step_name=self._get_step_name(),
                    step_type=self.config.get("type"),
                )

            dynamic_values[target_key] = value

            display_value = str(value)
            if len(display_value) > 100:
                display_value = display_value[:97] + "..."

            vprint(
                f"[green]   ✓[/green] [bold cyan]{target_key}[/bold cyan] [dim]=[/dim] {display_value}",
                level=LOG_LEVEL_VERBOSE,
            )

    def _resolve_capture(
        self,
        exported_variable: str,
        assigned_var: Any,
        actual_data: Any,
        node_name: str,
    ) -> tuple[str, str, Any]:
        """Read one `outputs:` entry, as ``(source, target_key, value)``.

        Dispatch only - the caller owns the verdict on an unbindable value, so
        that rule stays in one place instead of once per capture form.
        """
        if isinstance(assigned_var, str):
            # _get_value takes simple keys and dotted paths alike, parsing JSON
            # at each segment.
            return (
                assigned_var,
                exported_variable,
                self._get_value(actual_data, assigned_var, default=_MISSING),
            )

        if isinstance(assigned_var, dict) and "field" in assigned_var:
            source = assigned_var["field"]
            target_key = assigned_var.get("target", exported_variable).replace(
                "{node_name}", node_name
            )
            # Literal key lookup (not path traversal) for backward compatibility
            value = (
                actual_data.get(source, _MISSING)
                if isinstance(actual_data, dict)
                else _MISSING
            )
            if value is not _MISSING:
                # JSON parse only when explicitly requested
                if assigned_var.get("json"):
                    value = self._parse_json(value)
                # Optional nested path within the field's value
                path = assigned_var.get("path")
                if isinstance(path, str):
                    source = f"{source}.{path}"
                    value = self._get_value(value, path, default=_MISSING)
            return source, target_key, value

        raise OutputCaptureError(
            f"Step '{self._get_step_name()}': output '{exported_variable}' "
            f"is configured as {assigned_var!r}, which is neither a source "
            f"field name nor a mapping with a 'field' key",
            step_name=self._get_step_name(),
            step_type=self.config.get("type"),
        )

    def _export_variables(
        self,
        response_data: dict[str, Any],
        node_name: str,
        dynamic_values: dict[str, Any],
        protected_keys: Optional[set[str]] = None,
    ) -> None:
        """
        Main export method that handles only custom outputs (explicit exports).

        Args:
            response_data: The API response data
            node_name: The name of the node
            dynamic_values: The dynamic values dictionary to update
            protected_keys: Set of keys that should not be overwritten (e.g., error field exports)
        """
        # Only handle custom outputs - no automatic exports
        if "outputs" in self.config:
            self._export_custom_outputs(
                response_data, node_name, dynamic_values, protected_keys
            )
        else:
            # Advisory only (most steps intentionally export nothing) — verbose.
            vprint(
                "[yellow]⚠️  No outputs configured for this step. Variables will not be exported automatically.[/yellow]",
                level=LOG_LEVEL_VERBOSE,
            )
            vprint(
                "[yellow]   To export variables, add an 'outputs' section to your step configuration.[/yellow]",
                level=LOG_LEVEL_VERBOSE,
            )

    def _validate_export_config(self) -> bool:
        """
        Validate that the step configuration properly defines exportable variables.
        Returns True if valid, False otherwise.
        """
        # Check if custom outputs are configured
        if "outputs" in self.config:
            outputs_config = self.config["outputs"]
            if not isinstance(outputs_config, dict):
                console.print(
                    f"[yellow]⚠️  Step {self.__class__.__name__} has invalid outputs config: must be a dictionary[/yellow]"
                )
                return False

            # Validate each output configuration
            for exported_var, assigned_var in outputs_config.items():
                if not isinstance(exported_var, str):
                    console.print(
                        f"[yellow]⚠️  Invalid output key '{exported_var}': must be a string[/yellow]"
                    )
                    return False

                if isinstance(assigned_var, str):
                    # Simple string assignment is valid
                    pass
                elif isinstance(assigned_var, dict):
                    # Complex assignment must have 'field' key
                    if "field" not in assigned_var:
                        console.print(
                            f"[yellow]⚠️  Invalid output config for '{exported_var}': missing 'field' key[/yellow]"
                        )
                        return False
                else:
                    console.print(
                        f"[yellow]⚠️  Invalid output config for '{exported_var}': must be string or dict[/yellow]"
                    )
                    return False

            vprint(
                f"[blue]✅ Custom outputs configuration validated: {len(outputs_config)} outputs defined[/blue]",
                level=LOG_LEVEL_VERBOSE,
            )
            return True

        # Check if automatic exports are configured
        if not hasattr(self, "exportable_variables") or not self.exportable_variables:
            vprint(
                f"[yellow]⚠️  Step {self.__class__.__name__} has no exportable variables defined[/yellow]",
                level=LOG_LEVEL_VERBOSE,
            )
            return False

        vprint(
            f"[blue]✅ Automatic exports configuration validated: {len(self.exportable_variables)} variables defined[/blue]",
            level=LOG_LEVEL_VERBOSE,
        )
        return True

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        """Execute the step. Must be implemented by subclasses."""
        raise NotImplementedError

    def _is_expected_failure(self) -> bool:
        """Return True if the step is configured with `expected_failure: true`.

        Raises ValueError if the flag is present but not a boolean — caught at
        validation time so negative-path workflows fail fast on bad config.
        """
        value = self.config.get("expected_failure", False)
        if not isinstance(value, bool):
            raise ValueError(
                f"Step '{self._get_step_name()}': 'expected_failure' must be a boolean"
            )
        return value

    def _validate_expected_error(self) -> None:
        """`expected_error` without `expected_failure` never asserts anything,
        so that pairing is a config error rather than a silent no-op."""
        if self.config.get("expected_error") is None:
            return
        self._validate_string_field("expected_error", required=False)
        if not self._is_expected_failure():
            raise ValueError(
                f"Step '{self._get_step_name()}': 'expected_error' requires "
                f"'expected_failure: true' - on its own it never asserts anything"
            )

    def bind_expected_error(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> None:
        """Resolve `expected_error`'s placeholders before the step can fail.

        `_report_expected_failure` has no resolution context of its own, so the
        executors that own both dicts bind the value ahead of the step.
        """
        expected = self.config.get("expected_error")
        if not isinstance(expected, str):
            return
        resolved = str(
            self._resolve_dynamic_value(expected, workflow_results, dynamic_values)
        )
        # A pin resolving to blank is a substring of every error, so it would
        # accept any failure. Keep the raw form: it fails closed and names itself.
        self._expected_error = resolved if resolved.strip() else expected

    def _failure_detail(self, result: dict[str, Any]) -> str:
        """`fail()` files the step's own message under `error` and the cause
        under `exception.message`; `expected_error` has to match the cause."""
        message = str(result.get("error", "Unknown error"))
        exception = result.get("exception")
        cause = exception.get("message") if isinstance(exception, dict) else None
        # Call sites usually build the message as f"<verb> failed: {e}" from the
        # same exception, so appending unconditionally would print it twice.
        if not cause or cause in message:
            return message
        return f"{message}: {cause}"

    def _jsonrpc_error_detail(self, result_data: Any) -> str:
        """Flatten a JSON-RPC error envelope onto one line. merod puts the
        refusal reason in `error.data` on some routes and `error.type` on
        others, so `expected_error` gets both verbatim."""
        error = result_data.get("error") if isinstance(result_data, dict) else None
        if isinstance(error, dict):
            return (
                f"JSON-RPC error returned: {error.get('type', 'Unknown')} - "
                f"{error.get('data', 'No details')}"
            )
        if error is not None:
            return f"JSON-RPC error returned: {error}"
        return "JSON-RPC error returned"

    def _report_expected_failure(self, error_message: str) -> bool:
        """Report an expected failure, returning whether it was the RIGHT one.

        Any failure satisfies a step with no `expected_error`; with one, only a
        message containing it (case-sensitive), so an unreachable node cannot
        stand in for the refusal under test.
        """
        expected = self._expected_error
        if expected is not None and expected not in error_message:
            # markup=False so an error body containing brackets survives Rich.
            console.print(
                f"✗ expected the failure to mention {expected!r}, "
                f"but it was: {error_message}",
                style="red",
                markup=False,
                highlight=False,
            )
            return False
        # error_message may carry raw exception text whose brackets (e.g. a
        # multiaddr) Rich would otherwise parse as markup.
        console.print(
            f"[yellow]✓ Expected failure occurred: {escape(error_message)}[/yellow]"
        )
        return True

    def _report_unexpected_success(self) -> bool:
        """A negative test whose subject succeeds has been disproven, so passing
        here would make every gate the flag guards unable to fail."""
        console.print("[red]✗ expected_failure was set but the step succeeded[/red]")
        return False

    def _is_connectivity_error(self, error_message: str) -> bool:
        """Return True if an error message looks like a network/connectivity fault.

        Auth steps use this to keep ``expected_failure`` honest: a negative auth
        test (e.g. "bad credentials are rejected") must assert an *auth* failure,
        not silently pass because the node was unreachable. ``AuthManager`` wraps
        transport faults with a "Network error ..." prefix, while a genuine
        rejection surfaces as "Invalid credentials" / "Access forbidden".
        """
        if not error_message:
            return False
        lowered = error_message.lower()
        markers = (
            "network error",
            "connection refused",
            "cannot connect",
            "connection reset",
            "timed out",
            "timeout",
            "name or service not known",
            "temporary failure in name resolution",
        )
        return any(marker in lowered for marker in markers)

    def _check_jsonrpc_error(self, result_data: Any) -> bool:
        """
        Check if the API response contains a JSON-RPC error.

        Args:
            result_data: The 'data' field from the API response

        Returns:
            True if a JSON-RPC error was found (workflow should fail), False otherwise
        """
        if isinstance(result_data, dict) and "error" in result_data:
            # JSON-RPC error - fail the workflow
            error_info = result_data["error"]
            if isinstance(error_info, dict):
                error_type = error_info.get("type", "Unknown")
                error_data = error_info.get("data", "No details")
                console.print(f"[red]JSON-RPC Error: {error_type} - {error_data}[/red]")
            else:
                console.print(f"[red]JSON-RPC Error: {error_info}[/red]")
            return True
        return False

    def _print_node_logs_on_failure(
        self, node_name: Optional[str] = None, lines: int = 50
    ) -> None:
        """
        Print recent node logs when a step fails to help with debugging.

        Args:
            node_name: Name of the node to get logs from. If None, tries to extract from step config.
            lines: Number of log lines to show (default: 50)
        """
        # Try to get node name from config if not provided
        if node_name is None:
            node_name = self.config.get("node")

        if not node_name:
            # No node associated with this step, skip log printing
            return

        # Check if this is a remote node - can't get logs from remote nodes
        if self.resolver is not None:
            if self.resolver.is_registered_remote(node_name):
                console.print(
                    f"[dim]📋 Cannot retrieve logs for remote node '{node_name}'[/dim]"
                )
                return
            # Also check if it's a URL
            if self.resolver.is_url(node_name):
                console.print(
                    f"[dim]📋 Cannot retrieve logs for URL-based node '{node_name}'[/dim]"
                )
                return

        if not self.manager:
            # No manager available, skip log printing
            return

        try:
            console.print(
                f"\n[yellow]📋 Recent logs from node '{node_name}' (last {lines} lines):[/yellow]"
            )
            console.print("[dim]" + "=" * 80 + "[/dim]")

            # Check if we're in binary mode
            is_binary_mode = (
                hasattr(self.manager, "binary_path")
                and self.manager.binary_path is not None
            )

            if is_binary_mode:
                # Binary mode - use BinaryManager's get_node_logs
                log_content = self.manager.get_node_logs(node_name, lines=lines)
                if log_content:
                    # Node output may contain literal square brackets (e.g. a
                    # multiaddr list) that Rich would otherwise try to parse as markup.
                    console.print(log_content, markup=False)
                else:
                    console.print(f"[dim]No logs found for {node_name}[/dim]")
            else:
                # Docker mode - DockerManager.get_node_logs prints with its own formatting
                # We'll call it directly, but suppress its header since we have our own
                try:
                    # Get the container and logs directly to avoid double formatting
                    if node_name in self.manager.nodes:
                        container = self.manager.nodes[node_name]
                    else:
                        container = self.manager.client.containers.get(node_name)

                    logs = container.logs(tail=lines, timestamps=True).decode("utf-8")
                    if logs:
                        console.print(logs, markup=False)
                    else:
                        console.print(f"[dim]No logs available for {node_name}[/dim]")
                except Exception as e:
                    console.print(f"[dim]Could not retrieve logs: {str(e)}[/dim]")

            console.print("[dim]" + "=" * 80 + "[/dim]\n")
        except Exception as e:
            # Don't let log printing failures break the workflow
            console.print(f"[dim]Could not print node logs: {str(e)}[/dim]")

    def _resolve_dynamic_value(
        self,
        value: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> str:
        """Resolve dynamic values using placeholders and captured results.

        Every unresolved branch below hands the input straight back, so identity
        is the only reliable signal that nothing bound.
        """
        resolved = self._resolve_dynamic_value_impl(
            value, workflow_results, dynamic_values
        )
        if (
            self.strict_placeholders
            and resolved is value
            and isinstance(value, str)
            and "{{" in value
            and "}}" in value
        ):
            raise UnresolvedPlaceholderError(
                f"Step '{self._get_step_name()}': {value!r} contains a placeholder "
                f"that never resolved; comparing against it compares against its "
                f"own text",
                step_name=self._get_step_name(),
                step_type=self.config.get("type"),
            )
        return resolved

    def _resolve_dynamic_value_impl(
        self,
        value: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> str:
        if not isinstance(value, str):
            return value

        # Strip quotes from simple string literals (e.g., 'value' or "value" -> value)
        # This helps with assertions where string literals are quoted
        if len(value) >= 2:
            if (value.startswith("'") and value.endswith("'")) or (
                value.startswith('"') and value.endswith('"')
            ):
                # Only strip if it's a simple string literal without placeholders
                if "{{" not in value and "}}" not in value:
                    return value[1:-1]

        # Check if there are any placeholders in the string (embedded or complete)
        if "{{" in value and "}}" in value:
            # Handle complete placeholder strings first (e.g., "{{current_iteration}}")
            if value.startswith("{{") and value.endswith("}}"):
                placeholder = value[2:-2].strip()

                # First, check if this is a simple custom output variable name
                if placeholder in dynamic_values:
                    return dynamic_values[placeholder]

                # Handle different placeholder types
                if placeholder.startswith("install."):
                    # Format: {{install.node_name}}
                    parts = placeholder.split(".", 1)
                    if len(parts) == 2:
                        node_name = parts[1]
                        # First try to get from dynamic values (captured application ID)
                        dynamic_key = f"app_id_{node_name}"
                        if dynamic_key in dynamic_values:
                            app_id = dynamic_values[dynamic_key]
                            return app_id

                        # Fallback to workflow results
                        install_key = f"install_{node_name}"
                        if install_key in workflow_results:
                            result = workflow_results[install_key]
                            # Try to extract application ID from the result
                            if isinstance(result, dict):
                                return result.get(
                                    "id",
                                    result.get(
                                        "applicationId", result.get("name", value)
                                    ),
                                )
                            return str(result)
                        else:
                            console.print(
                                f"[yellow]Warning: Install result for {node_name} not found, using placeholder[/yellow]"
                            )
                            return value

                elif placeholder.startswith("context."):
                    # Format: {{context.node_name}} or {{context.node_name.field}}
                    parts = placeholder.split(".", 1)
                    if len(parts) == 2:
                        node_part = parts[1]
                        # Check if there's a field specification (e.g., context.node_name.memberPublicKey)
                        if "." in node_part:
                            node_name, field_name = node_part.split(".", 1)
                        else:
                            node_name = node_part
                            field_name = None

                        if field_name:
                            # For field access (e.g., memberPublicKey), look in workflow_results
                            context_key = f"context_{node_name}"
                            if context_key in workflow_results:
                                result = workflow_results[context_key]
                                # Try to extract specific field from the result
                                if isinstance(result, dict):
                                    # Handle nested data structure
                                    actual_data = result.get("data", result)
                                    return actual_data.get(field_name, value)
                            else:
                                console.print(
                                    f"[yellow]Warning: Context result for {node_name} not found, using placeholder[/yellow]"
                                )
                                return value
                        else:
                            # For context ID access, look in dynamic_values first
                            context_id_key = f"context_id_{node_name}"
                            if context_id_key in dynamic_values:
                                return dynamic_values[context_id_key]

                            # Fallback to workflow_results
                            context_key = f"context_{node_name}"
                            if context_key in workflow_results:
                                result = workflow_results[context_key]
                                # Try to extract context ID from the result
                                if isinstance(result, dict):
                                    # Handle nested data structure
                                    actual_data = result.get("data", result)
                                    return actual_data.get(
                                        "id",
                                        actual_data.get(
                                            "contextId", actual_data.get("name", value)
                                        ),
                                    )
                                return str(result)
                            else:
                                console.print(
                                    f"[yellow]Warning: Context result for {node_name} not found, using placeholder[/yellow]"
                                )
                                return value

                elif placeholder.startswith("identity."):
                    # Format: {{identity.node_name}}
                    parts = placeholder.split(".", 1)
                    if len(parts) == 2:
                        node_name = parts[1]
                        identity_key = f"identity_{node_name}"
                        if identity_key in workflow_results:
                            result = workflow_results[identity_key]
                            # Try to extract public key from the result
                            if isinstance(result, dict):
                                # Handle nested data structure
                                actual_data = result.get("data", result)
                                return actual_data.get(
                                    "publicKey",
                                    actual_data.get(
                                        "id", actual_data.get("name", value)
                                    ),
                                )
                            return str(result)
                        else:
                            console.print(
                                f"[yellow]Warning: Identity result for {node_name} not found, using placeholder[/yellow]"
                            )
                            return value

                elif placeholder.startswith("invite."):
                    # Format: {{invite.node_name_identity.node_name}}
                    parts = placeholder.split(".", 1)
                    if len(parts) == 2:
                        invite_part = parts[1]
                        # Parse the format: node_name_identity.node_name
                        if "_identity." in invite_part:
                            inviter_node, identity_node = invite_part.split(
                                "_identity.", 1
                            )
                            # First resolve the identity to get the actual public key
                            identity_placeholder = f"{{{{identity.{identity_node}}}}}"
                            actual_identity = self._resolve_dynamic_value(
                                identity_placeholder, workflow_results, dynamic_values
                            )

                            # Now construct the invite key using the actual identity
                            invite_key = f"invite_{inviter_node}_{actual_identity}"

                            if invite_key in workflow_results:
                                result = workflow_results[invite_key]
                                # Try to extract invitation data from the result
                                if isinstance(result, dict):
                                    # Handle nested data structure
                                    actual_data = result.get("data", result)
                                    return actual_data.get(
                                        "invitation",
                                        actual_data.get(
                                            "id", actual_data.get("name", value)
                                        ),
                                    )
                                return str(result)
                            else:
                                console.print(
                                    f"[yellow]Warning: Invite result for {invite_key} not found, using placeholder[/yellow]"
                                )
                                return value
                        else:
                            console.print(
                                f"[yellow]Warning: Invalid invite placeholder format {placeholder}, using as-is[/yellow]"
                            )
                            return value

                elif placeholder in dynamic_values:
                    return dynamic_values[placeholder]

                # Handle iteration placeholders
                elif placeholder.startswith("iteration"):
                    # Format: {{iteration}}, {{iteration_index}}, etc.
                    if placeholder in dynamic_values:
                        return str(dynamic_values[placeholder])
                    else:
                        console.print(
                            f"[yellow]Warning: Iteration placeholder {placeholder} not found, using as-is[/yellow]"
                        )
                        return value

                else:
                    console.print(
                        f"[yellow]Warning: Unknown placeholder {placeholder}, using as-is[/yellow]"
                    )
                    return value

            else:
                # Handle embedded placeholders within strings (e.g., "complex_key_{{current_iteration}}_b")
                result = value
                start = 0
                while True:
                    # Find the next placeholder
                    placeholder_start = result.find("{{", start)
                    if placeholder_start == -1:
                        break

                    placeholder_end = result.find("}}", placeholder_start)
                    if placeholder_end == -1:
                        break

                    # Extract the placeholder content
                    placeholder = result[
                        placeholder_start + 2 : placeholder_end
                    ].strip()

                    # Resolve the placeholder
                    resolved_value = self._resolve_single_placeholder(
                        placeholder, workflow_results, dynamic_values
                    )

                    # Replace the placeholder in the result string
                    result = (
                        result[:placeholder_start]
                        + str(resolved_value)
                        + result[placeholder_end + 2 :]
                    )

                    # Update start position for next search
                    start = placeholder_start + len(str(resolved_value))

                return result

        return value

    def _resolve_single_placeholder(
        self,
        placeholder: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> str:
        """Resolve a single placeholder without the {{}} wrapper.

        An embedded miss is substituted as its own NAME, braces gone, so a scan
        of the resolved string cannot find it - hence the check here.
        """
        resolved = self._resolve_single_placeholder_impl(
            placeholder, workflow_results, dynamic_values
        )
        if self.strict_placeholders and resolved is placeholder:
            raise UnresolvedPlaceholderError(
                f"Step '{self._get_step_name()}': placeholder '{{{{{placeholder}}}}}' "
                f"never resolved; comparing against it compares against its own text",
                step_name=self._get_step_name(),
                step_type=self.config.get("type"),
            )
        return resolved

    def _resolve_single_placeholder_impl(
        self,
        placeholder: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> str:
        # First, check if this is a simple custom output variable name
        if placeholder in dynamic_values:
            return dynamic_values[placeholder]

        # Handle different placeholder types
        if placeholder.startswith("install."):
            # Format: install.node_name
            parts = placeholder.split(".", 1)
            if len(parts) == 2:
                node_name = parts[1]
                # First try to get from dynamic values (captured application ID)
                dynamic_key = f"app_id_{node_name}"
                if dynamic_key in dynamic_values:
                    app_id = dynamic_values[dynamic_key]
                    return app_id

                # Fallback to workflow results
                install_key = f"install_{node_name}"
                if install_key in workflow_results:
                    result = workflow_results[install_key]
                    # Try to extract application ID from the result
                    if isinstance(result, dict):
                        return result.get(
                            "id",
                            result.get(
                                "applicationId", result.get("name", placeholder)
                            ),
                        )
                    return str(result)
                else:
                    console.print(
                        f"[yellow]Warning: Install result for {node_name} not found, using placeholder[/yellow]"
                    )
                    return placeholder

        elif placeholder.startswith("context."):
            # Format: context.node_name or context.node_name.field
            parts = placeholder.split(".", 1)
            if len(parts) == 2:
                node_part = parts[1]
                # Check if there's a field specification (e.g., context.node_name.memberPublicKey)
                if "." in node_part:
                    node_name, field_name = node_part.split(".", 1)
                else:
                    node_name = node_part
                    field_name = None

                if field_name:
                    # For field access (e.g., memberPublicKey), look in workflow_results
                    context_key = f"context_{node_name}"
                    if context_key in workflow_results:
                        result = workflow_results[context_key]
                        # Try to extract specific field from the result
                        if isinstance(result, dict):
                            # Handle nested data structure
                            actual_data = result.get("data", result)
                            return actual_data.get(field_name, placeholder)
                    else:
                        console.print(
                            f"[yellow]Warning: Context result for {node_name} not found, using placeholder[/yellow]"
                        )
                        return placeholder
                else:
                    # For context ID access, look in dynamic_values first
                    context_id_key = f"context_id_{node_name}"
                    if context_id_key in dynamic_values:
                        return dynamic_values[context_id_key]

                    # Fallback to workflow_results
                    context_key = f"context_{node_name}"
                    if context_key in workflow_results:
                        result = workflow_results[context_key]
                        # Try to extract context ID from the result
                        if isinstance(result, dict):
                            # Handle nested data structure
                            actual_data = result.get("data", result)
                            return actual_data.get(
                                "id",
                                actual_data.get(
                                    "contextId", actual_data.get("name", placeholder)
                                ),
                            )
                        return str(result)
                    else:
                        console.print(
                            f"[yellow]Warning: Context result for {node_name} not found, using placeholder[/yellow]"
                        )
                        return placeholder

        elif placeholder.startswith("identity."):
            # Format: identity.node_name
            parts = placeholder.split(".", 1)
            if len(parts) == 2:
                node_name = parts[1]
                identity_key = f"identity_{node_name}"
                if identity_key in workflow_results:
                    result = workflow_results[identity_key]
                    # Try to extract public key from the result
                    if isinstance(result, dict):
                        # Handle nested data structure
                        actual_data = result.get("data", result)
                        return actual_data.get(
                            "publicKey",
                            actual_data.get("id", actual_data.get("name", placeholder)),
                        )
                    return str(result)
                else:
                    console.print(
                        f"[yellow]Warning: Identity result for {node_name} not found, using placeholder[/yellow]"
                    )
                    return placeholder

        elif placeholder.startswith("invite."):
            # Format: invite.node_name_identity.node_name
            parts = placeholder.split(".", 1)
            if len(parts) == 2:
                invite_part = parts[1]
                # Parse the format: node_name_identity.node_name
                if "_identity." in invite_part:
                    inviter_node, identity_node = invite_part.split("_identity.", 1)
                    # First resolve the identity to get the actual public key
                    identity_placeholder = f"{{{{identity.{identity_node}}}}}"
                    actual_identity = self._resolve_dynamic_value(
                        identity_placeholder, workflow_results, dynamic_values
                    )

                    # Now construct the invite key using the actual identity
                    invite_key = f"invite_{inviter_node}_{actual_identity}"

                    if invite_key in workflow_results:
                        result = workflow_results[invite_key]
                        # Try to extract invitation data from the result
                        if isinstance(result, dict):
                            # Handle nested data structure
                            actual_data = result.get("data", result)
                            return actual_data.get(
                                "invitation",
                                actual_data.get(
                                    "id", actual_data.get("name", placeholder)
                                ),
                            )
                        return str(result)
                    else:
                        console.print(
                            f"[yellow]Warning: Invite result for {invite_key} not found, using placeholder[/yellow]"
                        )
                        return placeholder
                else:
                    console.print(
                        f"[yellow]Warning: Invalid invite placeholder format {placeholder}, using as-is[/yellow]"
                    )
                    return placeholder

        # Handle iteration placeholders
        elif placeholder.startswith("iteration"):
            # Format: iteration, iteration_index, etc.
            if placeholder in dynamic_values:
                return str(dynamic_values[placeholder])
            else:
                console.print(
                    f"[yellow]Warning: Iteration placeholder {placeholder} not found, using as-is[/yellow]"
                )
                return placeholder

        else:
            console.print(
                f"[yellow]Warning: Unknown placeholder {placeholder}, using as-is[/yellow]"
            )
            return placeholder
