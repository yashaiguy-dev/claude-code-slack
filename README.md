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
- **Smart threads** — @mention once to start, then just reply — no @mention needed after that
- **Tool mirroring** — see every file read, command run, grep search in Slack
- **File upload/download** — attach files in Slack, Claude reads them. Generated files auto-upload back
- **Hot-reload config** — change model/effort/timeout in .env without restarting
- **Message queue** — multiple messages in a thread wait in line (no collisions)
- **Status check** — type `status` in any thread to see what Claude is doing
- **Manual file share** — type `/share <path>` to upload any file to the thread
- **Proactive messaging** — send messages via CLI: `python3 bot.py --send USER_ID "message"`
- **Self-healing** — auto-repairs broken claude binary after updates
- **Audit logging** — every interaction logged with user, timing, and session info
- **Model selection** — use Opus, Sonnet, or Haiku via config

---

## Setup (Step by Step)

Follow these steps in order. Takes about 10 minutes.

### Step 1: Install Node.js

Claude Code CLI needs Node.js v18+.

**Mac:**
```bash
brew install node
```

**Windows:**
Download and install Node.js v22 LTS from https://nodejs.org

Verify: `node --version` should print v18 or higher.

### Step 2: Install Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
```

Then authenticate (pick one):

```bash
# Option A — Claude subscription (Pro/Team/Enterprise):
claude auth login

# Option B — API key (pay-per-use):
export ANTHROPIC_API_KEY=sk-ant-your-key    # Mac/Linux
set ANTHROPIC_API_KEY=sk-ant-your-key       # Windows CMD
$env:ANTHROPIC_API_KEY="sk-ant-your-key"    # Windows PowerShell
```

Verify: `claude --version` should print a version number.

### Step 3: Install Python

You need Python 3.9+.

**Mac:** Python 3 comes pre-installed. Verify with `python3 --version`.

**Windows:** Download from https://python.org/downloads — check "Add to PATH" during install. Verify with `python --version`.

### Step 4: Clone this repo

```bash
git clone https://github.com/yashaiguy-dev/claude-code-slack.git
cd claude-code-slack
```

### Step 5: Install Python dependencies

```bash
pip3 install -r requirements.txt    # Mac
pip install -r requirements.txt     # Windows
```

### Step 6: Create the Slack app (1 minute)

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** → **From an app manifest**
3. Select your Slack workspace
4. Switch to the **JSON** tab
5. Paste the contents of `slack-app-manifest.json` from this repo
6. Click **Next** → **Create**

All scopes, events, and Socket Mode are pre-configured by the manifest. No manual setup needed.

### Step 7: Get your tokens (2 tokens needed)

**Token 1 — Bot Token:**
1. In your Slack app settings, go to **OAuth & Permissions** (left sidebar)
2. Click **Install to Workspace** → **Allow**
3. Copy the **Bot User OAuth Token** — starts with `xoxb-`

**Token 2 — App Token:**
1. Go to **Settings → Basic Information** (left sidebar)
2. Scroll down to **App-Level Tokens**
3. Click **Generate Token and Scopes**
4. Name it anything (e.g. `socket-token`)
5. Add the scope `connections:write`
6. Click **Generate** → copy the token — starts with `xapp-`

### Step 8: Configure the bot

```bash
cp .env.example .env
```

Open `.env` in any text editor. Replace the placeholder values:

```
SLACK_BOT_TOKEN=xoxb-paste-your-bot-token-here
SLACK_APP_TOKEN=xapp-paste-your-app-token-here
PROJECT_DIR=~/my-projects
```

`PROJECT_DIR` is the folder Claude will work in — it can read/write files there.

### Step 9: Invite the bot to a channel

In Slack, go to any channel and type:
```
/invite @Claude Code
```

### Step 10: Run the bot

```bash
python3 bot.py    # Mac
python bot.py     # Windows
```

You should see:
```
============================================================
  Claude Code Slack Bot
============================================================
  Project dir:  /Users/you/my-projects
  Mode:         Socket Mode
  ...
  Bot is running! Send a message in Slack.
