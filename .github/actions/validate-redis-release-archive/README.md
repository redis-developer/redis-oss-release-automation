# Validate Redis Release Archive Action

This GitHub action validates Redis release archives during package repository release automation.

## Purpose

Used in package repositories to validate Redis release archives before building packages during the automated release process.

## Usage

```yaml
- uses: ./.github/actions/validate-redis-release-archive
  with:
    release_tag: "8.2.1"
```

## Inputs

- `release_tag` (required): Redis release tag to validate (e.g., "8.2.1")
