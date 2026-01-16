"""Tests for ReleaseState functionality."""

import json
from pathlib import Path

import pytest

from redis_release.bht.state import ReleaseState, Workflow
from redis_release.config import Config, PackageConfig
from redis_release.models import PackageType, ReleaseArgs
from redis_release.state_manager import InMemoryStateStorage, StateManager


class TestReleaseStateFromConfig:
    """Test cases for ReleaseState.from_config method."""

    def test_from_config_with_valid_workflows(self) -> None:
        """Test from_config with valid workflow files."""
        # Create a minimal config with valid workflow files
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        state = ReleaseState.from_config(config)

        assert "test-package" in state.packages
        assert state.packages["test-package"].meta.repo == "test/repo"
        assert state.packages["test-package"].meta.ref is None
        assert state.packages["test-package"].build.workflow_file == "build.yml"
        assert state.packages["test-package"].publish is not None
        assert state.packages["test-package"].publish.workflow_file == "publish.yml"
        # Check default timeout values
        assert state.packages["test-package"].build.timeout_minutes == 45
        assert state.packages["test-package"].publish.timeout_minutes == 10

    def test_from_config_with_custom_timeout_values(self) -> None:
        """Test from_config respects custom timeout values from config."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    build_timeout_minutes=60,
                    publish_workflow="publish.yml",
                    publish_timeout_minutes=20,
                )
            },
        )

        state = ReleaseState.from_config(config)

        assert state.packages["test-package"].build.timeout_minutes == 60
        assert state.packages["test-package"].publish is not None
        assert state.packages["test-package"].publish.timeout_minutes == 20

    def test_from_config_with_ref(self) -> None:
        """Test from_config respects ref field from config."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    ref="release/8.0",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        state = ReleaseState.from_config(config)

        assert state.packages["test-package"].meta.ref == "release/8.0"

    def test_from_config_with_workflow_inputs(self) -> None:
        """Test from_config respects build_inputs and publish_inputs from config."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    build_inputs={"key1": "value1", "key2": "value2"},
                    publish_workflow="publish.yml",
                    publish_inputs={"publish_key": "publish_value"},
                )
            },
        )

        state = ReleaseState.from_config(config)

        assert state.packages["test-package"].build.inputs == {
            "key1": "value1",
            "key2": "value2",
        }
        assert state.packages["test-package"].publish is not None
        assert state.packages["test-package"].publish.inputs == {
            "publish_key": "publish_value"
        }

    def test_from_config_with_all_optional_fields(self) -> None:
        """Test from_config with all optional fields set."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    ref="main",
                    build_workflow="build.yml",
                    build_timeout_minutes=60,
                    build_inputs={"build_arg": "build_val"},
                    publish_workflow="publish.yml",
                    publish_timeout_minutes=20,
                    publish_inputs={"publish_arg": "publish_val"},
                )
            },
        )

        state = ReleaseState.from_config(config)

        pkg = state.packages["test-package"]
        assert pkg.meta.ref == "main"
        assert pkg.build.timeout_minutes == 60
        assert pkg.build.inputs == {"build_arg": "build_val"}
        assert pkg.publish is not None
        assert pkg.publish.timeout_minutes == 20
        assert pkg.publish.inputs == {"publish_arg": "publish_val"}

    def test_from_config_with_empty_build_workflow(self) -> None:
        """Test from_config fails when build_workflow is empty."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="",
                    publish_workflow="publish.yml",
                )
            },
        )

        with pytest.raises(ValueError, match="build_workflow cannot be empty"):
            ReleaseState.from_config(config)

    def test_from_config_with_empty_publish_workflow(self) -> None:
        """Test from_config sets publish to None when publish_workflow is empty."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="",
                )
            },
        )

        state = ReleaseState.from_config(config)
        assert state.packages["test-package"].publish is None

    def test_from_config_with_whitespace_only_build_workflow(self) -> None:
        """Test from_config fails when build_workflow is whitespace only."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="   ",
                    publish_workflow="publish.yml",
                )
            },
        )

        with pytest.raises(ValueError, match="build_workflow cannot be empty"):
            ReleaseState.from_config(config)

    def test_from_config_with_whitespace_only_publish_workflow(self) -> None:
        """Test from_config sets publish to None when publish_workflow is whitespace only."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="   ",
                )
            },
        )

        state = ReleaseState.from_config(config)
        assert state.packages["test-package"].publish is None

    def test_from_config_with_multiple_packages(self) -> None:
        """Test from_config with multiple packages."""
        config = Config(
            version=1,
            packages={
                "package1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build1.yml",
                    publish_workflow="publish1.yml",
                ),
                "package2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DOCKER,
                    build_workflow="build2.yml",
                    publish_workflow="publish2.yml",
                ),
            },
        )

        state = ReleaseState.from_config(config)

        assert len(state.packages) == 2
        assert "package1" in state.packages
        assert "package2" in state.packages
        assert state.packages["package1"].build.workflow_file == "build1.yml"
        assert state.packages["package2"].build.workflow_file == "build2.yml"

    def test_from_config_error_message_includes_package_name(self) -> None:
        """Test that error messages include the package name for debugging."""
        config = Config(
            version=1,
            packages={
                "my-special-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="",
                    publish_workflow="publish.yml",
                )
            },
        )

        with pytest.raises(ValueError, match="my-special-package"):
            ReleaseState.from_config(config)

    def test_from_config_with_boolean_build_workflow(self) -> None:
        """Test from_config fails when build_workflow is a boolean."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow=False,
                    publish_workflow="publish.yml",
                )
            },
        )

        with pytest.raises(ValueError, match="build_workflow must be a string"):
            ReleaseState.from_config(config)

    def test_from_config_with_boolean_publish_workflow(self) -> None:
        """Test from_config sets publish to None when publish_workflow is False."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow=False,
                )
            },
        )

        state = ReleaseState.from_config(config)
        assert state.packages["test-package"].publish is None


