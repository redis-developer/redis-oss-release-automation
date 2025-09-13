#!/bin/bash
set -e

# shellcheck disable=SC2034
last_cmd_stdout=""
# shellcheck disable=SC2034
last_cmd_stderr=""
# shellcheck disable=SC2034
last_cmd_result=0
# shellcheck disable=SC2034
VERBOSITY=1

SCRIPT_DIR="$(dirname -- "$( readlink -f -- "$0"; )")"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/../common/helpers.sh"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/../common/github_helpers.sh"

init_console_output

FROM_BRANCH=
TO_BRANCH=
while [[ $# -gt 0 ]]; do
    case $1 in
        --from)
            FROM_BRANCH="$2"
            shift
            shift
            ;;
        --to)
            TO_BRANCH="$2"
            shift
            shift
            ;;
        *)
            echo "Error: Unknown argument $1" >&2
            exit 1
            ;;
    esac
done

if [ -z "$FROM_BRANCH" ] || [ -z "$TO_BRANCH" ]; then
    echo "Error: Missing required arguments --from and --to" >&2
    exit 1
fi

echo "Checking if merge is required from '$FROM_BRANCH' to '$TO_BRANCH'..." >&2

execute_command --no-std -- git_fetch_unshallow origin "$FROM_BRANCH" "$TO_BRANCH"
execute_command --no-std -- git rev-parse "origin/$FROM_BRANCH"
FROM_SHA=$last_cmd_stdout
execute_command --no-std -- git rev-parse "origin/$TO_BRANCH"
TO_SHA=$last_cmd_stdout

if [ -n "$GITHUB_OUTPUT" ]; then
    echo "target_before_merge_sha=$TO_SHA" >> "$GITHUB_OUTPUT"
    echo "source_commit_sha=$FROM_SHA" >> "$GITHUB_OUTPUT"
fi

# Check if FROM_BRANCH is already merged into TO_BRANCH
execute_command --ignore-exit-code 1 --no-std -- git merge-base --is-ancestor "origin/$FROM_BRANCH" "origin/$TO_BRANCH"
if [ $last_cmd_result -eq 0 ]; then
    echo "Branch '$FROM_BRANCH' is already merged into '$TO_BRANCH' - no merge required" >&2
    exit 0
fi

# Check if the branches are identical
if [ "$FROM_SHA" = "$TO_SHA" ]; then
    echo "Branches '$FROM_BRANCH' and '$TO_BRANCH' are identical - no merge required" >&2
    exit 0
fi

merge_commit_sha=$(execute_command github_create_verified_merge --from "$FROM_BRANCH" --to "$TO_BRANCH")
if [ -n "$GITHUB_OUTPUT" ]; then
    if echo "$merge_commit_sha" | grep -Pq '^[0-9a-fA-F]{40,}\s*$'; then
        echo "merge_commit_sha=$merge_commit_sha" >> "$GITHUB_OUTPUT"
    fi
fi