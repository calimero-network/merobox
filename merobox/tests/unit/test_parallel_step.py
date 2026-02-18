"""
Unit tests for ParallelStep failure modes.
"""

import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock problematic imports before importing the module under test
# These mocks must be set up before any merobox imports
_mock_ed25519 = MagicMock()
_mock_py_near = MagicMock()
_mock_py_near_account = MagicMock()
_mock_py_near_durable_nonce = MagicMock()
_mock_py_near_transactions = MagicMock()
_mock_calimero_client = MagicMock()
_mock_calimero_client_client = MagicMock()

sys.modules["ed25519"] = _mock_ed25519
sys.modules["py_near"] = _mock_py_near
sys.modules["py_near.account"] = _mock_py_near_account
sys.modules["py_near.durable_nonce"] = _mock_py_near_durable_nonce
sys.modules["py_near.transactions"] = _mock_py_near_transactions
sys.modules["calimero_client_py"] = _mock_calimero_client
sys.modules["calimero_client_py.client"] = _mock_calimero_client_client

from merobox.commands.bootstrap.steps.parallel import (  # noqa: E402
    VALID_FAILURE_MODES,
    ParallelStep,
)


class TestParallelStepValidation:
    """Tests for ParallelStep validation."""

    def test_valid_failure_modes_constant(self):
        """Test that VALID_FAILURE_MODES contains expected values."""
        assert "fail-fast" in VALID_FAILURE_MODES
        assert "fail-slow" in VALID_FAILURE_MODES
        assert "continue-on-error" in VALID_FAILURE_MODES
        assert len(VALID_FAILURE_MODES) == 3

    def test_invalid_failure_mode_raises_error(self):
        """Test that invalid failure_mode raises ValueError."""
        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "groups": [{"steps": [{"type": "wait", "duration": 1}]}],
            "failure_mode": "invalid-mode",
        }
        with pytest.raises(ValueError) as exc_info:
            ParallelStep(config)
        assert "failure_mode" in str(exc_info.value)
        assert "invalid-mode" in str(exc_info.value)

    def test_failure_mode_must_be_string(self):
        """Test that failure_mode must be a string."""
        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "groups": [{"steps": [{"type": "wait", "duration": 1}]}],
            "failure_mode": 123,
        }
        with pytest.raises(ValueError) as exc_info:
            ParallelStep(config)
        assert "failure_mode" in str(exc_info.value)
        assert "must be a string" in str(exc_info.value)

    def test_valid_failure_mode_fail_fast(self):
        """Test that fail-fast is accepted."""
        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "groups": [{"steps": [{"type": "wait", "duration": 1}]}],
            "failure_mode": "fail-fast",
        }
        step = ParallelStep(config)
        assert step.config["failure_mode"] == "fail-fast"

    def test_valid_failure_mode_fail_slow(self):
        """Test that fail-slow is accepted."""
        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "groups": [{"steps": [{"type": "wait", "duration": 1}]}],
            "failure_mode": "fail-slow",
        }
        step = ParallelStep(config)
        assert step.config["failure_mode"] == "fail-slow"

    def test_valid_failure_mode_continue_on_error(self):
        """Test that continue-on-error is accepted."""
        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "groups": [{"steps": [{"type": "wait", "duration": 1}]}],
            "failure_mode": "continue-on-error",
        }
        step = ParallelStep(config)
        assert step.config["failure_mode"] == "continue-on-error"

    def test_default_failure_mode_not_required(self):
        """Test that failure_mode defaults when not provided."""
        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "groups": [{"steps": [{"type": "wait", "duration": 1}]}],
        }
        # Should not raise - failure_mode is optional
        step = ParallelStep(config)
        # Default is fail-slow (set at runtime)
        assert "failure_mode" not in step.config or step.config.get("failure_mode") in (
            None,
            "fail-slow",
        )


