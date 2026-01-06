#!/bin/bash
# Send a Slack message using Bot Token API
# Reads JSON message from stdin, sends to Slack API
# Usage: slack_send_with_token "$SLACK_BOT_TOKEN" < message.json
slack_send_with_token() {
  local token="$1"
  local curl_stderr=$(mktemp)

  # Run curl with verbose output, stderr to curl_stderr
  if ! curl --fail-with-body -v -X POST https://api.slack.com/api/chat.postMessage \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d @- 2>"$curl_stderr"; then
    # If curl failed, output the error log
    echo "curl command failed. Error log:" >&2
    cat "$curl_stderr" >&2
    rm -f "$curl_stderr"
    return 1
  fi

  rm -f "$curl_stderr"
  return 0
}

# Handle Slack API response and extract message metadata
# Reads JSON response from stdin
# If GITHUB_OUTPUT is set, writes slack_ts and slack_url to it
# Usage: slack_handle_message_result "$SLACK_CHANNEL_ID" < response.json
slack_handle_message_result() {
  local message="$2"
  local response=$(cat)

  echo "Slack API Response:"

  # Check if successful
  if echo "$response" | jq -e '.ok == true' > /dev/null; then
    local slack_ts=$(echo "$response" | jq -r '.ts')
    local slack_channel=$(echo "$response" | jq -r '.channel')

    # Convert timestamp to URL format (remove dot)
    local ts_for_url=$(echo "$slack_ts" | tr -d '.')
    local slack_url="https://redis.slack.com/archives/${slack_channel}/p${ts_for_url}"

    # Write to GITHUB_OUTPUT if available
    if [ -n "$GITHUB_OUTPUT" ]; then
      echo "slack_ts=$slack_ts" >> "$GITHUB_OUTPUT"
      echo "slack_url=$slack_url" >> "$GITHUB_OUTPUT"
    fi

    echo "✅ Message sent successfully!"
    echo "Message URL: $slack_url"
    return 0
  else
    local error=$(echo "$response" | jq -r '.error // "unknown"')
    echo "❌ Failed to send Slack message: $error" >&2
    echo "$response" | jq '.'
    echo "Message content: $message" >&2
    return 1
  fi
}