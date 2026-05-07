#!/usr/bin/env python3
"""
Claude Code Slack Bot — agentic AI coding assistant in Slack.

Runs Claude Code CLI headless via Slack. Each thread = its own Claude session
with full conversation memory, tool use, and file access.

Supports TWO modes (user picks one):
  A) Socket Mode   — no public URL, @mention once to start a thread, then just reply
  B) Events API    — needs Cloudflare tunnel, reads all messages (no @mention at all)

Features:
  - Per-thread serial queue (messages wait for prior run to finish)
  - Tool call mirroring to Slack (shows what Claude is doing in real-time)
  - File upload/download (Slack attachments in, generated files out)
  - Session continuity (each thread resumes its Claude session)
  - Recent session window (reuses session within N seconds in same channel)
  - Hot-reload config (edit .env without restarting)
  - Self-healing CLI (auto-repairs broken claude binary)
  - Status check: type "status" to see what's running
  - Manual file share: type "/share <path>" to upload any file
  - Proactive messaging CLI: --send, --send-result, --channel
  - Event deduplication + Slack retry handling
  - Audit logging

Setup:
  1. Install Claude Code:  npm install -g @anthropic-ai/claude-code
  2. Login once:           claude auth login  (or set ANTHROPIC_API_KEY)
  3. Create Slack app (see README.md for Socket Mode or Events API)
  4. Copy .env.example → .env, fill in tokens
  5. pip3 install -r requirements.txt
  6. python3 bot.py

CLI usage:
  python3 bot.py --send USER_ID "message"
  python3 bot.py --channel "#general" "message"
  echo '{"result":"..."}' | python3 bot.py --send-result USER_ID
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request as flask_request, jsonify
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_sdk import WebClient

# ---------------------------------------------------------------------------
# Logging (file) + live terminal stream
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("claude-bot")

_rotating_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "bot.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_rotating_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
logger.addHandler(_rotating_handler)

AUDIT_LOG = LOG_DIR / "audit.log"
audit_handler = logging.FileHandler(AUDIT_LOG, encoding="utf-8")
audit_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
audit_logger = logging.getLogger("claude-bot.audit")
audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)


def term(tag: str, text: str, color: str = "") -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    reset = "\033[0m" if color else ""
    line = f"{color}[{stamp}] {tag:<7}{reset} {text}"
    print(line, flush=True)


C_MSG = "\033[36m"
C_TEXT = "\033[32m"
C_TOOL = "\033[33m"
C_RESULT = "\033[90m"
C_DONE = "\033[35m"
C_ERR = "\033[31m"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
PORT = int(os.environ.get("PORT", "3456"))

AUTHORIZED_USERS = set(
    u.strip() for u in os.environ.get("AUTHORIZED_USERS", "").split(",") if u.strip()
)

PROJECT_DIR = os.environ.get("PROJECT_DIR", "")
if not PROJECT_DIR:
    PROJECT_DIR = os.path.expanduser("~/claude-workspace")
    Path(PROJECT_DIR).mkdir(parents=True, exist_ok=True)

CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "1800"))
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "")
CLAUDE_EFFORT = os.environ.get("CLAUDE_EFFORT", "high")
CLAUDE_PERMISSION_MODE = os.environ.get("CLAUDE_PERMISSION_MODE", "bypassPermissions")

MIRROR_TOOLS_TO_SLACK = os.environ.get("MIRROR_TOOLS_TO_SLACK", "true").lower() in ("1", "true", "yes")
SESSION_RECENT_WINDOW = int(os.environ.get("SESSION_RECENT_WINDOW", "600"))
MAX_SLACK_MSG_LEN = 3900

# Detect which mode to run:
# - SLACK_SIGNING_SECRET set → Events API (Cloudflare tunnel, no @mention needed)
# - SLACK_APP_TOKEN set → Socket Mode (no public URL, requires @mention)
USE_EVENTS_API = bool(SLACK_SIGNING_SECRET)


def _reload_claude_config() -> None:
    """Re-read Claude settings from .env so live edits take effect without restart."""
    global CLAUDE_TIMEOUT, CLAUDE_MODEL, CLAUDE_EFFORT, CLAUDE_PERMISSION_MODE
    load_dotenv(override=True)
    try:
        CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "1800"))
    except ValueError:
        pass
    CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", CLAUDE_MODEL)
    CLAUDE_EFFORT = os.environ.get("CLAUDE_EFFORT", CLAUDE_EFFORT)
    CLAUDE_PERMISSION_MODE = os.environ.get("CLAUDE_PERMISSION_MODE", CLAUDE_PERMISSION_MODE)


# ---------------------------------------------------------------------------
# Slack app
# ---------------------------------------------------------------------------

if USE_EVENTS_API:
    app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET) if SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET else None
else:
    app = App(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

slack_client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

_user_name_cache: dict[str, str] = {}


def _get_user_name(user_id: str) -> str:
    if user_id in _user_name_cache:
        return _user_name_cache[user_id]
    try:
        info = slack_client.users_info(user=user_id)
        profile = info["user"].get("profile", {})
        name = (
            profile.get("display_name")
            or profile.get("real_name")
            or info["user"].get("real_name")
            or user_id
        )
        _user_name_cache[user_id] = name
    except Exception:
        name = user_id
        _user_name_cache[user_id] = name
    return name


# ---------------------------------------------------------------------------
# Session store: thread_ts → Claude session_id (file-backed)
# Plus recent-activity index: (channel, user) → (session_id, last_ts)
# ---------------------------------------------------------------------------

SESSION_FILE = LOG_DIR / ".sessions.json"
RECENT_FILE = LOG_DIR / ".recent_sessions.json"
MAX_SESSIONS = 200


def _load_sessions() -> dict:
    try:
        return json.loads(SESSION_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_session(thread_ts: str, session_id: str) -> None:
    sessions = _load_sessions()
    sessions[thread_ts] = session_id
    if len(sessions) > MAX_SESSIONS:
        for key in sorted(sessions.keys())[:-MAX_SESSIONS]:
            del sessions[key]
    SESSION_FILE.write_text(json.dumps(sessions))


def _get_session(thread_ts: str) -> str | None:
    return _load_sessions().get(thread_ts)


def _load_recent() -> dict:
    try:
        return json.loads(RECENT_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_recent(channel: str, user_id: str, session_id: str) -> None:
    data = _load_recent()
    data[f"{channel}:{user_id}"] = {"session": session_id, "ts": time.time()}
    RECENT_FILE.write_text(json.dumps(data))


def _get_recent_session(channel: str, user_id: str) -> str | None:
    entry = _load_recent().get(f"{channel}:{user_id}")
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > SESSION_RECENT_WINDOW:
        return None
    return entry.get("session")


# ---------------------------------------------------------------------------
# Event dedup
# ---------------------------------------------------------------------------

_seen_event_ids: list[str] = []
_seen_event_ids_set: set[str] = set()
_SEEN_MAX = 500


def _is_duplicate_event(event_id: str) -> bool:
    if not event_id:
        return False
    if event_id in _seen_event_ids_set:
        return True
    _seen_event_ids.append(event_id)
    _seen_event_ids_set.add(event_id)
    if len(_seen_event_ids) > _SEEN_MAX:
        old = _seen_event_ids.pop(0)
        _seen_event_ids_set.discard(old)
    return False


def _dedup_key(event: dict) -> str:
    return event.get("client_msg_id") or f"{event.get('channel')}:{event.get('ts')}"


# ---------------------------------------------------------------------------
# Activated threads — @mention once activates the thread, replies auto-trigger
# ---------------------------------------------------------------------------

_activated_threads: set[str] = set()
_activated_threads_lock = threading.Lock()
ACTIVATED_THREADS_FILE = LOG_DIR / ".activated_threads.json"


def _load_activated_threads() -> None:
    try:
        data = json.loads(ACTIVATED_THREADS_FILE.read_text())
        with _activated_threads_lock:
            _activated_threads.update(data)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _activate_thread(channel: str, thread_ts: str) -> None:
    key = f"{channel}:{thread_ts}"
    with _activated_threads_lock:
        _activated_threads.add(key)
        items = list(_activated_threads)
        if len(items) > MAX_SESSIONS:
            items = items[-MAX_SESSIONS:]
            _activated_threads.clear()
            _activated_threads.update(items)
    try:
        ACTIVATED_THREADS_FILE.write_text(json.dumps(list(_activated_threads)))
    except Exception:
        pass


def _is_bot_thread(channel: str, thread_ts: str) -> bool:
    key = f"{channel}:{thread_ts}"
    with _activated_threads_lock:
        if key in _activated_threads:
            return True
    if _get_session(thread_ts):
        return True
    return False


# ---------------------------------------------------------------------------
# Per-thread serial queue
# ---------------------------------------------------------------------------

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _get_thread_lock(key: str) -> threading.Lock:
    with _thread_locks_guard:
        lock = _thread_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[key] = lock
        return lock


_active_jobs: dict[str, dict] = {}
_active_jobs_guard = threading.Lock()


def _job_start(key: str, text_preview: str) -> None:
    with _active_jobs_guard:
        _active_jobs[key] = {
            "start": time.time(),
            "prompt": text_preview,
            "last_tool": None,
            "last_text": None,
        }


def _job_end(key: str) -> None:
    with _active_jobs_guard:
        _active_jobs.pop(key, None)


def _job_update(key: str, **fields) -> None:
    with _active_jobs_guard:
        if key in _active_jobs:
            _active_jobs[key].update(fields)


def _job_info(key: str) -> dict | None:
    with _active_jobs_guard:
        job = _active_jobs.get(key)
        return dict(job) if job else None


# ---------------------------------------------------------------------------
# Auth / audit
# ---------------------------------------------------------------------------


def is_authorized(user_id: str) -> bool:
    return not AUTHORIZED_USERS or user_id in AUTHORIZED_USERS


def log_unauthorized(event: dict) -> None:
    user = event.get("user", "unknown")
    channel = event.get("channel", "unknown")
    text = event.get("text", "")[:100]
    audit_logger.warning(
        f'UNAUTHORIZED | USER:{user} | CHANNEL:{channel} | MSG:"{text}"'
    )


def audit_interaction(
    event: dict, response_text: str, duration: float, session_id: str | None
) -> None:
    user = event.get("user", "unknown")
    channel = event.get("channel", "unknown")
    text = event.get("text", "")[:200]
    audit_logger.info(
        f"USER:{user} | CHANNEL:{channel} | SESSION:{session_id or 'new'} "
        f"| DURATION:{duration:.1f}s | MSG_LEN:{len(text)} | RESP_LEN:{len(response_text)} "
        f'| MSG:"{text}"'
    )


# ---------------------------------------------------------------------------
# File handling (Slack attachments in + auto-upload out)
# ---------------------------------------------------------------------------


def download_slack_files(event: dict) -> list[Path]:
    files = event.get("files", [])
    if not files:
        return []

    downloaded = []
    for f in files:
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            continue

        name = f.get("name", "attachment")
        suffix = Path(name).suffix or ".bin"

        try:
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
            )
            with urllib.request.urlopen(req) as resp:
                tmp = tempfile.NamedTemporaryFile(
                    suffix=suffix, prefix="slack-", delete=False
                )
                tmp.write(resp.read())
                tmp.close()
                downloaded.append(Path(tmp.name))
                logger.info(f"Downloaded Slack file: {name} -> {tmp.name}")
                term("FILE", f"downloaded {name} -> {tmp.name}", C_RESULT)
        except Exception as e:
            logger.error(f"Failed to download Slack file {name}: {e}")

    return downloaded


_SHAREABLE_EXT = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "heic", "tiff", "bmp",
    "mp4", "mov", "webm", "mkv", "avi", "m4v",
    "mp3", "wav", "flac", "ogg", "m4a", "aac",
    "pdf", "csv", "xlsx", "txt", "html", "md", "json", "yaml", "yml", "log",
    "zip", "tar", "gz", "tgz",
}

_ABS_PATH_RE = re.compile(
    r'(?:^|[\s\(\[`"\'])(/(?:Users|tmp|var|private|opt|mnt|home)[^\s\'"<>|*?\)\]`]+\.(\w{1,6}))',
    re.IGNORECASE | re.MULTILINE,
)
_MD_PATH_RE = re.compile(r'\!?\[[^\]]*\]\(([^)\s]+\.(\w{1,6}))\)')
_LABELLED_PATH_RE = re.compile(
    r'(?:saved to|output|output file|file|path|wrote|created|generated|final)[\s:]+([^\s\'"<>|*?`]+\.(\w{1,6}))',
    re.IGNORECASE,
)


def _resolve_path(candidate: str) -> Path | None:
    if not candidate:
        return None
    candidate = candidate.strip().rstrip(".,;:")
    p = Path(candidate).expanduser()
    if p.is_absolute():
        return p if p.exists() and p.is_file() else None
    for base in (Path(PROJECT_DIR), Path.cwd()):
        resolved = (base / p).resolve()
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def _extract_shareable_paths(text: str) -> list[Path]:
    seen: set[str] = set()
    results: list[Path] = []

    def _consider(raw: str, ext: str) -> None:
        if ext.lower() not in _SHAREABLE_EXT:
            return
        resolved = _resolve_path(raw)
        if not resolved:
            return
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        results.append(resolved)

    for m in _ABS_PATH_RE.finditer(text):
        _consider(m.group(1), m.group(2))
    for m in _MD_PATH_RE.finditer(text):
        _consider(m.group(1), m.group(2))
    for m in _LABELLED_PATH_RE.finditer(text):
        _consider(m.group(1), m.group(2))

    return results


def upload_file_to_slack(
    file_path: str,
    channel: str,
    thread_ts: str | None = None,
    title: str | None = None,
    message: str | None = None,
) -> None:
    path = Path(file_path)
    if not path.exists():
        logger.error(f"File not found: {file_path}")
        return

    filename = title or path.name
    file_size = path.stat().st_size

    try:
        url_response = slack_client.files_getUploadURLExternal(
            filename=filename,
            length=file_size,
        )
        upload_url = url_response["upload_url"]
        file_id = url_response["file_id"]

        with open(path, "rb") as f:
            req = urllib.request.Request(
                upload_url,
                data=f.read(),
                method="POST",
                headers={"Content-Type": "application/octet-stream"},
            )
            urllib.request.urlopen(req)

        slack_client.files_completeUploadExternal(
            files=[{"id": file_id, "title": filename}],
            channel_id=channel,
            thread_ts=thread_ts,
            initial_comment=message or "",
        )

        logger.info(f"Uploaded file to Slack: {filename} ({file_size} bytes) -> {channel}")
        term("UPLOAD", f"{filename} ({file_size} bytes) -> {channel}", C_RESULT)
    except Exception as e:
        logger.error(f"Failed to upload file {file_path}: {e}")
        slack_client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"Tried to upload `{filename}` but failed: {e}",
        )


# ---------------------------------------------------------------------------
# Proactive messaging (CLI mode)
# ---------------------------------------------------------------------------


def send_dm(
    user_id: str,
    message: str,
    session_id: str | None = None,
    thread_ts: str | None = None,
) -> str | None:
    response = slack_client.conversations_open(users=[user_id])
    channel_id = response["channel"]["id"]

    slack_text = md_to_slack(message)
    chunks = chunk_message(slack_text)

    parent_ts = thread_ts
    for chunk in chunks:
        result = slack_client.chat_postMessage(
            channel=channel_id, text=chunk, thread_ts=parent_ts,
        )
        if parent_ts is None:
            parent_ts = result["ts"]

    effective_thread_ts = thread_ts or parent_ts

    if session_id and effective_thread_ts:
        _save_session(effective_thread_ts, session_id)

    audit_logger.info(
        f"PROACTIVE_DM | USER:{user_id} | CHANNEL:{channel_id} "
        f"| THREAD:{effective_thread_ts} | SESSION:{session_id or 'none'} "
        f"| MSG_LEN:{len(message)}"
    )
    return effective_thread_ts


def send_to_channel(channel: str, message: str) -> None:
    slack_text = md_to_slack(message)
    for chunk in chunk_message(slack_text):
        slack_client.chat_postMessage(channel=channel, text=chunk)
    audit_logger.info(f"PROACTIVE_CHANNEL | CHANNEL:{channel} | MSG_LEN:{len(message)}")


# ---------------------------------------------------------------------------
# Claude CLI — streaming, with terminal visibility for every block
# ---------------------------------------------------------------------------


def _summarize_tool_input(name: str, tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return str(tool_input)[:200]

    if name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd if len(cmd) < 200 else cmd[:197] + "..."
    if name in ("Read", "Write"):
        return tool_input.get("file_path", "")
    if name == "Edit":
        fp = tool_input.get("file_path", "")
        old = (tool_input.get("old_string", "") or "")[:40].replace("\n", "⏎")
        return f"{fp}  [{old}...]"
    if name == "Glob":
        return tool_input.get("pattern", "")
    if name == "Grep":
        pat = tool_input.get("pattern", "")
        path = tool_input.get("path", "")
        return f"{pat}  in  {path}" if path else pat
    if name == "WebFetch":
        return tool_input.get("url", "")[:200]
    if name == "WebSearch":
        return tool_input.get("query", "")
    if name in ("Agent", "Task"):
        return tool_input.get("description", "") or tool_input.get("prompt", "")[:100]
    if name == "TodoWrite":
        todos = tool_input.get("todos", [])
        return f"{len(todos)} items" if todos else ""

    try:
        k, v = next(iter(tool_input.items()))
        s = f"{k}={v}"
        return s if len(s) < 200 else s[:197] + "..."
    except StopIteration:
        return ""


def call_claude_streaming(
    prompt: str,
    session_id: str | None,
    on_text: callable,
    on_tool: callable | None = None,
) -> str | None:
    _reload_claude_config()

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", CLAUDE_PERMISSION_MODE,
    ]
    if CLAUDE_MODEL:
        cmd.extend(["--model", CLAUDE_MODEL])
    cmd.extend(["--effort", CLAUDE_EFFORT])
    if session_id:
        cmd.extend(["--resume", session_id])

    logger.info(f"Spawning claude (model={CLAUDE_MODEL or 'default'}, resume={session_id or 'none'})")
    term("CLAUDE", f"model={CLAUDE_MODEL or 'default'} effort={CLAUDE_EFFORT} timeout={CLAUDE_TIMEOUT}s resume={session_id or 'new'}", C_DONE)

    stderr_tmp = tempfile.NamedTemporaryFile(mode="w+", suffix=".stderr", delete=False)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=stderr_tmp,
        text=True,
        cwd=PROJECT_DIR,
    )

    new_session_id = session_id
    deadline = time.time() + CLAUDE_TIMEOUT

    try:
        for line in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                raise subprocess.TimeoutExpired(cmd, CLAUDE_TIMEOUT)

            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")

            if msg_type == "assistant":
                content = data.get("message", {}).get("content", [])
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text = (block.get("text") or "").strip()
                        if text:
                            for ln in text.splitlines():
                                term("TEXT", ln, C_TEXT)
                            on_text(text)
                    elif btype == "tool_use":
                        name = block.get("name", "?")
                        summary = _summarize_tool_input(name, block.get("input", {}))
                        term("TOOL", f"{name}({summary})", C_TOOL)
                        if on_tool:
                            try:
                                on_tool(f"`{name}` — {summary}" if summary else f"`{name}`")
                            except Exception as e:
                                logger.warning(f"on_tool callback failed: {e}")

            elif msg_type == "user":
                content = data.get("message", {}).get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        raw = block.get("content", "")
                        if isinstance(raw, list):
                            raw = "\n".join(
                                c.get("text", "") if isinstance(c, dict) else str(c)
                                for c in raw
                            )
                        raw = str(raw).strip()
                        if not raw:
                            continue
                        first_line = raw.splitlines()[0][:200]
                        extra_lines = raw.count("\n")
                        suffix = f"  (+{extra_lines} more lines)" if extra_lines else ""
                        term("RESULT", first_line + suffix, C_RESULT)

            elif msg_type == "result":
                new_session_id = data.get("session_id") or new_session_id

        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    except Exception as e:
        proc.kill()
        raise RuntimeError(f"Claude streaming error: {e}")
    finally:
        stderr_tmp.close()

    if proc.returncode != 0:
        try:
            stderr_text = Path(stderr_tmp.name).read_text().strip()
        except Exception:
            stderr_text = "(stderr unavailable)"
        logger.error(f"Claude CLI failed (rc={proc.returncode}): {stderr_text[:500]}")
        term("ERR", f"claude rc={proc.returncode}: {stderr_text[:200]}", C_ERR)
        try:
            os.unlink(stderr_tmp.name)
        except OSError:
            pass
        raise RuntimeError(f"Claude CLI error: {stderr_text[:300]}")

    try:
        os.unlink(stderr_tmp.name)
    except OSError:
        pass

    return new_session_id


# ---------------------------------------------------------------------------
# Markdown → Slack mrkdwn
# ---------------------------------------------------------------------------


def md_to_slack(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)
    text = re.sub(r"```\w*\n", "```\n", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    return text


def chunk_message(text: str) -> list:
    if len(text) <= MAX_SLACK_MSG_LEN:
        return [text]

    chunks = []
    while text:
        if len(text) <= MAX_SLACK_MSG_LEN:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, MAX_SLACK_MSG_LEN)
        if split_at == -1:
            split_at = text.rfind(" ", 0, MAX_SLACK_MSG_LEN)
        if split_at == -1:
            split_at = MAX_SLACK_MSG_LEN
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------


def process_message_async(event: dict) -> None:
    user_id = event.get("user", "")
    text = event.get("text", "").strip()
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts") or event.get("ts")
    msg_ts = event.get("ts")
    raw_thread_ts = event.get("thread_ts")

    text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

    # Status check — bypasses the queue
    if re.match(r"^(?:/)?(?:status|progress|update|running|check)\s*\??\s*$", text, re.IGNORECASE):
        lock_key = f"{channel}:{thread_ts}"
        info = _job_info(lock_key)
        if info:
            elapsed = int(time.time() - info["start"])
            mins, secs = divmod(elapsed, 60)
            lines = [f"*A job is running — {mins}m {secs}s elapsed.*"]
            if info.get("prompt"):
                lines.append(f"*Prompt:* {info['prompt'][:200]}")
            if info.get("last_tool"):
                lines.append(f"*Last tool call:* `{info['last_tool']}`")
            if info.get("last_text"):
                lines.append(f"*Last output:* {info['last_text'][:200]}")
            msg = "\n".join(lines)
        else:
            msg = "No active job in this thread. Ready for your next message."
        term("STATUS", f"status check for {lock_key}", C_MSG)
        try:
            slack_client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=msg)
        except Exception as e:
            logger.warning(f"status reply failed: {e}")
        return

    # Manual share command: /share <path> or share /abs/path.ext
    share_match = re.match(
        r"^(?:/share\s+(.+)$|share\s+((?:/|~/)[^\s]+|[^\s]+\.\w{1,6}(?:\s.*)?))",
        text,
        re.IGNORECASE,
    )
    if share_match:
        raw_path = (share_match.group(1) or share_match.group(2) or "").strip().strip("`<>\"'")
        target = _resolve_path(raw_path)
        term("SHARE", f"manual share request: {raw_path} → {target}", C_MSG)
        if target:
            upload_file_to_slack(str(target), channel, thread_ts=thread_ts, message=f"Shared `{target.name}`")
        else:
            slack_client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=f"Couldn't find a file at `{raw_path}`. Absolute paths or paths under `{PROJECT_DIR}` work.",
            )
        return

    attached_files = download_slack_files(event)

    if not text and not attached_files:
        return

    if attached_files:
        file_instructions = [f"The user attached a file. Read it at: {fp}" for fp in attached_files]
        text = "\n".join(file_instructions) + "\n\n" + (text or "Describe what you see in the attached file(s).")

    sender_name = _get_user_name(user_id)
    text = (
        f"[{sender_name}] says:\n{text}\n\n"
        "[System: If you produce a file the user should see (video, image, PDF, audio, doc), "
        "end your reply with the absolute path on its own line — it will auto-upload to this Slack thread. "
        f"Example: `{PROJECT_DIR}/out/video.mp4` on its own line.]"
    )

    lock_key = f"{channel}:{thread_ts}"
    lock = _get_thread_lock(lock_key)
    if not lock.acquire(blocking=False):
        term("QUEUE", f"waiting for prior run on {channel}/{thread_ts}", C_DONE)
        logger.info(f"Queueing message for {lock_key}; waiting for prior run")
        lock.acquire()

    _job_start(lock_key, text[:300])
    try:
        _process_locked(event, user_id, text, channel, thread_ts, msg_ts, raw_thread_ts, sender_name)
    finally:
        _job_end(lock_key)
        lock.release()


def _process_locked(
    event: dict,
    user_id: str,
    text: str,
    channel: str,
    thread_ts: str,
    msg_ts: str,
    raw_thread_ts: str | None,
    sender_name: str,
) -> None:
    session_id = _get_session(thread_ts)
    resume_source = "thread" if session_id else "none"

    term(
        "MSG",
        f"{sender_name} in {channel} | msg_ts={msg_ts} thread_ts={raw_thread_ts or '(none)'} "
        f"→ key={thread_ts} resume={session_id or 'new'} ({resume_source})",
        C_MSG,
    )
    term("MSG", f"    {text.splitlines()[0][:200]}", C_MSG)

    try:
        slack_client.reactions_add(channel=channel, name="eyes", timestamp=msg_ts)
    except Exception:
        pass

    all_texts = []
    first_text_sent = False
    skip_detected = False

    lock_key = f"{channel}:{thread_ts}"

    def on_text(text_block: str):
        nonlocal first_text_sent, skip_detected

        if not first_text_sent and text_block.strip() == "SKIP":
            skip_detected = True
            return

        all_texts.append(text_block)
        _job_update(lock_key, last_text=text_block[:300])

        for fp in _extract_shareable_paths(text_block):
            upload_file_to_slack(str(fp), channel, thread_ts=thread_ts)

        slack_text = md_to_slack(text_block)
        for chunk in chunk_message(slack_text):
            slack_client.chat_postMessage(
                channel=channel, text=chunk, thread_ts=thread_ts,
            )
        first_text_sent = True

    def on_tool(label: str):
        _job_update(lock_key, last_tool=label)
        if not MIRROR_TOOLS_TO_SLACK:
            return
        try:
            slack_client.chat_postMessage(
                channel=channel, text=f":wrench: {label}", thread_ts=thread_ts,
            )
        except Exception as e:
            logger.warning(f"mirror tool to slack failed: {e}")

    def _run_with_repair():
        try:
            return call_claude_streaming(text, session_id, on_text, on_tool)
        except (OSError, FileNotFoundError) as e:
            term("ERR", f"claude exec failed ({e}) — running self-repair", C_ERR)
            logger.warning(f"claude exec failed ({e}); attempting self-repair")
            _ensure_claude_cli_working()
            return call_claude_streaming(text, session_id, on_text, on_tool)

    start = time.time()
    try:
        new_session_id = _run_with_repair()
    except subprocess.TimeoutExpired:
        minutes = CLAUDE_TIMEOUT // 60
        _remove_reaction(channel, msg_ts)
        slack_client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f"Timed out after {minutes} minutes. Try a simpler question?",
        )
        term("ERR", f"timeout after {CLAUDE_TIMEOUT}s", C_ERR)
        return
    except RuntimeError as e:
        _remove_reaction(channel, msg_ts)
        slack_client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f"Something went wrong: {e}",
        )
        term("ERR", str(e), C_ERR)
        return
    except (OSError, FileNotFoundError) as e:
        _remove_reaction(channel, msg_ts)
        slack_client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f"Claude CLI still broken after self-repair: {e}. Try `npm install -g @anthropic-ai/claude-code`",
        )
        term("ERR", f"claude exec failed after repair: {e}", C_ERR)
        return
    duration = time.time() - start

    if skip_detected:
        _remove_reaction(channel, msg_ts)
        term("SKIP", f"{user_id} in {channel} (not relevant)", C_DONE)
        return

    if new_session_id and thread_ts:
        _save_session(thread_ts, new_session_id)

    _remove_reaction(channel, msg_ts)

    full_response = "\n\n".join(all_texts)
    audit_interaction(event, full_response, duration, new_session_id)
    term("DONE", f"{duration:.1f}s  chars={len(full_response)}  session={new_session_id}", C_DONE)
    logger.info(f"Responded to {user_id} in {duration:.1f}s ({len(full_response)} chars)")


def _remove_reaction(channel: str, ts: str) -> None:
    try:
        slack_client.reactions_remove(channel=channel, name="eyes", timestamp=ts)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Slack event handlers
# ---------------------------------------------------------------------------

if app is not None:

    @app.event("app_mention")
    def handle_mention(event, say):
        if _is_duplicate_event(_dedup_key(event)):
            logger.info(f"Ignoring duplicate mention event {_dedup_key(event)}")
            return
        user_id = event.get("user", "")
        if not is_authorized(user_id):
            log_unauthorized(event)
            say(text="I only respond to authorized users.", thread_ts=event.get("ts"))
            return
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")
        _activate_thread(channel, thread_ts)
        threading.Thread(target=process_message_async, args=(event,), daemon=True).start()


    @app.event("message")
    def handle_message(event, say):
        subtype = event.get("subtype")
        if subtype and subtype != "file_share":
            return

        if _is_duplicate_event(_dedup_key(event)):
            logger.info(f"Ignoring duplicate message event {_dedup_key(event)}")
            return

        user_id = event.get("user", "")
        if not is_authorized(user_id):
            log_unauthorized(event)
            if event.get("channel_type") in ("im", "mpim"):
                say(text="I only respond to authorized users.", thread_ts=event.get("ts"))
            return

        channel_type = event.get("channel_type", "")
        is_dm = channel_type in ("im", "mpim")

        if event.get("bot_id"):
            return

        if is_dm:
            threading.Thread(target=process_message_async, args=(event,), daemon=True).start()
            return

        if USE_EVENTS_API:
            threading.Thread(target=process_message_async, args=(event,), daemon=True).start()
            return

        # Socket Mode channel message: only respond if it's a reply in an activated thread
        raw_thread_ts = event.get("thread_ts")
        if not raw_thread_ts:
            return
        channel = event.get("channel", "")
        if _is_bot_thread(channel, raw_thread_ts):
            threading.Thread(target=process_message_async, args=(event,), daemon=True).start()


    @app.event("member_joined_channel")
    def handle_member_joined(event):
        pass


    @app.event("reaction_added")
    def handle_reaction(event):
        pass


    @app.event("file_shared")
    def handle_file_shared(event):
        pass


# ---------------------------------------------------------------------------
# Self-healing CLI
# ---------------------------------------------------------------------------


def _ensure_claude_cli_working() -> None:
    try:
        r = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            logger.info(f"claude CLI OK: {r.stdout.strip()}")
            print(f"  Claude CLI:   {r.stdout.strip()}", flush=True)
            return
        logger.warning(
            f"claude --version failed (rc={r.returncode}): {r.stderr[:200]}. Attempting self-repair."
        )
    except FileNotFoundError:
        logger.warning("claude CLI not on PATH. Cannot self-repair.")
        print("  Claude CLI:   not on PATH — bot will fail on every message", flush=True)
        return
    except Exception as e:
        logger.warning(f"claude --version probe failed: {e}. Attempting self-repair.")

    try:
        which = subprocess.run(["which", "claude"], capture_output=True, text=True, timeout=5)
        if which.returncode == 0:
            bin_path = which.stdout.strip()
            real = os.path.realpath(bin_path)
            pkg_dir = Path(real).parent.parent
            installer = pkg_dir / "install.cjs"
            if installer.exists():
                logger.info(f"Running postinstall: node {installer}")
                r = subprocess.run(["node", str(installer)], capture_output=True, text=True, timeout=120)
                logger.info(f"Postinstall rc={r.returncode}: {(r.stdout + r.stderr)[:400]}")
    except Exception as e:
        logger.warning(f"Self-repair failed: {e}")

    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            logger.info(f"Self-repair succeeded: {r.stdout.strip()}")
            print(f"  Claude CLI:   self-repaired -> {r.stdout.strip()}", flush=True)
        else:
            logger.error(f"Self-repair failed: {r.stderr[:200]}")
            print(f"  Claude CLI:   self-repair failed — messages will error", flush=True)
    except Exception as e:
        logger.error(f"Post-repair probe failed: {e}")


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

if app is not None:
    _slack_flask_handler = SlackRequestHandler(app)
else:
    _slack_flask_handler = None

flask_app = Flask(__name__)


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    retry_num = flask_request.headers.get("X-Slack-Retry-Num")
    if retry_num:
        logger.info(
            f"Ignoring Slack retry {retry_num} "
            f"(reason={flask_request.headers.get('X-Slack-Retry-Reason')})"
        )
        return jsonify({"ok": True, "dropped": "retry"}), 200

    if flask_request.content_type == "application/json":
        body = flask_request.get_json(silent=True) or {}
        if body.get("type") == "url_verification":
            return jsonify({"challenge": body.get("challenge", "")})

    if _slack_flask_handler is None:
        return jsonify({"ok": False, "error": "bot not configured"}), 500
    return _slack_flask_handler.handle(flask_request)


@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "bot": "claude-code-slack"})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Claude Code Slack Bot")
    parser.add_argument(
        "--send", nargs=2, metavar=("USER_ID", "MESSAGE"),
        help="Send a proactive DM and exit",
    )
    parser.add_argument(
        "--send-result", metavar="USER_ID",
        help="Read Claude JSON from stdin, send as DM with session linking",
    )
    parser.add_argument(
        "--thread", metavar="THREAD_TS",
        help="Reply in an existing thread (use with --send or --send-result)",
    )
    parser.add_argument(
        "--channel", nargs=2, metavar=("CHANNEL", "MESSAGE"),
        help="Post a message to a channel and exit",
    )
    args = parser.parse_args()

    if args.send:
        thread_ts = send_dm(args.send[0], args.send[1], thread_ts=args.thread)
        if thread_ts:
            print(thread_ts)
        return

    if args.send_result:
        raw = sys.stdin.read().strip()
        try:
            data = json.loads(raw)
            message = data.get("result", "")
            session_id = data.get("session_id")
        except json.JSONDecodeError:
            message = raw
            session_id = None
        if not message:
            message = "Job completed but produced no output."
        send_dm(args.send_result, message, session_id=session_id, thread_ts=args.thread)
        return

    if args.channel:
        send_to_channel(args.channel[0], args.channel[1])
        return

    # Server mode
    if not SLACK_BOT_TOKEN:
        logger.error("Missing SLACK_BOT_TOKEN in .env")
        raise SystemExit(1)

    _ensure_claude_cli_working()
    _load_activated_threads()

    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    mode = "Events API (Cloudflare)" if USE_EVENTS_API else "Socket Mode"

    print("=" * 60, flush=True)
    print("  Claude Code Slack Bot", flush=True)
    print("=" * 60, flush=True)
    print(f"  Project dir:  {PROJECT_DIR}", flush=True)
    print(f"  Mode:         {mode}", flush=True)
    print(f"  Model:        {CLAUDE_MODEL or '(default)'}", flush=True)
    print(f"  Effort:       {CLAUDE_EFFORT}", flush=True)
    print(f"  Permission:   {CLAUDE_PERMISSION_MODE}", flush=True)
    print(f"  Timeout:      {CLAUDE_TIMEOUT}s", flush=True)
    print(f"  Auth:         {AUTHORIZED_USERS or 'all users'}", flush=True)
    print(f"  Tool mirror:  {MIRROR_TOOLS_TO_SLACK}", flush=True)
    print(f"  Session win:  {SESSION_RECENT_WINDOW}s", flush=True)

    if USE_EVENTS_API:
        print(f"  HTTP port:    {PORT}", flush=True)
        print("=" * 60, flush=True)
        print("  Bot is live. Point Cloudflare tunnel at this port.", flush=True)
        print("  No @mention needed — bot reads all messages in its channels.", flush=True)
        print("", flush=True)
        flask_app.run(host="0.0.0.0", port=PORT)
    else:
        print("=" * 60, flush=True)

        if not SLACK_APP_TOKEN:
            print("  ERROR: SLACK_APP_TOKEN required for Socket Mode!", flush=True)
            print("  Set it in .env or switch to Events API by setting SLACK_SIGNING_SECRET.", flush=True)
            sys.exit(1)

        print(f"  @mention:     Once to start a thread, then just reply", flush=True)
        print(f"  Threads:      {len(_activated_threads)} activated threads loaded", flush=True)
        print("", flush=True)
        print("  Bot is running! Send a message in Slack.", flush=True)
        print("", flush=True)

        from slack_bolt.adapter.socket_mode import SocketModeHandler
        socket_handler = SocketModeHandler(app, SLACK_APP_TOKEN)
        socket_handler.start()


if __name__ == "__main__":
    main()
