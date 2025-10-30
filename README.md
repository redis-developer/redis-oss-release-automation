# Redis OSS Release Automation CLI

A command-line tool for automating Redis OSS releases across multiple package repositories.

## Installation

### Using uv

```bash
git clone https://github.com/redis/redis-oss-release-automation.git
cd redis-oss-release-automation
uv sync
```

After `uv sync`, you can run the tool in two ways:
- **With `uv run`**: `uv run redis-release <command>`
- **Activate virtual environment**: `. .venv/bin/activate` then `redis-release <command>`

## Prerequisites

1. **GitHub Token**: Personal access token with workflow permissions
2. **AWS Credentials**: Access to S3 bucket for state storage
3. **Package Repositories**: Access to Redis package repositories

### Environment Variables

```bash
export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"
export AWS_ACCESS_KEY_ID="your-access-key-id"
export AWS_SECRET_ACCESS_KEY="your-secret-access-key"
export AWS_SESSION_TOKEN="your-session-token"
export REDIS_RELEASE_STATE_BUCKET="redis-release-state"
```

### AWS SSO Login

In AWS, you can also use `aws sso login` prior to running the tool to authenticate.

## Usage

### Basic Release

By default, `config.yaml` is used. You can specify a different config file with `--config`:

```bash
# Start a new release (uses config.yaml by default)
redis-release release 8.2.0

# Use custom config file
redis-release release 8.2.0 --config custom-config.yaml

# Force rebuild all packages (WARNING: This will delete all existing state!)
redis-release release 8.2.0 --force-rebuild all

# Force rebuild specific package
redis-release release 8.2.0 --force-rebuild package-name

# Release only specific packages (can be used multiple times)
redis-release release 8.2.0 --only-packages package1 --only-packages package2

# Force release type (changes release-type even for existing state)
redis-release release 8.2.0 --force-release-type rc
```

### Check Status

```bash
# Check release status
redis-release status 8.2.0
```

## Troubleshooting

### Dangling Release Locks

If you encounter a dangling lock file, you can delete it from the S3 bucket:

```bash
aws s3 rm s3://redis-release-state/release-locks/TAG.lock
```

Replace `TAG` with the release tag (e.g., `8.2.0`).

## Diagrams

Generate release workflow diagrams using:

```bash
# Generate full release diagram
redis-release release-print

# Generate diagram with custom name (list available with --help)
redis-release release-print --name NAME
```

**Note**: Graphviz is required to generate diagrams.

## Configuration

The tool uses a YAML configuration file to define release packages and their settings. By default, `config.yaml` is used.

See `config.yaml` for an example configuration file.
