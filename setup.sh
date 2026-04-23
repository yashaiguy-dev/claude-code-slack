#!/bin/bash
# Claude Code Slack Bot — one-command setup
set -e

echo ""
echo "======================================================="
echo "  Claude Code Slack Bot — Setup"
echo "======================================================="
echo ""

# Check Node.js
if ! command -v node &>/dev/null; then
    echo "❌ Node.js not found."
    echo "   Install: brew install node (Mac) or https://nodejs.org (Windows)"
    exit 1
fi
echo "✅ Node.js $(node -v)"

# Check/install Claude Code CLI
if ! command -v claude &>/dev/null; then
    echo "📦 Installing Claude Code CLI..."
    npm install -g @anthropic-ai/claude-code
else
    echo "✅ Claude Code CLI installed"
fi

# Check Claude auth
echo ""
echo "🔑 Checking Claude authentication..."
if claude auth status 2>/dev/null | grep -qi "authenticated\|logged in\|valid"; then
    echo "✅ Claude is authenticated"
else
    echo ""
    echo "⚠️  Claude needs authentication. Pick one:"
    echo ""
    echo "   Option 1 — Claude subscription (interactive login):"
    echo "     claude auth login"
    echo ""
    echo "   Option 2 — API key (pay-per-use):"
    echo "     export ANTHROPIC_API_KEY=sk-ant-..."
    echo ""
    echo "   Run one of these, then re-run this setup script."
    echo ""
fi

# Check Python
if command -v python3 &>/dev/null; then
    PY=python3
    PIP=pip3
elif command -v python &>/dev/null; then
    PY=python
    PIP=pip
else
    echo "❌ Python not found."
    echo "   Install: brew install python (Mac) or https://python.org (Windows)"
    exit 1
fi
echo "✅ Python $($PY --version 2>&1 | awk '{print $2}')"

# Install Python deps
echo ""
echo "📦 Installing Python dependencies..."
$PIP install -r requirements.txt -q
echo "✅ Dependencies installed"

# Create .env if needed
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "📄 Created .env from template"
fi

# Guide through Slack setup
echo ""
echo "======================================================="
echo "  Now set up your Slack app"
echo "======================================================="
echo ""
echo "  1. Go to: https://api.slack.com/apps"
echo "  2. Click 'Create New App' → 'From scratch'"
echo "  3. Give it a name (e.g. 'Claude Code') and pick your workspace"
echo ""
echo "  4. OAuth & Permissions → add these Bot Token Scopes:"
echo "     • app_mentions:read"
echo "     • chat:write"
echo "     • files:write"
echo "     • im:history"
echo "     • im:read"
echo "     • im:write"
echo "     • reactions:read"
echo "     • reactions:write"
echo "     • users:read"
echo ""
echo "  5. Settings → Socket Mode → toggle ON"
echo "     → Generate token → copy the xapp-... token"
echo ""
echo "  6. Event Subscriptions → toggle ON"
echo "     → Subscribe to: app_mention, message.im"
echo ""
echo "  7. App Home → enable 'Messages Tab'"
echo ""
echo "  8. Install App → copy the xoxb-... token"
echo ""
echo "  9. Paste both tokens into .env:"
echo "     SLACK_BOT_TOKEN=xoxb-your-token"
echo "     SLACK_APP_TOKEN=xapp-your-token"
echo ""
echo "======================================================="
echo "  Then run:  $PY bot.py"
echo "======================================================="
echo ""
