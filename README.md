# Claude Code Slack Bot

**Agentic AI coding assistant in Slack** — powered by Anthropic's Claude Code CLI.

Claude can read files, write code, run shell commands, search the web, and do multi-step coding tasks — all from Slack. Every tool call shows up in real-time so you can watch Claude work.

```
You type in Slack
  ↓
Python bot running on YOUR computer
  ↓
claude -p "your message" --resume <session>
  ↓
Claude AI (reads files, runs commands, writes code)
  ↓
Reply + tool calls + generated files appear in your Slack thread
```

## Features

- **Agentic** — reads/writes files, runs commands, multi-step reasoning
- **Thread memory** — each Slack thread = its own Claude session with full context
- **Tool mirroring** — see every file read, command run, grep search in Slack
- **File upload/download** — attach files in Slack, Claude reads them. Generated files auto-upload back
- **Two modes** — Socket Mode (easy, no server) or Events API (Cloudflare, no @mention)
- **Hot-reload config** — change model/effort/timeout in .env without restarting
- **Message queue** — multiple messages in a thread wait in line (no collisions)
- **Status check** — type `status` in any thread to see what Claude is doing
- **Manual file share** — type `/share <path>` to upload any file to the thread
- **Proactive messaging** — send messages via CLI: `python3 bot.py --send USER_ID "message"`
- **Self-healing** — auto-repairs broken claude binary after updates
- **Event dedup** — handles Slack retries gracefully
- **Audit logging** — every interaction logged with user, timing, and session info
- **Model selection** — use Opus, Sonnet, or Haiku via config

## Quick Start

### 1. Install prerequisites

```bash
# Node.js (needed for Claude Code CLI)
brew install node                        # Mac
# Windows: download v22 LTS from https://nodejs.org

# Claude Code CLI
npm install -g @anthropic-ai/claude-code

# Authenticate (pick one):
claude auth login                        # Interactive login (Claude subscription)
# OR set ANTHROPIC_API_KEY in your shell  # API key (pay-per-use)

# Python dependencies
pip3 install -r requirements.txt         # Mac
pip install -r requirements.txt          # Windows
```

### 2. Create a Slack app

Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**

#### Bot Token Scopes (OAuth & Permissions)

| Scope | Purpose |
|---|---|
| `app_mentions:read` | See @mentions |
| `chat:write` | Send messages |
| `channels:history` | Read channel messages (Events API mode) |
| `channels:read` | See channel list (Events API mode) |
| `files:read` | Download attached files |
| `files:write` | Upload generated files |
| `im:history` | Read DM history |
| `im:read` | See DM channels |
| `im:write` | Send DMs |
| `reactions:read` | See reactions |
| `reactions:write` | Add thinking indicator |
| `users:read` | Get display names |

> For Socket Mode only, you can skip `channels:history` and `channels:read`.

#### Option A: Socket Mode (easier — no public URL)

1. **Settings → Socket Mode** → toggle ON → generate token → copy `xapp-...` token
2. **Event Subscriptions** → toggle ON → subscribe to: `app_mention`, `message.im`
3. **App Home** → enable Messages Tab
4. **Install App** → copy `xoxb-...` token

#### Option B: Events API + Cloudflare (no @mention needed)

