"""Log-grep regression-gate steps: assert_log_absent / assert_log_present.

These steps let workflow YAMLs express log-grep regression gates inline
instead of relying on out-of-band CI shell steps to download docker log
artefacts and grep them.
"""

from __future__ import annotations

import re
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console

# Sentinel passed to docker-py for "scan everything" in container.logs(tail=...).
_DOCKER_TAIL_ALL = "all"
# Large value used as the "scan everything" knob for BinaryManager.get_node_logs,
# which always slices ``all_lines[-lines:]`` and has no native "unbounded" form.
_BINARY_TAIL_ALL = 10**9


class _AssertLogStepBase(BaseStep):
    """Shared field validation and log retrieval for the assert_log_* steps."""

    def _get_required_fields(self) -> list[str]:
        return ["nodes", "patterns"]

    def _validate_field_types(self) -> None:
        # nodes: list of strings. Empty list is allowed and means "all running
        # nodes" (resolved at execute time via the manager).
        self._validate_list_field("nodes", allow_empty=True, element_type=str)
        self._validate_list_field("patterns", allow_empty=False, element_type=str)
        # Empty-string patterns trivially match every line, which makes the
        # step meaningless and is almost always a YAML typo. Reject early.
        for idx, pattern in enumerate(self.config["patterns"]):
            if pattern == "":
                raise ValueError(
                    f"Step '{self._get_step_name()}': 'patterns[{idx}]' "
                    f"must not be an empty string"
                )
        if "regex" in self.config:
            self._validate_boolean_field("regex")
        if "case_sensitive" in self.config:
            self._validate_boolean_field("case_sensitive")
        if "tail_lines" in self.config and self.config["tail_lines"] is not None:
            self._validate_integer_field("tail_lines", positive=True)
        # If regex=True, compile each pattern at validation time so malformed
        # regexes surface as a clean ValueError instead of crashing the step
        # mid-execution with re.error.
        if self.config.get("regex"):
            for idx, pattern in enumerate(self.config["patterns"]):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(
                        f"Step '{self._get_step_name()}': 'patterns[{idx}]' "
                        f"is not a valid regex ({exc})"
                    ) from exc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_binary_mode(self) -> bool:
        return (
            self.manager is not None
            and getattr(self.manager, "binary_path", None) is not None
        )

    def _resolve_target_nodes(self) -> list[str]:
        nodes = list(self.config.get("nodes", []))
        if nodes:
            return nodes
        # Empty list => all running nodes
        if self.manager is None:
            return []
        if self._is_binary_mode():
            listed = self.manager.list_nodes() or []
            return [n["name"] for n in listed if isinstance(n, dict) and "name" in n]
        try:
            return list(self.manager.get_running_nodes() or [])
        except Exception:
            return []

    def _fetch_log(self, node_name: str) -> str | None:
        tail_lines = self.config.get("tail_lines")
        if self.manager is None:
            return None
        if self._is_binary_mode():
            lines = tail_lines if tail_lines is not None else _BINARY_TAIL_ALL
            try:
                return self.manager.get_node_logs(node_name, lines=lines)
            except Exception as e:
                console.print(
                    f"[yellow]⚠️  Could not retrieve logs for {node_name}: {e}[/yellow]"
                )
                return None
        # Docker mode
        try:
            container = (
                self.manager.nodes.get(node_name)
                if hasattr(self.manager, "nodes")
                else None
            )
            if container is None:
                container = self.manager.client.containers.get(node_name)
            tail = tail_lines if tail_lines is not None else _DOCKER_TAIL_ALL
            raw = container.logs(tail=tail, timestamps=True)
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="replace")
            return str(raw)
        except Exception as e:
            console.print(
                f"[yellow]⚠️  Could not retrieve logs for {node_name}: {e}[/yellow]"
            )
            return None

    def _compile_matcher(self, pattern: str):
        """Return a callable(line: str) -> bool for a single pattern."""
        use_regex = bool(self.config.get("regex", False))
        case_sensitive = bool(self.config.get("case_sensitive", True))
        if use_regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            compiled = re.compile(pattern, flags)
            return lambda line: compiled.search(line) is not None
        if case_sensitive:
            needle = pattern
            return lambda line: needle in line
        needle = pattern.lower()
        return lambda line: needle in line.lower()

    def _iter_lines(self, log: str) -> list[str]:
        return log.splitlines() if log else []


