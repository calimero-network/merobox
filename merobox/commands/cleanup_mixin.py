"""
Cleanup Mixin - Thread-safe cleanup coordination for manager classes.

Provides a common pattern for signal handling and resource cleanup that can be
shared between DockerManager and BinaryManager.
"""

import atexit
import os
import signal
import sys
import threading
from abc import ABC, abstractmethod

from rich.console import Console

from merobox.commands.constants import CleanupResult

console = Console()


class CleanupMixin(ABC):
    """Mixin providing thread-safe cleanup coordination for manager classes.

    This mixin handles:
    - Signal handler registration (SIGINT/SIGTERM)
    - Thread-safe cleanup guards (RLock, _cleanup_in_progress, _cleanup_done)
    - Atexit handler registration
    - Graceful shutdown coordination

    Subclasses must implement:
    - _do_cleanup(): Perform the actual resource cleanup (stop containers/processes)

    Usage:
        class MyManager(CleanupMixin):
            def __init__(self, enable_signal_handlers=True):
                self._init_cleanup_state()
                # ... other initialization ...
                if enable_signal_handlers:
                    self._setup_signal_handlers()

            def _do_cleanup(self):
                # Stop your resources here
                pass
    """

    def _init_cleanup_state(self):
        """Initialize cleanup-related instance variables.

        Call this in __init__ before _setup_signal_handlers().
        """
        self._shutting_down = False
        self._cleanup_lock = threading.RLock()
        self._cleanup_in_progress = False
        self._cleanup_done = False
        self._original_sigint_handler = None
        self._original_sigterm_handler = None

    def _setup_signal_handlers(self):
        """Register signal handlers for graceful shutdown."""
        self._original_sigint_handler = signal.signal(
            signal.SIGINT, self._signal_handler
        )
        self._original_sigterm_handler = signal.signal(
            signal.SIGTERM, self._signal_handler
        )
        atexit.register(self._cleanup_on_exit)

    def _signal_handler(self, signum, frame):
        """Handle SIGINT/SIGTERM signals for graceful shutdown.

        Uses sys.exit() to allow proper stack unwinding and finally block
        execution. If cleanup is already in progress (e.g., via atexit), we
        return without calling sys.exit() to avoid interrupting the ongoing
        cleanup with SystemExit.
        """
        if self._shutting_down:
            console.print("\n[red]Forced exit requested, terminating...[/red]")
            sys.stdout.flush()
            os._exit(1)

        self._shutting_down = True
        sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        console.print(
            f"\n[yellow]Received {sig_name}, initiating graceful shutdown...[/yellow]"
        )

        cleanup_result = self._cleanup_resources()

        if cleanup_result != CleanupResult.IN_PROGRESS:
            sys.exit(0)

    def _cleanup_on_exit(self):
        """Cleanup handler for atexit.

        Calls _cleanup_resources unconditionally since it's idempotent and
        will return immediately if cleanup was already done or is in progress.
        """
        self._cleanup_resources()

    def _cleanup_resources_guarded(self, cleanup_fn, *args, **kwargs) -> CleanupResult:
        """Execute cleanup function with thread-safe guards.

        Thread-safe guard ensuring at-most-once execution semantics. Uses RLock
        to allow re-entrant calls from signal handlers in the same thread.

        Args:
            cleanup_fn: The cleanup function to execute (typically self._do_cleanup)
            *args, **kwargs: Arguments to pass to cleanup_fn

        Returns:
            CleanupResult.PERFORMED: Cleanup was executed by this call
            CleanupResult.ALREADY_DONE: Cleanup was already completed previously
            CleanupResult.IN_PROGRESS: Cleanup is currently in progress (re-entrant call)
        """
        with self._cleanup_lock:
            if self._cleanup_done:
                return CleanupResult.ALREADY_DONE
            if self._cleanup_in_progress:
                return CleanupResult.IN_PROGRESS
            self._cleanup_in_progress = True

            try:
                cleanup_fn(*args, **kwargs)
            finally:
                self._cleanup_done = True
                self._cleanup_in_progress = False

        return CleanupResult.PERFORMED

    def _cleanup_resources(self) -> CleanupResult:
        """Stop all managed resources with thread-safe guards.

        Default implementation that calls _do_cleanup() with no arguments.
        Subclasses can override this to add parameters (like drain_timeout).

        Returns:
            CleanupResult.PERFORMED: Cleanup was executed by this call
            CleanupResult.ALREADY_DONE: Cleanup was already completed previously
            CleanupResult.IN_PROGRESS: Cleanup is currently in progress (re-entrant call)
        """
        return self._cleanup_resources_guarded(self._do_cleanup)

    @abstractmethod
    def _do_cleanup(self, *args, **kwargs):
        """Perform the actual resource cleanup.

        Implement this method to stop containers, processes, or other resources.
        This method is called inside the cleanup lock, so it's guaranteed to run
        at most once.

        Note: Do not call sys.exit() or raise SystemExit from this method.
        """
        pass

    def remove_signal_handlers(self):
        """Remove signal handlers and restore original handlers."""
        if self._original_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._original_sigint_handler)
            self._original_sigint_handler = None
        if self._original_sigterm_handler is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm_handler)
            self._original_sigterm_handler = None
