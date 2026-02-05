"""Tests for tree.py functions."""

import pytest

from redis_release.bht.state import Package, PackageMeta, ReleaseState
from redis_release.bht.tree import arrange_packages_list, resolve_package_deps
from redis_release.config import Config, PackageConfig
from redis_release.models import PackageType


class TestResolvePackageDeps:
    """Tests for resolve_package_deps function."""

    def test_no_dependencies(self) -> None:
        """Test packages with no dependencies."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                ),
            },
        )

        result = resolve_package_deps(["pkg1", "pkg2"], config)

        assert set(result) == {"pkg1", "pkg2"}

    def test_single_dependency(self) -> None:
        """Test package with a single dependency."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    needs=["pkg2"],
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                ),
            },
        )

        result = resolve_package_deps(["pkg1"], config)

        assert set(result) == {"pkg1", "pkg2"}

    def test_multiple_dependencies(self) -> None:
        """Test package with multiple dependencies."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    needs=["pkg2", "pkg3"],
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                ),
                "pkg3": PackageConfig(
                    repo="test/repo3",
                    package_type=PackageType.RPM,
                    build_workflow="build.yml",
                ),
            },
        )

        result = resolve_package_deps(["pkg1"], config)

        assert set(result) == {"pkg1", "pkg2", "pkg3"}

    def test_no_duplicate_dependencies(self) -> None:
        """Test that dependencies are not duplicated."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    needs=["pkg3"],
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    needs=["pkg3"],
                ),
                "pkg3": PackageConfig(
                    repo="test/repo3",
                    package_type=PackageType.RPM,
                    build_workflow="build.yml",
                ),
            },
        )

        result = resolve_package_deps(["pkg1", "pkg2"], config)

        # pkg3 should only appear once
        assert set(result) == {"pkg1", "pkg2", "pkg3"}
        assert len(result) == 3

    def test_dependency_already_in_list(self) -> None:
        """Test when dependency is already in the input list."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    needs=["pkg2"],
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                ),
            },
        )

        result = resolve_package_deps(["pkg1", "pkg2"], config)

        assert set(result) == {"pkg1", "pkg2"}
        assert len(result) == 2

    def test_empty_packages_list(self) -> None:
        """Test with empty packages list."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                ),
            },
        )

        result = resolve_package_deps([], config)

        assert result == []

    def test_package_not_in_config(self) -> None:
        """Test with package not in config (should still include it)."""
        config = Config(
            version=1,
            packages={},
        )

        result = resolve_package_deps(["unknown_pkg"], config)

        assert result == ["unknown_pkg"]

    def test_transitive_dependencies(self) -> None:
        """Test that dependencies of dependencies are resolved."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    needs=["pkg2"],
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    needs=["pkg3"],
                ),
                "pkg3": PackageConfig(
                    repo="test/repo3",
                    package_type=PackageType.RPM,
                    build_workflow="build.yml",
                ),
            },
        )

        result = resolve_package_deps(["pkg1"], config)

        # pkg1 needs pkg2, pkg2 needs pkg3, so all three should be included
        assert set(result) == {"pkg1", "pkg2", "pkg3"}

    def test_deep_transitive_dependencies(self) -> None:
        """Test deeply nested transitive dependencies."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    needs=["pkg2"],
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    needs=["pkg3"],
                ),
                "pkg3": PackageConfig(
                    repo="test/repo3",
                    package_type=PackageType.RPM,
                    build_workflow="build.yml",
                    needs=["pkg4"],
                ),
                "pkg4": PackageConfig(
                    repo="test/repo4",
                    package_type=PackageType.HOMEBREW,
                    build_workflow="build.yml",
                ),
            },
        )

        result = resolve_package_deps(["pkg1"], config)

        assert set(result) == {"pkg1", "pkg2", "pkg3", "pkg4"}

    def test_circular_dependencies(self) -> None:
        """Test that circular dependencies don't cause infinite loop."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    needs=["pkg2"],
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    needs=["pkg1"],
                ),
            },
        )

        result = resolve_package_deps(["pkg1"], config)

        assert set(result) == {"pkg1", "pkg2"}


class TestArrangePackagesList:
    """Tests for arrange_packages_list function."""

    def _create_packages_dict(self, names: list[str]) -> dict[str, Package]:
        """Helper to create a packages dict from names."""
        return {
            name: Package(meta=PackageMeta(repo=f"test/{name}", ref="main"))
            for name in names
        }

    def test_all_packages_no_filter(self) -> None:
        """Test returning all packages when no filter is applied."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                ),
            },
        )
        packages = self._create_packages_dict(["pkg1", "pkg2"])

        result = arrange_packages_list(
            config=config,
            packages=packages,
            only_packages=[],
            custom_build=False,
        )

        assert set(result) == {"pkg1", "pkg2"}

    def test_only_packages_filter(self) -> None:
        """Test filtering with only_packages."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                ),
                "pkg3": PackageConfig(
                    repo="test/repo3",
                    package_type=PackageType.RPM,
                    build_workflow="build.yml",
                ),
            },
        )
        packages = self._create_packages_dict(["pkg1", "pkg2", "pkg3"])

        result = arrange_packages_list(
            config=config,
            packages=packages,
            only_packages=["pkg1", "pkg3"],
            custom_build=False,
        )

        assert set(result) == {"pkg1", "pkg3"}

    def test_custom_build_mode(self) -> None:
        """Test custom build mode only includes allow_custom_build packages."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    allow_custom_build=True,
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    allow_custom_build=False,
                ),
                "pkg3": PackageConfig(
                    repo="test/repo3",
                    package_type=PackageType.RPM,
                    build_workflow="build.yml",
                    allow_custom_build=True,
                ),
            },
        )
        packages = self._create_packages_dict(["pkg1", "pkg2", "pkg3"])

        result = arrange_packages_list(
            config=config,
            packages=packages,
            only_packages=[],
            custom_build=True,
        )

        assert set(result) == {"pkg1", "pkg3"}

    def test_custom_build_with_only_packages(self) -> None:
        """Test custom build mode with only_packages filter."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    allow_custom_build=True,
                ),
                "pkg2": PackageConfig(
                    repo="test/repo2",
                    package_type=PackageType.DEBIAN,
                    build_workflow="build.yml",
                    allow_custom_build=True,
                ),
                "pkg3": PackageConfig(
                    repo="test/repo3",
                    package_type=PackageType.RPM,
                    build_workflow="build.yml",
                    allow_custom_build=True,
                ),
            },
        )
        packages = self._create_packages_dict(["pkg1", "pkg2", "pkg3"])

        result = arrange_packages_list(
            config=config,
            packages=packages,
            only_packages=["pkg1"],
            custom_build=True,
        )

        assert result == ["pkg1"]

    def test_no_packages_raises_error(self) -> None:
        """Test that empty result raises ValueError."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                ),
            },
        )
        packages = self._create_packages_dict(["pkg1"])

        with pytest.raises(ValueError, match="No packages left after filtering"):
            arrange_packages_list(
                config=config,
                packages=packages,
                only_packages=["nonexistent"],
                custom_build=False,
            )

    def test_custom_build_no_available_packages_raises_error(self) -> None:
        """Test that custom build with no allow_custom_build packages raises error."""
        config = Config(
            version=1,
            packages={
                "pkg1": PackageConfig(
                    repo="test/repo1",
                    package_type=PackageType.DOCKER,
                    build_workflow="build.yml",
                    allow_custom_build=False,
                ),
            },
        )
        packages = self._create_packages_dict(["pkg1"])

        with pytest.raises(
            ValueError, match="No available packages found in config for custom build"
        ):
            arrange_packages_list(
                config=config,
                packages=packages,
                only_packages=[],
                custom_build=True,
            )
