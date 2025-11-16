#!/bin/bash

# This script ensures that a release branch and release version branch exist for a given release tag.

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

# Parse arguments
ALLOW_MODIFY=""
TAG=""
RELEASE_BRANCH=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --allow-modify)
            ALLOW_MODIFY=1
            shift
            ;;
        --release-branch)
            RELEASE_BRANCH="$2"
            shift
            shift
            ;;
        -*)
            echo "Error: Unknown option $1"
            exit 1
            ;;
        *)
            if [ -z "$TAG" ]; then
                TAG="$1"
            else
                echo "Error: Multiple TAG arguments provided"
                exit 1
            fi
            shift
            ;;
    esac
done

if [ -z "$TAG" ]; then
    echo "Error: TAG is required as argument"
    echo "Usage: $0 [--allow-modify] [--release-branch BRANCH] <TAG>"
    exit 1
fi

# Define RELEASE_VERSION_BRANCH which is the same as TAG
RELEASE_VERSION_BRANCH="$TAG"

echo "release_version_branch=$RELEASE_VERSION_BRANCH" >> "$GITHUB_OUTPUT"

echo "TAG: $TAG"
echo "RELEASE_VERSION_BRANCH: $RELEASE_VERSION_BRANCH"

if [ -z "$RELEASE_BRANCH" ]; then
    # Detect RELEASE_BRANCH name (release/X.Y format)
    RELEASE_BRANCH="release/$(echo "$TAG" | grep -Po '^\d+\.\d+')"
fi

echo "RELEASE_BRANCH: $RELEASE_BRANCH"
echo "release_branch=$RELEASE_BRANCH" >> "$GITHUB_OUTPUT"

# Check if RELEASE_BRANCH exists in origin
if execute_command git ls-remote --heads origin "$RELEASE_BRANCH" | grep -q "$RELEASE_BRANCH"; then
    echo "Branch $RELEASE_BRANCH exists in origin"
    execute_command --no-std -- git_fetch_unshallow origin "$RELEASE_BRANCH"
else
    echo "Branch $RELEASE_BRANCH does not exist in origin, need to create it"
    if [ -z "$ALLOW_MODIFY" ]; then
        echo "Refuse to modify repository without --allow-modify option"
        exit 1
    fi

    # Detect base branch (previous existing branch for the version)
    MAJOR_MINOR=$(echo "$TAG" | grep -Po '^\d+\.\d+')
    MAJOR=$(echo "$MAJOR_MINOR" | cut -d. -f1)

    # Find the previous existing release branch
    execute_command --no-std -- git ls-remote --heads origin "release/$MAJOR.[0-9]"
    BASE_BRANCH=$(echo "$last_cmd_stdout" | grep -oP 'release/\d+\.\d+' | sort -V | tail -n 1)

    if [ -z "$BASE_BRANCH" ]; then
        echo "Error: Could not find a base branch for $RELEASE_BRANCH"
        exit 1
    fi

    echo "Using base branch: $BASE_BRANCH"

    # Create new branch based on base branch and push to origin
    execute_command --no-std -- git_fetch_unshallow origin "$BASE_BRANCH"
    execute_command --no-std -- git checkout -b "$RELEASE_BRANCH" "origin/$BASE_BRANCH"
    execute_command --no-std -- git push origin HEAD:"$RELEASE_BRANCH"
    echo "Created and pushed $RELEASE_BRANCH based on $BASE_BRANCH"
fi

# Check if RELEASE_VERSION_BRANCH exists in origin
if execute_command git ls-remote --heads origin "$RELEASE_VERSION_BRANCH" | grep -q "$RELEASE_VERSION_BRANCH"; then
    execute_command --no-std -- git_fetch_unshallow origin "$RELEASE_VERSION_BRANCH"

    # Check if there are changes in release branch that are not in release version branch
    echo "Checking for differences between $RELEASE_BRANCH and $RELEASE_VERSION_BRANCH..."
    execute_command --no-std -- git_fetch_unshallow origin "$RELEASE_BRANCH"

    # Check if there are commits in RELEASE_BRANCH that are not in RELEASE_VERSION_BRANCH
    execute_command --no-std -- git rev-list --count "origin/$RELEASE_VERSION_BRANCH..origin/$RELEASE_BRANCH"
    COMMITS_TO_MERGE=$(echo "$last_cmd_stdout" | tr -d '[:space:]')

    if [ "$COMMITS_TO_MERGE" -gt 0 ]; then
        # Compare the two branches to see if there are actual file differences
        # The reliable way to check the differences ignoring merges from version
        # branch into release branch is to perform a merge and check the result
        execute_command --no-std -- git switch -c tmp-rvb "origin/$RELEASE_VERSION_BRANCH"
        execute_command --no-std -- git -c user.name="github-actions[bot]" \
        -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
        merge --no-commit --no-ff "origin/$RELEASE_BRANCH"
        execute_command --ignore-exit-code 1 --no-std -- git diff --quiet --cached "origin/$RELEASE_VERSION_BRANCH"
        diff_result=$last_cmd_result
        execute_command --ignore-errors --no-std -- git merge --abort
        # Switch back to original branch
        execute_command --no-std -- git switch -
        if [ "$diff_result" -eq 1 ]; then
            echo "Found file differences between $RELEASE_BRANCH and $RELEASE_VERSION_BRANCH"
            execute_command --no-std -- git diff --name-only "origin/$RELEASE_VERSION_BRANCH" "origin/$RELEASE_BRANCH"
            console_output 1 gray "$last_cmd_stdout"

            if [ -z "$ALLOW_MODIFY" ]; then
                echo "Changes detected but refusing to merge without --allow-modify option"
                exit 1
            fi

            github_create_verified_merge --from "$RELEASE_BRANCH" --to "$RELEASE_VERSION_BRANCH"
        fi
    fi

    execute_command --no-std -- git_fetch_unshallow origin "$RELEASE_VERSION_BRANCH"
    execute_command --no-std -- git checkout "${RELEASE_VERSION_BRANCH}"
    echo "Successfully checked out to $RELEASE_VERSION_BRANCH"

    exit 0
fi

echo "Branch $RELEASE_VERSION_BRANCH does not exist in origin"
if [ -z "$ALLOW_MODIFY" ]; then
    echo "Refuse to modify repository without --allow-modify option"
    exit 1
fi

execute_command --no-std -- git checkout "$RELEASE_BRANCH"
# At this point, we should be on RELEASE_BRANCH
echo "Current branch: $(git branch --show-current)"

# Create RELEASE_VERSION_BRANCH based on RELEASE_BRANCH and push to origin
execute_command --no-std -- git checkout -b "$RELEASE_VERSION_BRANCH"
execute_command --no-std -- git push origin HEAD:"$RELEASE_VERSION_BRANCH"
echo "Created and pushed $RELEASE_VERSION_BRANCH based on $RELEASE_BRANCH"

echo "Successfully set up $RELEASE_VERSION_BRANCH - working directory now points to this branch"