class AssertLogAbsentStep(_AssertLogStepBase):
    """Fail if any pattern matches in any of the named nodes' logs.

    Configuration::

        - name: Regression guard — no rejection spam
          type: assert_log_absent
          nodes: [af-no-spam-node-1, af-no-spam-node-2]
          patterns:
            - "context not materialised within join race window"
            - "inbound stream for unknown context"
    """

    async def execute(
        self,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> bool:
        target_nodes = self._resolve_target_nodes()
        if not target_nodes:
            console.print(
                "[yellow]⚠️  assert_log_absent: no target nodes resolved; "
                "treating as pass[/yellow]"
            )
            return True

        # Deduplicate patterns to avoid double-counting and duplicate reports
        # when a user accidentally lists the same pattern twice.
        patterns: list[str] = list(dict.fromkeys(self.config["patterns"]))
        matchers = [(p, self._compile_matcher(p)) for p in patterns]

        all_ok = True
        for node_name in target_nodes:
            log = self._fetch_log(node_name)
            if log is None:
                continue
            for line_no, line in enumerate(self._iter_lines(log), start=1):
                for pattern, matches in matchers:
                    if matches(line):
                        trimmed = line if len(line) <= 200 else line[:197] + "..."
                        console.print(
                            f"[red]✗ assert_log_absent failed on node "
                            f"'{node_name}' line {line_no}: matched pattern "
                            f"{pattern!r} -> {trimmed!r}[/red]"
                        )
                        all_ok = False

        if all_ok:
            console.print(
                f"[green]✓ assert_log_absent: none of "
                f"{len(patterns)} pattern(s) matched across "
                f"{len(target_nodes)} node(s)[/green]"
            )
        return all_ok


class AssertLogPresentStep(_AssertLogStepBase):
    """Fail unless every pattern matches at least ``min_matches`` times.

    Hits are aggregated across the union of the named nodes (not per-node).

    Configuration::

        - name: Regression guard — sync completed
          type: assert_log_present
          nodes: [bootstrap-node-1]
          patterns:
            - "Sync session complete"
          tail_lines: 5000
    """

    def _validate_field_types(self) -> None:
        super()._validate_field_types()
        if "min_matches" in self.config:
            self._validate_integer_field("min_matches", positive=True)

    async def execute(
        self,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> bool:
        target_nodes = self._resolve_target_nodes()
        if not target_nodes:
            console.print(
                "[red]✗ assert_log_present failed: no target nodes resolved[/red]"
            )
            return False

        # Deduplicate patterns: with duplicates a single matching line would
        # increment hits[pattern] multiple times via the duplicate matcher
        # entries, letting a pattern that only hits min_matches/N times still
        # pass an aggregated min_matches threshold.
        patterns: list[str] = list(dict.fromkeys(self.config["patterns"]))
        min_matches = int(self.config.get("min_matches", 1))
        matchers = [(p, self._compile_matcher(p)) for p in patterns]

        hits: dict[str, int] = dict.fromkeys(patterns, 0)
        sample_hits: dict[str, tuple[str, int, str]] = {}

        for node_name in target_nodes:
            log = self._fetch_log(node_name)
            if log is None:
                continue
            for line_no, line in enumerate(self._iter_lines(log), start=1):
                for pattern, matches in matchers:
                    if matches(line):
                        hits[pattern] += 1
                        sample_hits.setdefault(pattern, (node_name, line_no, line))

        missing = [p for p in patterns if hits[p] < min_matches]
        if missing:
            for pattern in missing:
                console.print(
                    f"[red]✗ assert_log_present failed: pattern "
                    f"{pattern!r} had {hits[pattern]} hit(s), "
                    f"expected >= {min_matches}[/red]"
                )
            return False

        for pattern in patterns:
            node_name, line_no, line = sample_hits[pattern]
            trimmed = line if len(line) <= 200 else line[:197] + "..."
            console.print(
                f"[green]✓ assert_log_present: {pattern!r} matched "
                f"{hits[pattern]} time(s) (first: node '{node_name}' line "
                f"{line_no} -> {trimmed!r})[/green]"
            )
        return True
