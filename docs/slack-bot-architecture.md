# Slack Bot Architecture

## Three Independent Threads

The system consists of three separate threads that communicate via queues and shared state:

1. **Slack Bot Thread** (async) - Listens to Slack events via WebSocket
2. **Conversation Tree Thread** (sync) - LLM-powered command classifier and router
3. **Release Tree Thread** (async) - Orchestrates the actual release process

## Release Process Flow

The following diagram shows the complete flow for a **release command** (e.g., `@bot release 8.4-m01`):

```mermaid
sequenceDiagram
    participant User as User in Slack
    participant Slack as Slack Platform
    participant Bot as Thread 1:<br/>Slack Bot<br/>(async)
    participant Conv as Thread 2:<br/>Conversation Tree<br/>(sync, LLM)
    participant Rel as Thread 3:<br/>Release Tree<br/>(async)
    participant GH as GitHub API
    participant Workflow as GitHub Workflow<br/>(in package repo)
    participant S3 as S3 State

    Note over Bot: Bot starts and connects
    Bot->>Slack: WebSocket connect (Socket Mode)
    Slack-->>Bot: Connected

    Note over User,Slack: User mentions bot
    User->>Slack: @bot release 8.4-m01
    Slack->>Bot: Event via WebSocket

    Note over Bot,Conv: Bot spawns conversation thread
    Bot->>Slack: GET thread messages (context)
    Slack-->>Bot: Thread history
    Bot->>Conv: Start thread with:<br/>- message<br/>- context<br/>- janus.Queue

    Note over Conv: Conversation tree processes
    Conv->>Conv: LLM classifies command<br/>(RELEASE/STATUS/HELP)
    Conv->>Conv: Extract release_tag
    Conv->>Conv: Check if confirmation needed

    alt Needs Confirmation
        Conv->>Bot: Put "Confirm release?" in queue
        Bot->>Slack: POST confirmation message
        Note over Conv: Thread exits, waits for reply
        User->>Slack: "yes"
        Slack->>Bot: New message event
        Bot->>Conv: Start NEW conversation thread<br/>with updated context
        Conv->>Conv: LLM detects confirmation
    end

    Note over Conv,Rel: Conversation starts release
    Conv->>Conv: Build ReleaseArgs from LLM output
    Conv->>Rel: Start release thread with args
    Conv->>Bot: Put "Starting release..." in queue
    Bot->>Slack: POST message
    Note over Conv: Thread exits (no link to release)

    Note over Rel: Release tree runs independently
    Rel->>S3: Load/create state
    Rel->>Rel: Initialize SlackStatePrinter<br/>(post_tick_handler)

    loop For each package
        Rel->>GH: Trigger workflow<br/>(with slack_channel_id, slack_thread_ts)
        Rel->>Slack: Update state (tick handler)
        Note over Workflow: Workflow starts in package repo
        Workflow->>Slack: POST progress messages<br/>(using SLACK_BOT_TOKEN secret)
        Rel->>GH: Poll workflow status
        Rel->>S3: Sync state
        Rel->>Slack: Update state (tick handler)
        Workflow->>Slack: POST completion message
        Rel->>GH: Download artifacts
        Rel->>GH: Trigger workflow<br/>(publish_workflow)
        Rel->>Slack: Update state (tick handler)
        Workflow->>Slack: POST publish messages
    end

    Note over Rel: Release completes
    Rel->>S3: Final state sync
    Rel->>Slack: Final state update (tick handler)
    Note over Rel: Thread exits
```

## Thread Responsibilities

### Thread 1: Slack Bot (async)
**File:** `src/redis_release/slack_bot.py`

**Responsibilities:**
- Maintain WebSocket connection to Slack (Socket Mode)
- Listen for `app_mention` and `message` events
- Fetch thread context (conversation history)
- Spawn new conversation threads for each user message
- Listen to janus.Queue and forward messages back to Slack
- Run independently, can handle multiple conversations simultaneously

**Key Point:** Bot does NOT process commands or run releases - it only routes messages!