class TestWorkflowEphemeral:
    """Test cases for Workflow ephemeral field."""

    def test_ephemeral_field_exists(self) -> None:
        """Test that ephemeral field is accessible."""
        workflow = Workflow(workflow_file="test.yml")

        assert hasattr(workflow, "ephemeral")
        assert workflow.ephemeral.trigger_workflow is None
        assert workflow.ephemeral.wait_for_completion is None

    def test_ephemeral_field_can_be_modified(self) -> None:
        """Test that ephemeral field values can be modified."""
        from py_trees.common import Status

        workflow = Workflow(workflow_file="test.yml")

        workflow.ephemeral.trigger_workflow = Status.FAILURE
        workflow.ephemeral.wait_for_completion = Status.RUNNING

        assert workflow.ephemeral.trigger_workflow == Status.FAILURE
        assert workflow.ephemeral.wait_for_completion == Status.RUNNING

    def test_ephemeral_field_not_serialized_to_json(self) -> None:
        """Test that ephemeral field is serialized but log_once_flags are excluded."""
        from py_trees.common import Status

        workflow = Workflow(workflow_file="test.yml")
        workflow.ephemeral.trigger_workflow = Status.FAILURE
        workflow.ephemeral.wait_for_completion = Status.SUCCESS
        workflow.ephemeral.log_once_flags["test_flag"] = True

        # Serialize to JSON
        json_str = workflow.model_dump_json()
        json_data = json.loads(json_str)

        # Verify ephemeral field IS in JSON (except log_once_flags)
        assert "ephemeral" in json_data
        assert json_data["ephemeral"]["trigger_workflow"] == "FAILURE"
        assert json_data["ephemeral"]["wait_for_completion"] == "SUCCESS"
        assert "log_once_flags" not in json_data["ephemeral"]

        # Verify other fields are present
        assert "workflow_file" in json_data
        assert json_data["workflow_file"] == "test.yml"

    def test_ephemeral_field_not_in_model_dump(self) -> None:
        """Test that ephemeral field is in model_dump but log_once_flags are excluded."""
        from py_trees.common import Status

        workflow = Workflow(workflow_file="test.yml")
        workflow.ephemeral.trigger_workflow = Status.SUCCESS
        workflow.ephemeral.log_once_flags["test_flag"] = True

        # Get dict representation
        data = workflow.model_dump()

        # Verify ephemeral field IS in dict (except log_once_flags)
        assert "ephemeral" in data
        assert data["ephemeral"]["trigger_workflow"] == Status.SUCCESS
        assert "log_once_flags" not in data["ephemeral"]

    def test_ephemeral_field_initialized_on_deserialization(self) -> None:
        """Test that ephemeral field is initialized when loading from JSON."""
        json_str = '{"workflow_file": "test.yml", "inputs": {}}'

        workflow = Workflow.model_validate_json(json_str)

        # Ephemeral field should be initialized with defaults
        assert hasattr(workflow, "ephemeral")
        assert workflow.ephemeral.trigger_workflow is None
        assert workflow.ephemeral.wait_for_completion is None

    def test_release_state_ephemeral_not_serialized(self) -> None:
        """Test that ephemeral fields are serialized but log_once_flags are excluded."""
        from py_trees.common import Status

        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        state = ReleaseState.from_config(config)

        # Modify ephemeral fields
        state.packages["test-package"].build.ephemeral.trigger_workflow = Status.FAILURE
        assert state.packages["test-package"].publish is not None
        state.packages["test-package"].publish.ephemeral.wait_for_completion = (
            Status.SUCCESS
        )
        state.packages["test-package"].build.ephemeral.log_once_flags["test"] = True

        # Serialize to JSON
        json_str = state.model_dump_json()
        json_data = json.loads(json_str)

        # Verify ephemeral fields ARE in JSON (except log_once_flags)
        build_workflow = json_data["packages"]["test-package"]["build"]
        publish_workflow = json_data["packages"]["test-package"]["publish"]

        assert "ephemeral" in build_workflow
        assert build_workflow["ephemeral"]["trigger_workflow"] == "FAILURE"
        assert "log_once_flags" not in build_workflow["ephemeral"]
        assert "ephemeral" in publish_workflow
        assert publish_workflow["ephemeral"]["wait_for_completion"] == "SUCCESS"
        assert "log_once_flags" not in publish_workflow["ephemeral"]


