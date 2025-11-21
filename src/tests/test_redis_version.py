"""Tests for data models."""

import pytest

from redis_release.models import RedisVersion


class TestRedisVersion:
    """Tests for RedisVersion model."""

    def test_parse_basic_version(self):
        """Test parsing basic version strings."""
        version = RedisVersion.parse("8.2.1")
        assert version.major == 8
        assert version.minor == 2
        assert version.patch == 1
        assert version.suffix == ""

    def test_parse_version_with_v_prefix(self):
        """Test parsing version with 'v' prefix."""
        version = RedisVersion.parse("v8.2.1")
        assert version.major == 8
        assert version.minor == 2
        assert version.patch == 1
        assert version.suffix == ""

    def test_parse_version_with_suffix(self):
        """Test parsing version with suffix."""
        version = RedisVersion.parse("8.2.1-m01")
        assert version.major == 8
        assert version.minor == 2
        assert version.patch == 1
        assert version.suffix == "-m01"

    def test_parse_version_without_patch(self):
        """Test parsing version without patch number."""
        version = RedisVersion.parse("8.2")
        assert version.major == 8
        assert version.minor == 2
        assert version.patch is None
        assert version.suffix == ""

    def test_parse_eol_version(self):
        """Test parsing EOL version."""
        version = RedisVersion.parse("7.4.0-eol")
        assert version.major == 7
        assert version.minor == 4
        assert version.patch == 0
        assert version.suffix == "-eol"
        assert version.is_eol is True

    def test_parse_rc_internal_version(self):
        """Test parsing RC internal version."""
        version = RedisVersion.parse("8.2.1-rc2-int3")
        assert version.major == 8
        assert version.minor == 2
        assert version.patch == 1
        assert version.suffix == "-rc2-int3"
        assert version.is_rc is True
        assert version.is_internal is True
        assert len(version.sort_key) > 0

        version = RedisVersion.parse("8.4-int")
        assert version.major == 8
        assert version.minor == 4
        assert version.patch == None
        assert version.suffix == "-int"
        assert version.is_internal is True
        assert len(version.sort_key) > 0

    def test_parse_invalid_version(self):
        """Test parsing invalid version strings."""
        with pytest.raises(ValueError):
            RedisVersion.parse("invalid")

        with pytest.raises(ValueError):
            RedisVersion.parse("0.1.0")  # Major version must be >= 1

    def test_is_milestone(self):
        """Test milestone detection."""
        ga_version = RedisVersion.parse("8.2.1")
        milestone_version = RedisVersion.parse("8.2.1-m01")

        assert ga_version.is_milestone is False
        assert milestone_version.is_milestone is True

    def test_mainline_version(self):
        """Test mainline version property."""
        version = RedisVersion.parse("8.2.1-m01")
        assert version.mainline_version == "8.2"

    def test_string_representation(self):
        """Test string representation."""
        version1 = RedisVersion.parse("8.2.1")
        version2 = RedisVersion.parse("8.2.1-m01")
        version3 = RedisVersion.parse("8.2")

        assert str(version1) == "8.2.1"
        assert str(version2) == "8.2.1-m01"
        assert str(version3) == "8.2"

    def test_version_comparison(self):
        """Test version comparison for sorting."""
        v8_2_1 = RedisVersion.parse("8.2.1")
        v8_2_2 = RedisVersion.parse("8.2.2")
        v8_2_1_m_01 = RedisVersion.parse("8.2.1-m01")
        v8_2_1_rc_01 = RedisVersion.parse("8.2.1-rc01")
        v8_2_1_rc_01_int_1 = RedisVersion.parse("8.2.1-rc01-int1")
        v8_3_0 = RedisVersion.parse("8.3.0")
        v8_3_0_rc_1 = RedisVersion.parse("8.3.0-rc1")
        v8_3_0_rc_1_int_1 = RedisVersion.parse("8.3.0-rc1-int1")
        v8_3_0_rc_1_int_2 = RedisVersion.parse("8.3.0-rc1-int2")
        v8_4 = RedisVersion.parse("8.4")
        v8_4_rc_1 = RedisVersion.parse("8.4-rc1")
        v8_6_int = RedisVersion.parse("8.6-int")

        # Test numeric comparison
        assert v8_2_1 < v8_2_2
        assert v8_2_2 < v8_3_0

        # Test milestone vs GA (GA comes after milestone)
        assert v8_2_1_m_01 < v8_2_1

        assert v8_3_0_rc_1 < v8_3_0

        assert v8_2_1_rc_01 > v8_2_1_m_01
        assert v8_2_1_rc_01_int_1 > v8_2_1_m_01
        assert v8_2_1_rc_01_int_1 < v8_2_1_rc_01

        assert v8_3_0_rc_1_int_1 < v8_3_0_rc_1_int_2

        assert v8_3_0_rc_1 > v8_3_0_rc_1_int_1
        assert v8_3_0_rc_1 > v8_3_0_rc_1_int_2

        # Test sorting
        versions = [
            v8_3_0,
            v8_2_1,
            v8_2_1_m_01,
            v8_2_2,
            v8_3_0_rc_1,
            v8_3_0_rc_1_int_1,
            v8_3_0_rc_1_int_2,
            v8_6_int,
            v8_4,
            v8_4_rc_1,
            v8_2_1_rc_01,
            v8_2_1_rc_01_int_1,
        ]
        sorted_versions = sorted(versions)
        assert sorted_versions == [
            v8_2_1_m_01,
            v8_2_1_rc_01_int_1,
            v8_2_1_rc_01,
            v8_2_1,
            v8_2_2,
            v8_3_0_rc_1_int_1,
            v8_3_0_rc_1_int_2,
            v8_3_0_rc_1,
            v8_3_0,
            v8_4_rc_1,
            v8_4,
            v8_6_int,
        ]
