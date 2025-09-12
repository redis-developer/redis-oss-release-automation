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
                exit 1
            fi
            shift
            ;;
    esac
done

if [ -z "$TAG_NAME" ]; then
    echo "Error: Tag name is required"
    exit 1
fi

if [ -z "$REF_SHA" ]; then
    echo "Error: Missing required argument --ref"
    exit 1
fi

echo "Creating verified tag '$TAG_NAME' at commit '$REF_SHA'..."

execute_command github_create_verified_tag "$TAG_NAME" --ref "$REF_SHA" ${TAG_MESSAGE:+--message "$TAG_MESSAGE"}
console_output 1 gray "$last_cmd_stdout"