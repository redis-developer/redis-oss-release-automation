#!/bin/bash
set -e

# This script validates a Redis release archive by downloading it and calculating its SHA256 sum.
# It constructs the URL, downloads the file, calculates the hash, and exports environment variables.

# Input TAG is expected in $1
TAG="$1"

if [ -z "$TAG" ]; then
    echo "Error: TAG is required as first argument"
    exit 1
fi

# Construct Redis archive URL
#REDIS_ARCHIVE_URL="https://github.com/redis/redis/archive/refs/tags/${TAG}.tar.gz"
REDIS_ARCHIVE_URL="https://download.redis.io/releases/redis-${TAG}.tar.gz"
echo "REDIS_ARCHIVE_URL: $REDIS_ARCHIVE_URL"

# Download the Redis archive
TEMP_ARCHIVE="/tmp/redis-${TAG}.tar.gz"
echo "Downloading Redis archive to $TEMP_ARCHIVE..."
if ! curl -sfL -o "$TEMP_ARCHIVE" "$REDIS_ARCHIVE_URL"; then
    echo "Error: Failed to download Redis archive from $REDIS_ARCHIVE_URL"
    exit 1
fi

# Calculate SHA256 sum
echo "Calculating SHA256 sum..."
REDIS_ARCHIVE_SHA=$(sha256sum "$TEMP_ARCHIVE" | cut -d' ' -f1)
echo "REDIS_ARCHIVE_SHA: $REDIS_ARCHIVE_SHA"

# Write variables to GITHUB_ENV
if [ -n "$GITHUB_ENV" ]; then
    echo "REDIS_ARCHIVE_URL=$REDIS_ARCHIVE_URL" >> "$GITHUB_ENV"
    echo "REDIS_ARCHIVE_SHA=$REDIS_ARCHIVE_SHA" >> "$GITHUB_ENV"
    echo "Environment variables written to $GITHUB_ENV"
else
    echo "Error: GITHUB_ENV not set"
    # Clean up temporary file
    rm -f "$TEMP_ARCHIVE"
    exit 1
fi

# Clean up temporary file
rm -f "$TEMP_ARCHIVE"

echo "Redis archive validation completed successfully"