class TestReleaseMeta:
    """Test cases for ReleaseMeta functionality."""

    def test_release_meta_tag_field(self) -> None:
        """Test that ReleaseMeta has tag field."""
        state = ReleaseState()
        assert state.meta.tag is None

        state.meta.tag = "8.4-m01"
        assert state.meta.tag == "8.4-m01"

    def test_release_meta_serialization(self) -> None:
        """Test that ReleaseMeta is serialized correctly."""
        state = ReleaseState()
        state.meta.tag = "8.4-m01"

        json_str = state.model_dump_json()
        json_data = json.loads(json_str)

        assert "meta" in json_data
        assert json_data["meta"]["tag"] == "8.4-m01"

    def test_release_meta_deserialization(self) -> None:
        """Test that ReleaseMeta is deserialized correctly."""
        json_str = '{"meta": {"tag": "8.4-m01"}, "packages": {}}'
        state = ReleaseState.model_validate_json(json_str)

        assert state.meta.tag == "8.4-m01"


class TestPackageMetaEphemeral:
    """Test cases for PackageMetaEphemeral functionality."""

    def test_ephemeral_field_exists(self) -> None:
        """Test that ephemeral field exists and force_rebuild defaults to False."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        state = ReleaseState.from_config(config)
        assert state.packages["test-package"].meta.ephemeral.force_rebuild is False

    def test_force_rebuild_field_can_be_modified(self) -> None:
        """Test that force_rebuild field can be modified."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        state = ReleaseState.from_config(config)
        state.packages["test-package"].meta.ephemeral.force_rebuild = True
        assert state.packages["test-package"].meta.ephemeral.force_rebuild is True

    def test_ephemeral_not_serialized(self) -> None:
        """Test that ephemeral field is serialized but log_once_flags are excluded."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        state = ReleaseState.from_config(config)
        state.packages["test-package"].meta.ephemeral.force_rebuild = True
        state.packages["test-package"].meta.ephemeral.log_once_flags["test"] = True

        json_str = state.model_dump_json()
        json_data = json.loads(json_str)

        # Ephemeral field IS serialized (except log_once_flags)
        assert "ephemeral" in json_data["packages"]["test-package"]["meta"]
        assert (
            json_data["packages"]["test-package"]["meta"]["ephemeral"]["force_rebuild"]
            is True
        )
        assert (
            "log_once_flags"
            not in json_data["packages"]["test-package"]["meta"]["ephemeral"]
        )


class TestStateSyncerWithArgs:
    """Test cases for StateSyncer with ReleaseArgs."""

    def test_state_syncer_sets_tag_from_args(self) -> None:
        """Test that StateSyncer sets tag from ReleaseArgs when creating from config."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        args = ReleaseArgs(release_tag="8.4-m01", force_rebuild=[])
        storage = InMemoryStateStorage()
        syncer = StateManager(storage=storage, config=config, args=args)

        assert syncer.state.meta.tag == "8.4-m01"

    def test_state_syncer_sets_force_rebuild_from_args(self) -> None:
        """Test that StateSyncer sets force_rebuild flags from ReleaseArgs."""
        config = Config(
            version=1,
            packages={
                "docker": PackageConfig(
                    repo="test/docker",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "redis": PackageConfig(
                    repo="test/redis",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
            },
        )

        args = ReleaseArgs(release_tag="8.4-m01", force_rebuild=["docker"])
        storage = InMemoryStateStorage()
        syncer = StateManager(storage=storage, config=config, args=args)

        assert syncer.state.packages["docker"].meta.ephemeral.force_rebuild is True
        assert syncer.state.packages["redis"].meta.ephemeral.force_rebuild is False

    def test_state_syncer_sets_multiple_force_rebuild_from_args(self) -> None:
        """Test that StateSyncer sets multiple force_rebuild flags from ReleaseArgs."""
        config = Config(
            version=1,
            packages={
                "docker": PackageConfig(
                    repo="test/docker",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "redis": PackageConfig(
                    repo="test/redis",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "snap": PackageConfig(
                    repo="test/snap",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
            },
        )

        args = ReleaseArgs(release_tag="8.4-m01", force_rebuild=["docker", "snap"])
        storage = InMemoryStateStorage()
        syncer = StateManager(storage=storage, config=config, args=args)

        assert syncer.state.packages["docker"].meta.ephemeral.force_rebuild is True
        assert syncer.state.packages["redis"].meta.ephemeral.force_rebuild is False
        assert syncer.state.packages["snap"].meta.ephemeral.force_rebuild is True

    def test_state_syncer_without_args(self) -> None:
        """Test that StateSyncer works without ReleaseArgs."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        args = ReleaseArgs(release_tag="test-tag", force_rebuild=[])
        storage = InMemoryStateStorage()
        syncer = StateManager(storage=storage, config=config, args=args)

        assert syncer.state.meta.tag == "test-tag"
        assert (
            syncer.state.packages["test-package"].meta.ephemeral.force_rebuild is False
        )

    def test_state_syncer_force_rebuild_all(self) -> None:
        """Test that StateSyncer sets force_rebuild for all packages when 'all' is specified."""
        config = Config(
            version=1,
            packages={
                "docker": PackageConfig(
                    repo="test/docker",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "redis": PackageConfig(
                    repo="test/redis",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "snap": PackageConfig(
                    repo="test/snap",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
            },
        )

        args = ReleaseArgs(release_tag="8.4-m01", force_rebuild=["all"])
        storage = InMemoryStateStorage()
        syncer = StateManager(storage=storage, config=config, args=args)

        # All packages should have force_rebuild set to True
        assert syncer.state.packages["docker"].meta.ephemeral.force_rebuild is True
        assert syncer.state.packages["redis"].meta.ephemeral.force_rebuild is True
        assert syncer.state.packages["snap"].meta.ephemeral.force_rebuild is True

    def test_state_syncer_force_rebuild_all_with_other_values(self) -> None:
        """Test that 'all' takes precedence even if other package names are specified."""
        config = Config(
            version=1,
            packages={
                "docker": PackageConfig(
                    repo="test/docker",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "redis": PackageConfig(
                    repo="test/redis",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
            },
        )

        args = ReleaseArgs(release_tag="8.4-m01", force_rebuild=["docker", "all"])
        storage = InMemoryStateStorage()
        syncer = StateManager(storage=storage, config=config, args=args)

        # All packages should have force_rebuild set to True
        assert syncer.state.packages["docker"].meta.ephemeral.force_rebuild is True
        assert syncer.state.packages["redis"].meta.ephemeral.force_rebuild is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