class TestParallelStepExecution:
    """Tests for ParallelStep execution with different failure modes."""

    @pytest.fixture
    def mock_console(self):
        """Mock the console for output suppression."""
        with patch("merobox.commands.bootstrap.steps.parallel.console") as mock:
            yield mock

    @pytest.fixture
    def basic_config(self):
        """Basic parallel step configuration."""
        return {
            "name": "Test Parallel",
            "type": "parallel",
            "groups": [
                {"name": "Group1", "steps": [{"type": "wait", "duration": 0.01}]},
                {"name": "Group2", "steps": [{"type": "wait", "duration": 0.01}]},
            ],
        }

    @pytest.mark.asyncio
    async def test_fail_slow_waits_for_all_groups(self, mock_console):
        """Test that fail-slow mode waits for all groups to complete."""
        execution_order = []

        async def mock_execute_group(
            self, idx, group, workflow_results, dynamic_values
        ):
            group_name = group.get("name", f"Group {idx+1}")
            await asyncio.sleep(0.01 * (idx + 1))  # Staggered completion
            execution_order.append(group_name)
            # First group fails, second succeeds
            return {
                "success": idx != 0,
                "duration_seconds": 0.01 * (idx + 1),
            }

        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "failure_mode": "fail-slow",
            "groups": [
                {"name": "Group1", "steps": [{"type": "wait", "duration": 0.01}]},
                {"name": "Group2", "steps": [{"type": "wait", "duration": 0.02}]},
            ],
        }

        step = ParallelStep(config)
        with patch.object(ParallelStep, "_execute_group", mock_execute_group):
            result = await step.execute({}, {})

        # Both groups should have completed
        assert len(execution_order) == 2
        # Result should be False because one group failed
        assert result is False

    @pytest.mark.asyncio
    async def test_fail_fast_cancels_remaining_groups(self, mock_console):
        """Test that fail-fast mode cancels remaining groups after failure."""
        started_groups = []
        completed_groups = []

        async def mock_execute_group(
            self, idx, group, workflow_results, dynamic_values
        ):
            group_name = group.get("name", f"Group {idx+1}")
            started_groups.append(group_name)

            if idx == 0:
                # First group fails immediately
                completed_groups.append(group_name)
                return {"success": False, "duration_seconds": 0.001}
            else:
                # Other groups take longer - should be cancelled
                try:
                    await asyncio.sleep(10.0)  # Long enough to be cancelled
                    completed_groups.append(group_name)
                    return {"success": True, "duration_seconds": 10.0}
                except asyncio.CancelledError:
                    # Re-raise to properly propagate cancellation
                    raise

        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "failure_mode": "fail-fast",
            "groups": [
                {"name": "FailingGroup", "steps": [{"type": "wait", "duration": 0.01}]},
                {"name": "SlowGroup", "steps": [{"type": "wait", "duration": 10}]},
            ],
        }

        step = ParallelStep(config)
        dynamic_values = {}
        with patch.object(ParallelStep, "_execute_group", mock_execute_group):
            result = await step.execute({}, dynamic_values)

        # Result should be False
        assert result is False
        # Verify that SlowGroup was cancelled (not in completed_groups)
        assert "FailingGroup" in completed_groups
        assert "SlowGroup" not in completed_groups
        # Verify failure count reflects the cancellation
        assert (
            dynamic_values.get("parallel_failure_count") == 2
        )  # 1 failed + 1 cancelled

    @pytest.mark.asyncio
    async def test_continue_on_error_returns_success_with_partial_success(
        self, mock_console
    ):
        """Test that continue-on-error returns True if at least one group succeeded."""

        async def mock_execute_group(
            self, idx, group, workflow_results, dynamic_values
        ):
            # First group fails, second succeeds
            return {
                "success": idx != 0,
                "duration_seconds": 0.01,
            }

        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "failure_mode": "continue-on-error",
            "groups": [
                {"name": "FailingGroup", "steps": [{"type": "wait", "duration": 0.01}]},
                {"name": "SuccessGroup", "steps": [{"type": "wait", "duration": 0.01}]},
            ],
        }

        step = ParallelStep(config)
        with patch.object(ParallelStep, "_execute_group", mock_execute_group):
            dynamic_values = {}
            result = await step.execute({}, dynamic_values)

        # Result should be True because at least one group succeeded
        assert result is True
        # Verify statistics
        assert dynamic_values.get("parallel_success_count") == 1
        assert dynamic_values.get("parallel_failure_count") == 1

    @pytest.mark.asyncio
    async def test_continue_on_error_returns_false_when_all_fail(self, mock_console):
        """Test that continue-on-error returns False when all groups fail."""

        async def mock_execute_group(
            self, idx, group, workflow_results, dynamic_values
        ):
            # All groups fail
            return {"success": False, "duration_seconds": 0.01}

        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "failure_mode": "continue-on-error",
            "groups": [
                {
                    "name": "FailingGroup1",
                    "steps": [{"type": "wait", "duration": 0.01}],
                },
                {
                    "name": "FailingGroup2",
                    "steps": [{"type": "wait", "duration": 0.01}],
                },
            ],
        }

        step = ParallelStep(config)
        with patch.object(ParallelStep, "_execute_group", mock_execute_group):
            dynamic_values = {}
            result = await step.execute({}, dynamic_values)

        # Result should be False because all groups failed
        assert result is False
        assert dynamic_values.get("parallel_success_count") == 0
        assert dynamic_values.get("parallel_failure_count") == 2

    @pytest.mark.asyncio
    async def test_default_failure_mode_is_fail_slow(self, mock_console):
        """Test that the default failure mode is fail-slow."""

        async def mock_execute_group(
            self, idx, group, workflow_results, dynamic_values
        ):
            # First group fails, second succeeds
            return {"success": idx != 0, "duration_seconds": 0.01}

        config = {
            "name": "Test Parallel",
            "type": "parallel",
            # No failure_mode specified - should default to fail-slow
            "groups": [
                {"name": "FailingGroup", "steps": [{"type": "wait", "duration": 0.01}]},
                {"name": "SuccessGroup", "steps": [{"type": "wait", "duration": 0.01}]},
            ],
        }

        step = ParallelStep(config)
        with patch.object(ParallelStep, "_execute_group", mock_execute_group):
            dynamic_values = {}
            result = await step.execute({}, dynamic_values)

        # With fail-slow (default), result should be False because one group failed
        assert result is False

    @pytest.mark.asyncio
    async def test_all_success_returns_true(self, mock_console):
        """Test that all failure modes return True when all groups succeed."""

        async def mock_execute_group(
            self, idx, group, workflow_results, dynamic_values
        ):
            return {"success": True, "duration_seconds": 0.01}

        for failure_mode in VALID_FAILURE_MODES:
            config = {
                "name": "Test Parallel",
                "type": "parallel",
                "failure_mode": failure_mode,
                "groups": [
                    {"name": "Group1", "steps": [{"type": "wait", "duration": 0.01}]},
                    {"name": "Group2", "steps": [{"type": "wait", "duration": 0.01}]},
                ],
            }

            step = ParallelStep(config)
            with patch.object(ParallelStep, "_execute_group", mock_execute_group):
                result = await step.execute({}, {})

            assert result is True, f"Failed for failure_mode={failure_mode}"


