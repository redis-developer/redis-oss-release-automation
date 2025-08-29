# Merge Branches Verified Action

This GitHub action performs verified branch merges during Redis package repository release automation.

## Purpose

Used in package repositories to safely merge branches with verification during the automated release process.

## Usage

```yaml
- uses: ./.github/actions/merge-branches-verified
  with:
    from_branch: "release/8.2"
    to_branch: "main"
    gh_token: ${{ secrets.GITHUB_TOKEN }}
```

## Inputs

- `from_branch` (required): Branch to merge from
- `to_branch` (required): Branch to merge into
- `gh_token` (required): GitHub token for repository access
