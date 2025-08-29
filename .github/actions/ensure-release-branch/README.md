# Ensure Release Branch Action

This GitHub action ensures that the correct release branches exist and are up to date during Redis package repository release automation.

## Purpose

This action is intended to be used in package repositories (such as Docker, Debian, RPM packages) during the automated release process. It ensures that:

- The appropriate release branch exists for the given release tag
- The branch is properly synchronized with the latest changes
- Branch naming follows the expected convention for Redis releases

## Usage

```yaml
- uses: ./.github/actions/ensure-release-branch
  with:
    release_tag: "8.2.1"
    allow_modify: true
    gh_token: ${{ secrets.GITHUB_TOKEN }}
```

## Inputs

- `release_tag` (required): The Redis release tag to build (e.g., "8.2.1")
- `allow_modify` (optional): Whether to allow modifying the repository (default: false)
- `gh_token` (required): GitHub token for repository access

## Outputs

- `release_version_branch`: The release version branch name (e.g., "8.2.1-int1")
- `release_branch`: The release branch name (e.g., "release/8.2")
