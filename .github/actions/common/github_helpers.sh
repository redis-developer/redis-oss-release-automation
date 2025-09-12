#!/bin/bash
git_fetch_unshallow() {
    git fetch --unshallow "$@" 2>/dev/null || git fetch "$@"
}

# Merges branch --from into branch --to using github API
# GITHUB_TOKEN must be set in the environment
# This is the only way to create verified merge commits in CI
github_create_verified_merge() {
    BASE_BRANCH=
    HEAD_BRANCH=
    while [[ $# -gt 0 ]]; do
    case $1 in
        --from)
            HEAD_BRANCH="$2"
            shift
            shift
            ;;
        --to)
            BASE_BRANCH="$2"
            shift
            shift
            ;;
        *)
            echo "Error: Unknown argument $1"
            return 1
            ;;
    esac
    done

    if [ -z "$BASE_BRANCH" ] || [ -z "$HEAD_BRANCH" ]; then
        echo "Error: Missing required arguments --from and --to" >&2
        return 1
    fi

    # Create a verified merge commit on GitHub (HEAD_BRANCH -> BASE_BRANCH)
    API_URL="https://api.github.com/repos/${GITHUB_REPOSITORY}/merges"

    PAYLOAD="{\"base\":\"${BASE_BRANCH}\",\"head\":\"${HEAD_BRANCH}\",\"commit_message\":\"Merge ${HEAD_BRANCH} into ${BASE_BRANCH} (bot)\"}"

    # Make the request and capture status code + body
    HTTP_CODE=$(curl -sS -w "%{http_code}" -o /tmp/merge.json \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$API_URL" \
    -d "$PAYLOAD")

    case "$HTTP_CODE" in
        201)
            local merge_sha
            merge_sha=$(jq -r '.sha' /tmp/merge.json)
            echo "✅ Verified merge created: $merge_sha" >&2
            echo "$merge_sha"
            ;;
        204) echo "✔️  Already up to date (no merge necessary)" >&2;;
        409) echo "❌ Merge conflict; open a PR to resolve" >&2; cat /tmp/merge.json >&2; return 1;;
        *)   echo "❌ Unexpected status $HTTP_CODE" >&2; cat /tmp/merge.json >&2; return 1;;
    esac
}

# Creates a tag using GitHub API
# GITHUB_TOKEN must be set in the environment
# This is the only way to create verified tags in CI
github_create_verified_tag() {
    TAG_NAME=
    TAG_MESSAGE=
    REF_SHA=
    while [[ $# -gt 0 ]]; do
    case $1 in
        --message)
            TAG_MESSAGE="$2"
            shift
            shift
            ;;
        --ref)
            REF_SHA="$2"
            shift
            shift
            ;;
        *)
            if [ -z "$TAG_NAME" ]; then
                TAG_NAME="$1"
            else
                echo "Error: Unknown argument $1"
                return 1
            fi
            shift
            ;;
    esac
    done

    if [ -z "$TAG_NAME" ]; then
        echo "Error: Tag name is required" >&2
        return 1
    fi

    if [ -z "$REF_SHA" ]; then
        echo "Error: Missing required argument --ref" >&2
        return 1
    fi

    # Use tag name as default message if not provided
    if [ -z "$TAG_MESSAGE" ]; then
        TAG_MESSAGE="$TAG_NAME"
    fi

    # Create tag using GitHub API
    API_URL="https://api.github.com/repos/${GITHUB_REPOSITORY}/git/tags"

    PAYLOAD="{\"tag\":\"${TAG_NAME}\",\"message\":\"${TAG_MESSAGE}\",\"object\":\"${REF_SHA}\",\"type\":\"commit\"}"

    # Make the request and capture status code + body
    HTTP_CODE=$(curl -sS -w "%{http_code}" -o /tmp/tag.json \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$API_URL" \
    -d "$PAYLOAD")

    case "$HTTP_CODE" in
        201)
            local tag_sha
            tag_sha=$(jq -r '.sha' /tmp/tag.json)
            echo "✅ Verified tag created: $tag_sha" >&2

            # Create the reference
            REF_API_URL="https://api.github.com/repos/${GITHUB_REPOSITORY}/git/refs"
            REF_PAYLOAD="{\"ref\":\"refs/tags/${TAG_NAME}\",\"sha\":\"${tag_sha}\"}"

            REF_HTTP_CODE=$(curl -sS -w "%{http_code}" -o /tmp/ref.json \
            -X POST \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer ${GITHUB_TOKEN}" \
            -H "X-GitHub-Api-Version: 2022-11-28" \
            "$REF_API_URL" \
            -d "$REF_PAYLOAD")

            if [ "$REF_HTTP_CODE" = "201" ]; then
                echo "$tag_sha"
            else
                echo "❌ Failed to create tag reference: $REF_HTTP_CODE" >&2
                cat /tmp/ref.json >&2
                return 1
            fi
            ;;
        422) echo "❌ Tag already exists or validation error" >&2; cat /tmp/tag.json >&2; return 1;;
        *)   echo "❌ Unexpected status $HTTP_CODE" >&2; cat /tmp/tag.json >&2; return 1;;
    esac
}
