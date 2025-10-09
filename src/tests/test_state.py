"""Tests for ReleaseState functionality."""

import json
from pathlib import Path

import pytest

from redis_release.bht.args import ReleaseArgs
from redis_release.bht.state import (
    InMemoryStateStorage,
    ReleaseState,
    StateSyncer,
    Workflow,
)
from redis_release.config import Config, PackageConfig


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
                    build_workflow="build.yml",
                    build_timeout_minutes=60,
                    publish_workflow="publish.yml",
                    publish_timeout_minutes=20,
                )
            },
        )

        state = ReleaseState.from_config(config)

        assert state.packages["test-package"].build.timeout_minutes == 60
        assert state.packages["test-package"].publish.timeout_minutes == 20

    def test_from_config_with_ref(self) -> None:
        """Test from_config respects ref field from config."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
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
        assert pkg.publish.timeout_minutes == 20
        assert pkg.publish.inputs == {"publish_arg": "publish_val"}

    def test_from_config_with_empty_build_workflow(self) -> None:
        """Test from_config fails when build_workflow is empty."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    build_workflow="",
                    publish_workflow="publish.yml",
                )
            },
        )

        with pytest.raises(ValueError, match="build_workflow cannot be empty"):
            ReleaseState.from_config(config)

    def test_from_config_with_empty_publish_workflow(self) -> None:
        """Test from_config fails when publish_workflow is empty."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    build_workflow="build.yml",
                    publish_workflow="",
                )
            },
        )

        with pytest.raises(ValueError, match="publish_workflow cannot be empty"):
            ReleaseState.from_config(config)

    def test_from_config_with_whitespace_only_build_workflow(self) -> None:
        """Test from_config fails when build_workflow is whitespace only."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    build_workflow="   ",
                    publish_workflow="publish.yml",
                )
            },
        )

        with pytest.raises(ValueError, match="build_workflow cannot be empty"):
            ReleaseState.from_config(config)

    def test_from_config_with_whitespace_only_publish_workflow(self) -> None:
        """Test from_config fails when publish_workflow is whitespace only."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    build_workflow="build.yml",
                    publish_workflow="   ",
                )
            },
        )

        with pytest.raises(ValueError, match="publish_workflow cannot be empty"):
            ReleaseState.from_config(config)

    def test_from_config_with_multiple_packages(self) -> None:
        """Test from_config with multiple packages."""
        config = Config(
            version=1,
            packages={
                "package1": PackageConfig(
                    repo="test/repo1",
                    build_workflow="build1.yml",
                    publish_workflow="publish1.yml",
                ),
                "package2": PackageConfig(
                    repo="test/repo2",
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
                    build_workflow=False,
                    publish_workflow="publish.yml",
                )
            },
        )

        with pytest.raises(ValueError, match="build_workflow must be a string"):
            ReleaseState.from_config(config)

    def test_from_config_with_boolean_publish_workflow(self) -> None:
        """Test from_config fails when publish_workflow is a boolean."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    build_workflow="build.yml",
                    publish_workflow=False,
                )
            },
        )

        with pytest.raises(ValueError, match="publish_workflow must be a string"):
            ReleaseState.from_config(config)