### Thread 2: Conversation Tree (sync, LLM-powered)
**File:** `src/redis_release/bht/conversation_tree.py`

**Responsibilities:**
- Use OpenAI LLM to classify user intent (RELEASE/STATUS/HELP)
- Extract command arguments from natural language
- Determine if user confirmation is needed
- Show confirmation message if needed
- Build `ReleaseArgs` object from LLM output
- Spawn release thread when ready
- Exit after starting release (no ongoing link)

**Key Point:** Each user message creates a NEW conversation thread. LLM is REQUIRED (manual mode not fully implemented). Multiple conversations can run simultaneously.

### Thread 3: Release Tree (async)
**File:** `src/redis_release/bht/tree.py`

**Responsibilities:**
- Load/sync state from S3
- Orchestrate package builds and publishes
- Trigger GitHub workflows (build → publish)
- Monitor workflow status via polling
- Download artifacts between build and publish
- Post state updates to Slack on EVERY tick via `SlackStatePrinter`
- Sync state to S3 on every tick
- Run until all packages complete or error


## Communication Between Threads

### Bot ↔ Conversation
- **Link:** `janus.Queue` (bidirectional sync/async queue)
- **Direction:** Bot → Conversation (message + context), Conversation → Bot (replies)
- **Lifetime:** Queue exists only during conversation thread execution

### Conversation → Release
- **Link:** `ReleaseArgs` object passed to new thread
- **Direction:** One-way handoff
- **Lifetime:** No ongoing connection after release thread starts

### Release → Slack
- **Link:** Direct API calls via `SlackStatePrinter`
- **Direction:** Release → Slack (state updates)
- **Lifetime:** Independent, uses same channel_id/thread_ts from ReleaseArgs

## Credentials Required

### On EC2 (Running the Bot)

Required environment variables:
1. **`SLACK_BOT_TOKEN`** (xoxb-...) - For posting messages to Slack
2. **`SLACK_APP_TOKEN`** (xapp-...) - For WebSocket connection (Socket Mode)
3. **`OPENAI_API_KEY`** - For LLM-based command detection (REQUIRED)
4. **`GITHUB_TOKEN`** - For triggering workflows in package repos
5. **AWS credentials** - For S3 state storage (via IAM role or env vars)

### In GitHub Workflows (Package Repos)

Required secrets in each package repository:
1. **`SLACK_BOT_TOKEN`** - Same token as EC2 bot (stored as GitHub secret)
2. **`GITHUB_TOKEN`** - Automatically provided by GitHub Actions

## Slack Message Sources During Release

There are **three independent sources** of Slack messages during a release:

### 1. Conversation Tree Messages (via Bot Queue)
- **Source:** Conversation tree behaviors
- **Mechanism:** Messages added to `janus.Queue` → Bot reads and posts
- **Examples:** "Starting release...", "Confirm release?", error messages
- **Code:** `conversation_behaviours.py` → `slack_bot.py:create_queue_listener`

### 2. Release Tree Status Updates (Direct API)
- **Source:** `SlackStatePrinter` post-tick handler
- **Mechanism:** Direct Slack API calls via `WebClient`
- **Examples:** Live-updating status message with package progress (✅ ⏳ ❌)
- **Code:** `state_slack.py:SlackStatePrinter.update_message()`
- **Frequency:** Every tick (but only updates if state changed)

### 3. GitHub Workflow Messages (Direct API)
- **Source:** GitHub Actions workflows in package repos
- **Mechanism:** Workflows call `slack_send_with_token` bash function
- **Examples:** "Build started", "Tests passed", "Published to registry"
- **Code:** `.github/actions/common/slack.sh` (sourced by workflows)
- **Credentials:** Uses `SLACK_BOT_TOKEN` secret stored in package repo
- **Thread:** Uses `slack_channel_id` and `slack_thread_ts` passed as workflow inputs

**Key Point:** All three sources post to the **same Slack thread** using the same `SLACK_BOT_TOKEN`, but they operate completely independently!

