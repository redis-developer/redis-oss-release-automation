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
        echo "Error: Missing required arguments --from and --to"
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
        201) echo "✅ Verified merge created: $(jq -r '.sha' /tmp/merge.json)";;
        204) echo "✔️  Already up to date (no merge necessary)";;
        409) echo "❌ Merge conflict; open a PR to resolve"; cat /tmp/merge.json; return 1;;
        *)   echo "❌ Unexpected status $HTTP_CODE"; cat /tmp/merge.json; return 1;;
    esac
}