1. Install Cloudflare Tunnel:
   - Mac: `brew install cloudflare/cloudflare/cloudflared`
   - Windows: [download MSI](https://github.com/cloudflare/cloudflared/releases/latest)
2. **Quick tunnel** (free, temporary URL):
   ```bash
   cloudflared tunnel --url http://localhost:3456
   ```
3. **Named tunnel** (permanent domain — recommended):
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create claude-bot
   # Add DNS record for your subdomain in Cloudflare dashboard
   # Create ~/.cloudflared/config.yml (see below)
   cloudflared tunnel run claude-bot
   ```
4. **Event Subscriptions** → Request URL: `https://your-domain.com/slack/events`
5. Subscribe to: `app_mention`, `message.im`, `message.channels`
6. **Basic Information** → copy Signing Secret
7. **Install App** → copy `xoxb-...` token

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` with your tokens:

**Socket Mode:**
```
SLACK_BOT_TOKEN=xoxb-your-token
SLACK_APP_TOKEN=xapp-your-token
PROJECT_DIR=~/my-projects
```

**Events API:**
```
SLACK_BOT_TOKEN=xoxb-your-token
SLACK_SIGNING_SECRET=your-signing-secret
PROJECT_DIR=~/my-projects
```

### 4. Run

```bash
python3 bot.py    # Mac
python bot.py     # Windows
```

## Usage

| Action | How |
|---|---|
| **DM the bot** | Find it in Direct Messages, just type |
| **@mention in channel** | `/invite @ClaudeCode` then `@ClaudeCode your question` |
| **Continue conversation** | Reply in the same thread — Claude remembers |
| **Fresh start** | Start a new thread |
| **Check progress** | Type `status` in the thread |
| **Share a file** | Type `/share /path/to/file.ext` |
| **Attach a file** | Drag-and-drop a file into the message |
| **Send proactive DM** | `python3 bot.py --send USER_ID "message"` |
| **Post to channel** | `python3 bot.py --channel "#general" "message"` |

## What it looks like

When `MIRROR_TOOLS_TO_SLACK=true`, you see Claude's thought process:

```
You:     Fix the bug in auth.py
Claude:  🔧 Read — /app/auth.py
Claude:  🔧 Grep — "login" in /app/
Claude:  🔧 Edit — /app/auth.py  [old_token = ...]
Claude:  Fixed the authentication bug. The issue was...
```

## Configuration

| Setting | Default | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | required | Bot token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | — | Socket Mode token (`xapp-...`) |
| `SLACK_SIGNING_SECRET` | — | Events API signing secret |
| `PROJECT_DIR` | `~/claude-workspace` | Directory Claude operates in |
| `CLAUDE_TIMEOUT` | `1800` | Max seconds per request (30 min) |
| `CLAUDE_MODEL` | (default) | Model: `sonnet`, `opus`, `haiku`, or full ID |
| `CLAUDE_EFFORT` | `high` | Effort: `low`, `medium`, `high` |
| `CLAUDE_PERMISSION_MODE` | `bypassPermissions` | Permission mode for tool calls |
| `MIRROR_TOOLS_TO_SLACK` | `true` | Show tool calls in Slack |
| `SESSION_RECENT_WINDOW` | `600` | Seconds to reuse session in same channel |
| `AUTHORIZED_USERS` | (all) | Comma-separated Slack user IDs |
| `PORT` | `3456` | Web server port (Events API only) |

All `CLAUDE_*` settings are **hot-reloadable** — edit `.env` while the bot is running, changes take effect on the next message.

## Auto-Start on Boot (Mac)

Create a launchd plist to run the bot automatically:

```bash
# Create the plist
cat > ~/Library/LaunchAgents/com.claude.slack.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude.slack</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>-u</string>
        <string>/path/to/claude-code-slack/bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/claude-code-slack</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
    <key>StandardOutPath</key>
    <string>/path/to/claude-code-slack/bot.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/claude-code-slack/bot.stderr.log</string>
</dict>
</plist>
EOF

# Load it
launchctl load ~/Library/LaunchAgents/com.claude.slack.plist
```

## Cloudflare Named Tunnel Config

For a permanent domain, create `~/.cloudflared/config.yml`:

```yaml
tunnel: YOUR-TUNNEL-ID
credentials-file: /path/to/.cloudflared/YOUR-TUNNEL-ID.json

ingress:
  - hostname: claude-bot.yourdomain.com
    service: http://localhost:3456
  - service: http_status:404
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `claude: command not found` | `npm install -g @anthropic-ai/claude-code` then restart terminal |
| Not authenticated | Run `claude auth login` in terminal |
| Bot doesn't respond to DMs | App Home → enable Messages Tab |
| Bot doesn't respond in channels | `/invite @ClaudeCode` first |
| Timeout on long tasks | Increase `CLAUDE_TIMEOUT=3600` in `.env` |
| Port already in use | Change `PORT=3457` in `.env` |
| Tool calls not showing | Set `MIRROR_TOOLS_TO_SLACK=true` |
| Duplicate responses | Bot handles retries — check `X-Slack-Retry-Num` in logs |
| Broken CLI after update | Bot self-repairs automatically, or run `npm install -g @anthropic-ai/claude-code` |

## License

MIT
