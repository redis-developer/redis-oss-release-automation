from typing import Dict, List, Optional

from typer import BadParameter

from redis_release.models import RedisModule, ReleaseType


def parse_force_release_type(
    force_release_type_list: Optional[List[str]],
) -> Dict[str, ReleaseType]:
    """Parse force_release_type arguments from 'package_name:release_type' format.

    Args:
        force_release_type_list: List of strings in format 'package_name:release_type'

    Returns:
        Dictionary mapping package names to ReleaseType

    Raises:
        BadParameter: If format is invalid or release type is unknown
    """
    if not force_release_type_list:
        return {}

    result = {}
    for item in force_release_type_list:
        if ":" not in item:
            raise BadParameter(
                f"Invalid format '{item}'. Expected 'package_name:release_type' (e.g., 'docker:internal')"
            )

        package_name, release_type_str = item.split(":", 1)
        package_name = package_name.strip()
        release_type_str = release_type_str.strip().lower()

        try:
            release_type = ReleaseType(release_type_str)
        except ValueError:
            valid_types = ", ".join([rt.value for rt in ReleaseType])
            raise BadParameter(
                f"Invalid release type '{release_type_str}'. Valid types: {valid_types}"
            )

        result[package_name] = release_type

    return result


def parse_module_versions(
    module_versions_list: Optional[List[str]],
) -> Dict[RedisModule, str]:
    """Parse module versions from 'module_name:version' format.

    Args:
        module_versions_list: List of strings in format 'module_name:version'

    Returns:
        Dictionary mapping RedisModule to version strings

    Raises:
        BadParameter: If format is invalid or module name is unknown
    """
    if not module_versions_list:
        return {}

    result = {}
    for item in module_versions_list:
        if ":" not in item:
            raise BadParameter(
                f"Invalid format '{item}'. Expected 'module_name:version' (e.g., 'redisjson:2.6.0')"
            )

        module_name, version = item.split(":", 1)
        module_name = module_name.strip().lower()
        version = version.strip()

        # Find matching RedisModule enum value
        module_enum = None
        for module in RedisModule:
            if module.value.lower() == module_name:
                module_enum = module
                break

        if module_enum is None:
            valid_modules = ", ".join([m.value for m in RedisModule])
            raise BadParameter(
                f"Invalid module name '{module_name}'. Valid modules: {valid_modules}"
            )

        result[module_enum] = version

    return result
