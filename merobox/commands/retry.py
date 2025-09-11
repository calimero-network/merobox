"""
Retry and timeout utilities for network calls.
"""

import asyncio
from functools import wraps
from typing import Any, Callable, Optional

from merobox.commands.constants import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF,
    DEFAULT_RETRY_DELAY,
)


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        delay: float = DEFAULT_RETRY_DELAY,
        backoff: float = DEFAULT_RETRY_BACKOFF,
        connection_timeout: float = DEFAULT_CONNECTION_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        exceptions: tuple = (Exception,),
    ):
        self.max_attempts = max_attempts
        self.delay = delay
        self.backoff = backoff
        self.connection_timeout = connection_timeout
        self.read_timeout = read_timeout
        self.exceptions = exceptions


def with_retry(
    config: Optional[RetryConfig] = None,
    exceptions: Optional[tuple] = None,
    max_attempts: Optional[int] = None,
    delay: Optional[float] = None,
    backoff: Optional[float] = None,
):
    """
    Decorator to add retry logic to async functions.

    Args:
        config: RetryConfig instance (takes precedence over individual params)
        exceptions: Tuple of exception types to retry on
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Exponential backoff multiplier
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Use provided config or create default
            retry_config = config or RetryConfig(
                max_attempts=max_attempts or DEFAULT_RETRY_ATTEMPTS,
                delay=delay or DEFAULT_RETRY_DELAY,
                backoff=backoff or DEFAULT_RETRY_BACKOFF,
                exceptions=exceptions or (Exception,),
            )

            last_exception = None
            current_delay = retry_config.delay

            for attempt in range(retry_config.max_attempts):
                try:
                    return await func(*args, **kwargs)
                except retry_config.exceptions as e:
                    last_exception = e

                    # Don't retry on the last attempt
                    if attempt == retry_config.max_attempts - 1:
                        break

                    # Wait before retrying
                    await asyncio.sleep(current_delay)
                    current_delay *= retry_config.backoff

            # If we get here, all retries failed
            raise last_exception

        return wrapper

    return decorator


async def retry_async_call(
    func: Callable, *args, config: Optional[RetryConfig] = None, **kwargs
) -> Any:
    """
    Retry an async function call with the given configuration.

    Args:
        func: The async function to call
        *args: Positional arguments for the function
        config: RetryConfig instance
        **kwargs: Keyword arguments for the function

    Returns:
        The result of the function call

    Raises:
        The last exception if all retries fail
    """
    retry_config = config or RetryConfig()
    last_exception = None
    current_delay = retry_config.delay

    for attempt in range(retry_config.max_attempts):
        try:
            return await func(*args, **kwargs)
        except retry_config.exceptions as e:
            last_exception = e

            # Don't retry on the last attempt
            if attempt == retry_config.max_attempts - 1:
                break

            # Wait before retrying
            await asyncio.sleep(current_delay)
            current_delay *= retry_config.backoff

    # If we get here, all retries failed
    raise last_exception


def create_retry_config(
    max_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    delay: float = DEFAULT_RETRY_DELAY,
    backoff: float = DEFAULT_RETRY_BACKOFF,
    connection_timeout: float = DEFAULT_CONNECTION_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    exceptions: tuple = (Exception,),
) -> RetryConfig:
    """Create a RetryConfig with the given parameters."""
    return RetryConfig(
        max_attempts=max_attempts,
        delay=delay,
        backoff=backoff,
        connection_timeout=connection_timeout,
        read_timeout=read_timeout,
        exceptions=exceptions,
    )


# Common retry configurations
NETWORK_RETRY_CONFIG = RetryConfig(
    max_attempts=3,
    delay=1.0,
    backoff=2.0,
    connection_timeout=10.0,
    read_timeout=30.0,
    exceptions=(ConnectionError, TimeoutError, asyncio.TimeoutError),
)

QUICK_RETRY_CONFIG = RetryConfig(
    max_attempts=2,
    delay=0.5,
    backoff=1.5,
    connection_timeout=5.0,
    read_timeout=15.0,
    exceptions=(ConnectionError, TimeoutError, asyncio.TimeoutError),
)

PERSISTENT_RETRY_CONFIG = RetryConfig(
    max_attempts=5,
    delay=2.0,
    backoff=1.5,
    connection_timeout=15.0,
    read_timeout=60.0,
    exceptions=(ConnectionError, TimeoutError, asyncio.TimeoutError),
)