class TestParallelStepExportVariables:
    """Tests for ParallelStep variable exports."""

    @pytest.fixture
    def mock_console(self):
        """Mock the console for output suppression."""
        with patch("merobox.commands.bootstrap.steps.parallel.console") as mock:
            yield mock

    @pytest.mark.asyncio
    async def test_exports_success_and_failure_counts(self, mock_console):
        """Test that success and failure counts are exported."""

        async def mock_execute_group(
            self, idx, group, workflow_results, dynamic_values
        ):
            # Alternate success/failure
            return {"success": idx % 2 == 0, "duration_seconds": 0.01}

        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "failure_mode": "fail-slow",
            "groups": [
                {"name": "Group1", "steps": [{"type": "wait", "duration": 0.01}]},
                {"name": "Group2", "steps": [{"type": "wait", "duration": 0.01}]},
                {"name": "Group3", "steps": [{"type": "wait", "duration": 0.01}]},
                {"name": "Group4", "steps": [{"type": "wait", "duration": 0.01}]},
            ],
        }

        step = ParallelStep(config)
        with patch.object(ParallelStep, "_execute_group", mock_execute_group):
            dynamic_values = {}
            await step.execute({}, dynamic_values)

        assert dynamic_values["parallel_success_count"] == 2
        assert dynamic_values["parallel_failure_count"] == 2
        assert dynamic_values["group_count"] == 4

    @pytest.mark.asyncio
    async def test_exports_timing_metrics(self, mock_console):
        """Test that timing metrics are exported."""

        async def mock_execute_group(
            self, idx, group, workflow_results, dynamic_values
        ):
            return {"success": True, "duration_seconds": 0.1}

        config = {
            "name": "Test Parallel",
            "type": "parallel",
            "groups": [
                {"name": "Group1", "steps": [{"type": "wait", "duration": 0.01}]},
            ],
        }

        step = ParallelStep(config)
        with patch.object(ParallelStep, "_execute_group", mock_execute_group):
            dynamic_values = {}
            await step.execute({}, dynamic_values)

        assert "overall_duration_seconds" in dynamic_values
        assert "overall_duration_ms" in dynamic_values
        assert "overall_duration_ns" in dynamic_values
        assert "group_0_duration_seconds" in dynamic_values
        assert "Group1_duration_seconds" in dynamic_values