class TestWorkflowEphemeral:
    """Test cases for Workflow ephemeral field."""

    def test_ephemeral_field_exists(self) -> None:
        """Test that ephemeral field is accessible."""
        workflow = Workflow(workflow_file="test.yml")

        assert hasattr(workflow, "ephemeral")
        assert workflow.ephemeral.trigger_failed is False
        assert workflow.ephemeral.timed_out is False

    def test_ephemeral_field_can_be_modified(self) -> None:
        """Test that ephemeral field values can be modified."""
        workflow = Workflow(workflow_file="test.yml")

        workflow.ephemeral.trigger_failed = True
        workflow.ephemeral.timed_out = True

        assert workflow.ephemeral.trigger_failed is True
        assert workflow.ephemeral.timed_out is True

    def test_ephemeral_field_not_serialized_to_json(self) -> None:
        """Test that ephemeral field is excluded from JSON serialization."""
        workflow = Workflow(workflow_file="test.yml")
        workflow.ephemeral.trigger_failed = True
        workflow.ephemeral.timed_out = True

        # Serialize to JSON
        json_str = workflow.model_dump_json()
        json_data = json.loads(json_str)

        # Verify ephemeral field is not in JSON
        assert "ephemeral" not in json_data
        assert "trigger_failed" not in json_data
        assert "timed_out" not in json_data

        # Verify other fields are present
        assert "workflow_file" in json_data
        assert json_data["workflow_file"] == "test.yml"

    def test_ephemeral_field_not_in_model_dump(self) -> None:
        """Test that ephemeral field is excluded from model_dump."""
        workflow = Workflow(workflow_file="test.yml")
        workflow.ephemeral.trigger_failed = True

        # Get dict representation
        data = workflow.model_dump()

        # Verify ephemeral field is not in dict
        assert "ephemeral" not in data
        assert "trigger_failed" not in data
        assert "timed_out" not in data

    def test_ephemeral_field_initialized_on_deserialization(self) -> None:
        """Test that ephemeral field is initialized when loading from JSON."""
        json_str = '{"workflow_file": "test.yml", "inputs": {}}'

        workflow = Workflow.model_validate_json(json_str)

        # Ephemeral field should be initialized with defaults
        assert hasattr(workflow, "ephemeral")
        assert workflow.ephemeral.trigger_failed is False
        assert workflow.ephemeral.timed_out is False

    def test_release_state_ephemeral_not_serialized(self) -> None:
        """Test that ephemeral fields are not serialized in ReleaseState."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        state = ReleaseState.from_config(config)

        # Modify ephemeral fields
        state.packages["test-package"].build.ephemeral.trigger_failed = True
        state.packages["test-package"].publish.ephemeral.timed_out = True

        # Serialize to JSON
        json_str = state.model_dump_json()
        json_data = json.loads(json_str)

        # Verify ephemeral fields are not in JSON
        build_workflow = json_data["packages"]["test-package"]["build"]
        publish_workflow = json_data["packages"]["test-package"]["publish"]

        assert "ephemeral" not in build_workflow
        assert "trigger_failed" not in build_workflow
        assert "ephemeral" not in publish_workflow
        assert "timed_out" not in publish_workflow


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
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        state = ReleaseState.from_config(config)
        state.packages["test-package"].meta.ephemeral.force_rebuild = True
        assert state.packages["test-package"].meta.ephemeral.force_rebuild is True

    def test_ephemeral_not_serialized(self) -> None:
        """Test that ephemeral field is not serialized to JSON."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        state = ReleaseState.from_config(config)
        state.packages["test-package"].meta.ephemeral.force_rebuild = True

        json_str = state.model_dump_json()
        json_data = json.loads(json_str)

        assert "ephemeral" not in json_data["packages"]["test-package"]["meta"]
        assert "force_rebuild" not in json_data["packages"]["test-package"]["meta"]


class TestStateSyncerWithArgs:
    """Test cases for StateSyncer with ReleaseArgs."""

    def test_state_syncer_sets_tag_from_args(self) -> None:
        """Test that StateSyncer sets tag from ReleaseArgs when creating from config."""
        config = Config(
            version=1,
            packages={
                "test-package": PackageConfig(
                    repo="test/repo",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        args = ReleaseArgs(release_tag="8.4-m01", force_rebuild=[])
        storage = InMemoryStateStorage()
        syncer = StateSyncer(storage=storage, config=config, args=args)

        assert syncer.state.meta.tag == "8.4-m01"

    def test_state_syncer_sets_force_rebuild_from_args(self) -> None:
        """Test that StateSyncer sets force_rebuild flags from ReleaseArgs."""
        config = Config(
            version=1,
            packages={
                "docker": PackageConfig(
                    repo="test/docker",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "redis": PackageConfig(
                    repo="test/redis",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
            },
        )

        args = ReleaseArgs(release_tag="8.4-m01", force_rebuild=["docker"])
        storage = InMemoryStateStorage()
        syncer = StateSyncer(storage=storage, config=config, args=args)

        assert syncer.state.packages["docker"].meta.ephemeral.force_rebuild is True
        assert syncer.state.packages["redis"].meta.ephemeral.force_rebuild is False

    def test_state_syncer_sets_multiple_force_rebuild_from_args(self) -> None:
        """Test that StateSyncer sets multiple force_rebuild flags from ReleaseArgs."""
        config = Config(
            version=1,
            packages={
                "docker": PackageConfig(
                    repo="test/docker",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "redis": PackageConfig(
                    repo="test/redis",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "snap": PackageConfig(
                    repo="test/snap",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
            },
        )

        args = ReleaseArgs(release_tag="8.4-m01", force_rebuild=["docker", "snap"])
        storage = InMemoryStateStorage()
        syncer = StateSyncer(storage=storage, config=config, args=args)

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
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                )
            },
        )

        args = ReleaseArgs(release_tag="test-tag", force_rebuild=[])
        storage = InMemoryStateStorage()
        syncer = StateSyncer(storage=storage, config=config, args=args)

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
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "redis": PackageConfig(
                    repo="test/redis",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "snap": PackageConfig(
                    repo="test/snap",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
            },
        )

        args = ReleaseArgs(release_tag="8.4-m01", force_rebuild=["all"])
        storage = InMemoryStateStorage()
        syncer = StateSyncer(storage=storage, config=config, args=args)

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
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
                "redis": PackageConfig(
                    repo="test/redis",
                    build_workflow="build.yml",
                    publish_workflow="publish.yml",
                ),
            },
        )

        args = ReleaseArgs(release_tag="8.4-m01", force_rebuild=["docker", "all"])
        storage = InMemoryStateStorage()
        syncer = StateSyncer(storage=storage, config=config, args=args)

        # All packages should have force_rebuild set to True
        assert syncer.state.packages["docker"].meta.ephemeral.force_rebuild is True
        assert syncer.state.packages["redis"].meta.ephemeral.force_rebuild is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
