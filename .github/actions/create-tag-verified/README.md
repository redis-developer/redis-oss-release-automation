# Create Tag Verified Action

This GitHub action creates verified tags using the GitHub API during Redis package repository release automation.

## Purpose

Used in package repositories to create verified tags during the automated release process. This ensures tags are properly signed and verified by GitHub.

## Usage

```yaml
- uses: ./.github/actions/create-tag-verified
  with:
    tag_name: "v8.2.1"
    ref_sha: "abc123def456"
    tag_message: "Release v8.2.1"
    gh_token: ${{ secrets.GITHUB_TOKEN }}
```

## Inputs

- `tag_name` (required): Name of the tag to create
- `ref_sha` (required): SHA of the commit to tag
- `tag_message` (optional): Tag message (defaults to tag name)
- `gh_token` (required): GitHub token for repository access

## Notes

- Creates verified tags that appear as verified in GitHub UI
- Uses GitHub API to ensure proper verification
- Fails if tag already exists