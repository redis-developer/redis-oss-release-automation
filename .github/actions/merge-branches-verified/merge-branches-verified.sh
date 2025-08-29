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
            echo "Error: Unknown argument $1"
            exit 1
            ;;
    esac
done

if [ -z "$FROM_BRANCH" ] || [ -z "$TO_BRANCH" ]; then
    echo "Error: Missing required arguments --from and --to"
    exit 1
fi

execute_command github_create_verified_merge --from "$FROM_BRANCH" --to "$TO_BRANCH"