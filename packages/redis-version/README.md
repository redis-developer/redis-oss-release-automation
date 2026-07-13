# redis-version

Small Redis version parsing and comparison package.

## Local CLI usage

From the `redis-oss-release-automation` repository root, run the CLI through uv:

```bash
uv run redis-version major 8.2.1
uv run redis-version minor 8.2.1
uv run redis-version patch 8.2.1
uv run redis-version parts 8.2.1-rc1
uv run redis-version check 8.2.1-rc1 is-rc
uv run redis-version compare 8.2.1-rc1 '<=' 8.2.1
```

From this package directory, the same commands work:

```bash
cd packages/redis-version
uv run redis-version compare 8.2.1-rc1 '<=' 8.2.1
```

`check` and `compare` are intended for shell scripts. They exit with `0` for true,
`1` for false, and `2` for parse or argument errors.

## Local package import

When working in the `redis-oss-release-automation` uv workspace, import the package directly:

```python
from redis_version import RedisVersion

version = RedisVersion.parse("8.2.1-rc1")
assert version.is_rc
assert str(version) == "8.2.1-rc1"
```

The main release automation package also re-exports the same class for compatibility:

```python
from redis_release.models import RedisVersion
```

## Use from another local project

Add the package from a local checkout during development:

```bash
uv add --editable /home/petar/dev/redis-oss-release-automation/packages/redis-version
```

Then import it normally:

```python
from redis_version import RedisVersion
```

## Run the CLI from GitHub

Run the tool directly from the GitHub repository subdirectory with `uvx`:

```bash
uvx 'redis-version @ git+https://github.com/redis/redis-oss-release-automation.git#subdirectory=packages/redis-version' compare 8.2.1-rc1 '<=' 8.2.1
```

For SSH access:

```bash
uvx 'redis-version @ git+ssh://git@github.com/redis/redis-oss-release-automation.git#subdirectory=packages/redis-version' compare 8.2.1-rc1 '<=' 8.2.1
```

To install the command on your `PATH` instead of running it ephemerally:

```bash
uv tool install 'redis-version @ git+https://github.com/redis/redis-oss-release-automation.git#subdirectory=packages/redis-version'
redis-version compare 8.2.1-rc1 '<=' 8.2.1
```

Pin to a tag or commit for reproducible usage:

```bash
uvx 'redis-version @ git+https://github.com/redis/redis-oss-release-automation.git@main#subdirectory=packages/redis-version' compare 8.2.1-rc1 '<=' 8.2.1
```

## Add as a GitHub dependency

From another uv project, add the package from the GitHub subdirectory:

```bash
uv add 'redis-version @ git+https://github.com/redis/redis-oss-release-automation.git#subdirectory=packages/redis-version'
```

Then import it normally:

```python
from redis_version import RedisVersion
```
