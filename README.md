# Redis OSS Release Automation CLI

A command-line tool for automating Redis OSS releases across multiple package repositories.

## Installation

### From Source

```bash
git clone https://github.com/redis/redis-oss-release-automation.git
cd redis-oss-release-automation
pip install -e .
```

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

## Usage

### Basic Release

```bash
# Start a new release
redis-release release 8.2.0

# Force rebuild packages
redis-release release 8.2.0 --force-rebuild
```

### Check Status

```bash
# Check release status
redis-release status 8.2.0
```

### Advanced Options

```bash
# Dry run mode (simulate without changes)
redis-release release 8.2.0 --dry-run
```