```

### Step 11: Test it

1. **DM test:** Find "Claude Code" in Direct Messages → type "hello"
2. **Channel test:** In the channel you invited it to, type `@Claude Code what time is it?`
3. **Thread test:** Reply to the bot's response in the thread — just type normally, no @mention needed

If the bot responds, you're done!

---

## Usage

| Action | How |
|---|---|
| **DM the bot** | Find it in Direct Messages, just type |
| **Start in a channel** | `@Claude Code your question` — starts a thread |
| **Continue in thread** | Just reply — no @mention needed after the first one |
| **Fresh start** | Start a new thread (or DM) |
| **Check progress** | Type `status` in the thread |
| **Share a file** | Type `/share /path/to/file.ext` |
| **Attach a file** | Drag-and-drop a file into the message |
| **Send proactive DM** | `python3 bot.py --send USER_ID "message"` |
| **Post to channel** | `python3 bot.py --channel "#general" "message"` |

## What it looks like

When `MIRROR_TOOLS_TO_SLACK=true` (default), you see Claude's thought process:

```
You:     Fix the bug in auth.py
Claude:  🔧 Read — /app/auth.py
Claude:  🔧 Grep — "login" in /app/
Claude:  🔧 Edit — /app/auth.py  [old_token = ...]
Claude:  Fixed the authentication bug. The issue was...
```

---

## Configuration

All settings go in `.env`. The `CLAUDE_*` settings are **hot-reloadable** — edit while the bot is running, changes take effect on the next message.

| Setting | Default | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | required | Bot token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | required | Socket Mode token (`xapp-...`) |
| `PROJECT_DIR` | `~/claude-workspace` | Directory Claude operates in |
| `CLAUDE_TIMEOUT` | `1800` | Max seconds per request (30 min) |
| `CLAUDE_MODEL` | (default) | Model: `sonnet`, `opus`, `haiku`, or full ID |
| `CLAUDE_EFFORT` | `high` | Effort: `low`, `medium`, `high` |
| `CLAUDE_PERMISSION_MODE` | `bypassPermissions` | Permission mode for tool calls |
| `MIRROR_TOOLS_TO_SLACK` | `true` | Show tool calls in Slack |
| `SESSION_RECENT_WINDOW` | `600` | Seconds to reuse session in same channel |
| `AUTHORIZED_USERS` | (all) | Comma-separated Slack user IDs to restrict access |

---

## Run in Background (auto-start on boot)

You probably don't want to keep a terminal open. Here's how to run the bot automatically.

### Mac — launchd

```bash
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
```

Replace `/path/to/claude-code-slack` with your actual path, then:

```bash
launchctl load ~/Library/LaunchAgents/com.claude.slack.plist
```

The bot now starts automatically on login and restarts if it crashes.

### Windows — Task Scheduler

1. Open **Task Scheduler** → Create Basic Task
2. Name: `Claude Slack Bot`
3. Trigger: **When I log on**
4. Action: **Start a program**
   - Program: `python`
   - Arguments: `bot.py`
   - Start in: `C:\path\to\claude-code-slack`
5. Finish → right-click the task → **Properties**:
   - Check "Run whether user is logged on or not"
   - Check "Run with highest privileges"
   - Conditions → uncheck "Start only if on AC power"

### Windows — NSSM (runs as a service, no terminal window)

```powershell
# Download NSSM from https://nssm.cc/download
nssm install ClaudeSlackBot python bot.py
nssm set ClaudeSlackBot AppDirectory C:\path\to\claude-code-slack
nssm set ClaudeSlackBot AppStdout C:\path\to\claude-code-slack\bot.stdout.log
nssm set ClaudeSlackBot AppStderr C:\path\to\claude-code-slack\bot.stderr.log
nssm start ClaudeSlackBot
```

The service runs silently in the background and auto-restarts if it crashes.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `claude: command not found` | `npm install -g @anthropic-ai/claude-code` then restart terminal |
| `node: command not found` | Install Node.js (Step 1) |
| Not authenticated | Run `claude auth login` in terminal |
| Bot doesn't respond to DMs | Go to your Slack app → App Home → enable **Messages Tab** |
| Bot doesn't respond in channels | Type `/invite @Claude Code` in the channel first |
| Thread replies not working | Make sure the first message in the thread @mentioned the bot |
| Timeout on long tasks | Increase `CLAUDE_TIMEOUT=3600` in `.env` |
| Port already in use | This only applies to Events API mode — Socket Mode doesn't use a port |
| Tool calls not showing | Set `MIRROR_TOOLS_TO_SLACK=true` in `.env` |
| Duplicate responses | Bot handles retries — check `X-Slack-Retry-Num` in logs |
| Broken CLI after update | Bot self-repairs automatically, or run `npm install -g @anthropic-ai/claude-code` |

---

<details>
<summary>Advanced: Events API mode (Cloudflare tunnel — no @mention needed at all)</summary>

If you want the bot to respond to every message in its channels without any @mention (not even the first one), you can use Events API mode instead of Socket Mode. This requires a Cloudflare tunnel to give Slack a public URL to send events to.

### Setup

1. Install Cloudflare Tunnel:
   - Mac: `brew install cloudflare/cloudflare/cloudflared`
   - Windows: download from https://github.com/cloudflare/cloudflared/releases/latest

2. Create a tunnel:
   ```bash
   # Quick tunnel (free, temporary URL — good for testing):
   cloudflared tunnel --url http://localhost:3456

   # Named tunnel (permanent domain — for production):
   cloudflared tunnel login
   cloudflared tunnel create claude-bot
   cloudflared tunnel run claude-bot
   ```

3. In your Slack app settings:
   - **Event Subscriptions** → Request URL: `https://your-tunnel-domain/slack/events`
   - Subscribe to: `app_mention`, `message.im`, `message.channels`
   - **Basic Information** → copy the **Signing Secret**

4. Update `.env`:
   ```
   SLACK_BOT_TOKEN=xoxb-your-token
   SLACK_SIGNING_SECRET=your-signing-secret
   # Remove or comment out SLACK_APP_TOKEN — it's not needed for Events API
   ```

5. For a permanent tunnel, create `~/.cloudflared/config.yml`:
   ```yaml
   tunnel: YOUR-TUNNEL-ID
   credentials-file: /path/to/.cloudflared/YOUR-TUNNEL-ID.json

   ingress:
     - hostname: claude-bot.yourdomain.com
       service: http://localhost:3456
     - service: http_status:404
   ```

### When to use Events API vs Socket Mode

| | Socket Mode | Events API |
|---|---|---|
| Setup | Easy (no domain needed) | Harder (needs Cloudflare tunnel) |
| @mention | Once to start thread, then just reply | Never needed |
| Public URL | Not needed | Required |
| Best for | Most users | Always-listening in all channels |

</details>

---

## License

MIT
