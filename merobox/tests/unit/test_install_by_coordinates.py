"""`install_application` names a registry coordinate, not a URL.

The node's POST /admin-api/install-application takes `{package, version}` with
`deny_unknown_fields`, so a body carrying `url` is a 400. The compiled client
still builds the old URL body, so the coordinate install drives the admin API
over raw HTTP - the same reason the namespace and TEE steps do.
"""

import asyncio
from unittest.mock import MagicMock, patch

import click
import pytest

from merobox.commands.bootstrap.config import validate_workflow_step
from merobox.commands.bootstrap.steps.install import InstallApplicationStep
from merobox.commands.install import validate_installation_source


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _config(**overrides):
    cfg = {"type": "install_application", "name": "Install", "node": "n1"}
    cfg.update(overrides)
    return cfg


class TestSourceValidation:
    """The schema layer, which runs before any container starts."""

    def test_coordinates_pass(self):
        assert (
            validate_workflow_step(
                _config(package="com.example.app", version="1.0.0"), 0
            )
            == []
        )

    def test_a_path_still_passes(self):
        assert validate_workflow_step(_config(path="./dist/app.mpk"), 0) == []

    def test_a_url_is_refused(self):
        errors = validate_workflow_step(
            _config(url="https://apps.example.com/a.mpk"), 0
        )
        assert any("unknown field 'url'" in e for e in errors)

    @pytest.mark.parametrize(
        "half", [{"package": "com.example.app"}, {"version": "1.0.0"}]
    )
    def test_half_a_coordinate_is_refused(self, half):
        errors = validate_workflow_step(_config(**half), 0)
        assert any("one coordinate" in e for e in errors)

    def test_two_sources_are_refused(self):
        errors = validate_workflow_step(
            _config(path="./dist/app.mpk", package="com.example.app", version="1.0.0"),
            0,
        )
        assert any("two sources" in e for e in errors)


class TestExecutorFieldValidation:
    """A step built directly, as a parallel group does, still gets checked."""

    def test_non_string_package_raises(self):
        with pytest.raises(ValueError, match="package"):
            InstallApplicationStep(_config(package=1, version="1.0.0"))

    def test_half_a_coordinate_raises(self):
        with pytest.raises(ValueError, match="one coordinate"):
            InstallApplicationStep(_config(package="com.example.app"))

    def test_no_source_raises(self):
        with pytest.raises(ValueError, match=r"'path' or 'package'"):
            InstallApplicationStep(_config())


class TestCoordinateInstall:
    def _exec(self, step, install_side_effect=None):
        with (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:7180", "n1"),
            ),
            patch(
                "merobox.commands.bootstrap.steps.install.get_client_for_rpc_url"
            ) as mock_get_client,
            patch.object(step, "_print_node_logs_on_failure"),
        ):
            client = MagicMock()
            client.install_application.return_value = {
                "data": {"applicationId": "app-hex"}
            }
            client.install_dev_application.return_value = {
                "data": {"applicationId": "app-hex"}
            }
            if install_side_effect is not None:
                client.install_application.side_effect = install_side_effect
            mock_get_client.return_value = client

            dynamic_values = {}
            result = _run(step.execute({}, dynamic_values))
            return result, client, dynamic_values

    def test_passes_the_coordinate_and_exports_the_app_id(self):
        step = InstallApplicationStep(
            _config(package="com.example.app", version="1.0.0")
        )
        result, client, dynamic_values = self._exec(step)

        assert result is True
        client.install_application.assert_called_once_with(
            package="com.example.app", version="1.0.0"
        )
        # The dev endpoint is the one that takes a path; coordinates never use it.
        client.install_dev_application.assert_not_called()
        assert dynamic_values["app_id_n1"] == "app-hex"

    def test_the_servers_refusal_reaches_the_report(self):
        step = InstallApplicationStep(
            _config(package="com.example.app", version="9.9.9")
        )
        result, _, _ = self._exec(
            step,
            install_side_effect=RuntimeError(
                "Client error: the configured Dht source has no application "
                "published at com.example.app@9.9.9"
            ),
        )
        assert result is False

    def test_a_path_install_uses_the_dev_endpoint(self, tmp_path):
        bundle = tmp_path / "app.mpk"
        bundle.write_bytes(b"\0asm")
        step = InstallApplicationStep(_config(path=str(bundle)))

        with patch.object(step, "_is_binary_mode", return_value=True):
            result, client, _ = self._exec(step)

        assert result is True
        client.install_application.assert_not_called()
        client.install_dev_application.assert_called_once_with(path=str(bundle))


class TestCliSourceValidation:
    """`merobox install` must accept the same sources as the workflow step."""

    def test_coordinates_pass(self):
        validate_installation_source(package="com.example.app", version="1.0.0")

    def test_a_path_passes(self, tmp_path):
        bundle = tmp_path / "app.mpk"
        bundle.write_bytes(b"\0asm")
        validate_installation_source(path=str(bundle))

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(click.BadParameter, match="not a file"):
            validate_installation_source(path=str(tmp_path / "absent.mpk"))

    @pytest.mark.parametrize(
        "kwargs", [{}, {"package": "com.example.app"}, {"version": "1.0.0"}]
    )
    def test_a_missing_or_half_coordinate_is_refused(self, kwargs):
        with pytest.raises(click.BadParameter, match="--package and --version"):
            validate_installation_source(**kwargs)
