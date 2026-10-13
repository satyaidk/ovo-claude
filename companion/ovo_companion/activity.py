"""Turn Claude Code tool calls into short display labels.

The labels describe *what kind of action* is running ("Running tests",
"Editing code"), derived from the tool name and, for shell commands, the
program being run. They are not Claude's reasoning and never quote a prompt.

Shell commands get extra care: arguments can contain tokens, passwords or
paths, so only the first program name is ever shown, and only on the approval
screen ("Run: npm"). File paths are reduced to the base name.
"""
from __future__ import annotations

import ntpath
import posixpath
import re
from typing import Mapping, Optional
from urllib.parse import urlsplit

from .protocol import ascii_text

LABEL_LIMIT = 32

_TOOL_LABELS = {
    "Read": "Reading files",
    "Glob": "Searching code",
    "Grep": "Searching code",
    "LS": "Listing files",
    "Edit": "Editing code",
    "MultiEdit": "Editing code",
    "NotebookEdit": "Editing notebook",
    "Write": "Writing files",
    "WebFetch": "Reading the web",
    "WebSearch": "Searching the web",
    "Task": "Running subagent",
    "Agent": "Running subagent",
    "TodoWrite": "Planning tasks",
    "TaskCreate": "Planning tasks",
    "TaskUpdate": "Planning tasks",
    "Skill": "Using a skill",
    "AskUserQuestion": "Asking you",
    "EnterPlanMode": "Planning",
    "ExitPlanMode": "Plan ready",
    "ToolSearch": "Loading tools",
}

_SHELL_TOOLS = {"Bash", "PowerShell", "BashOutput"}

# Matched against one shell segment (text between &&, ||, ; and |), lower case.
_TEST_RE = re.compile(
    r"""^(?:
        (?:py\.?test|pytest|jest|vitest|mocha|ctest|tox|nox|rspec|phpunit|karma)\b
      | python[0-9.]*(?:\.exe)?\s+-m\s+(?:pytest|unittest)\b
      | (?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b
      | (?:cargo|go|deno|dotnet|swift|mix|bazel)\s+test\b
      | (?:mvn|\./mvnw|gradle|\./gradlew)\s+(?:\S+\s+)*test\b
      | make\s+(?:\S+\s+)*(?:test|check)\b
      | (?:\./|\.\\)?\S*test_\S*
    )""",
    re.X,
)
_BUILD_RE = re.compile(
    r"""^(?:
        (?:make|cmake|ninja|msbuild|tsc|webpack|vite\s+build|gcc|g\+\+|c\+\+|clang\+\+|clang|cl)(?:\.exe)?(?=\s|$)
      | (?:cargo|go|dotnet|swift|zig)\s+build\b
      | (?:npm|pnpm|yarn|bun)\s+run\s+build\b
      | arduino-cli(?:\.exe)?\s+compile\b
      | idf\.py\s+build\b
      | (?:mvn|gradle|\./gradlew)\s+(?:package|compile|build|install)\b
    )""",
    re.X,
)
_INSTALL_RE = re.compile(
    r"""^(?:
        pip[0-9.]*\s+install\b | python[0-9.]*\s+-m\s+pip\s+install\b
      | (?:npm|pnpm)\s+(?:install|ci|add|i)\b | yarn(?:\s+(?:install|add))?$ | yarn\s+add\b
      | (?:uv|poetry)\s+(?:add|sync|install|pip)\b | cargo\s+add\b
      | (?:apt|apt-get|brew|choco|winget|scoop)\s+install\b
      | arduino-cli(?:\.exe)?\s+(?:lib|core)\s+install\b
    )""",
    re.X,
)

_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
_PREFIXES = {"sudo", "time", "nice", "env", "command", "exec", "nohup", "npx", "uvx", "&"}


def _segments(command: str):
    for seg in _SEGMENT_SPLIT.split(command or ""):
        seg = seg.strip()
        if not seg or seg.startswith("#"):
            continue
        yield seg


def _strip_prefixes(seg: str) -> str:
    words = seg.split()
    while words and (_ENV_ASSIGN.match(words[0]) or words[0] in _PREFIXES):
        words.pop(0)
    return " ".join(words)


def classify_command(command: str) -> str:
    """'Running tests', 'Building', 'Installing deps', 'Using git' or 'Running command'."""
    segs = [_strip_prefixes(s).lower() for s in _segments(command)]
    segs = [s for s in segs if s and not s.startswith(("cd ", "pushd ", "set-location ", "echo "))]
    if any(_TEST_RE.match(s) for s in segs):
        return "Running tests"
    if any(_BUILD_RE.match(s) for s in segs):
        return "Building"
    if any(_INSTALL_RE.match(s) for s in segs):
        return "Installing deps"
    first = segs[0].split()[0] if segs else ""
    if first in ("git", "git.exe"):
        return "Using git"
    if first in ("gh", "gh.exe"):
        return "Using GitHub CLI"
    return "Running command"


def program_name(command: str) -> str:
    """The first program a command runs, without its arguments or directory."""
    for seg in _segments(command):
        seg = _strip_prefixes(seg)
        if not seg or seg.lower().startswith(("cd ", "pushd ", "set-location ")):
            continue
        token = seg.split()[0].strip("\"'`&")
        base = ntpath.basename(posixpath.basename(token))
        return ascii_text(base, 20)
    return ""


def _base_name(path: object) -> str:
    if not isinstance(path, str) or not path:
        return ""
    return ascii_text(ntpath.basename(posixpath.basename(path.rstrip("/\\"))), 22)


def _mcp_server(tool_name: str) -> str:
    parts = tool_name.split("__")
    return ascii_text(parts[1] if len(parts) > 2 else tool_name, 20)


def tool_activity(tool_name: Optional[str], tool_input: Optional[Mapping]) -> str:
    """The running label for a tool call."""
    name = tool_name or ""
    tool_input = tool_input if isinstance(tool_input, Mapping) else {}
    if name in _SHELL_TOOLS:
        command = tool_input.get("command")
        return classify_command(command) if isinstance(command, str) else "Running command"
    if name in _TOOL_LABELS:
        return _TOOL_LABELS[name]
    if name.startswith("mcp__"):
        return ascii_text("MCP: " + _mcp_server(name), LABEL_LIMIT)
    if not name:
        return "Working"
    return ascii_text("Using " + name, LABEL_LIMIT)


def permission_action(tool_name: Optional[str], tool_input: Optional[Mapping]) -> str:
    """A short, sanitised description of what is waiting for approval."""
    name = tool_name or ""
    tool_input = tool_input if isinstance(tool_input, Mapping) else {}
    if name in _SHELL_TOOLS:
        prog = program_name(tool_input.get("command", "") if isinstance(tool_input.get("command"), str) else "")
        return ascii_text("Run: " + prog if prog else "Run a command", LABEL_LIMIT)
    if name in ("Edit", "MultiEdit", "NotebookEdit", "Write", "Read"):
        verb = {"Write": "Write", "Read": "Read"}.get(name, "Edit")
        base = _base_name(tool_input.get("file_path") or tool_input.get("notebook_path"))
        return ascii_text(f"{verb}: {base}" if base else f"{verb} a file", LABEL_LIMIT)
    if name == "WebFetch":
        url = tool_input.get("url")
        host = ""
        if isinstance(url, str):
            try:
                host = urlsplit(url).hostname or ""
            except ValueError:
                host = ""
        return ascii_text("Fetch: " + host if host else "Fetch a web page", LABEL_LIMIT)
    if name == "WebSearch":
        return "Search the web"
    if name.startswith("mcp__"):
        return ascii_text("MCP: " + _mcp_server(name), LABEL_LIMIT)
    if not name:
        return "Permission request"
    return ascii_text("Use: " + name, LABEL_LIMIT)


STOP_FAILURE_LABELS = {
    "rate_limit": "Rate limited",
    "overloaded": "API overloaded",
    "authentication_failed": "Sign-in failed",
    "oauth_org_not_allowed": "Org not allowed",
    "account_on_hold": "Account on hold",
    "billing_error": "Billing error",
    "invalid_request": "Invalid request",
    "model_not_found": "Model not found",
    "server_error": "Server error",
    "max_output_tokens": "Output limit hit",
    "cloud_credential_error": "Cloud credentials",
}


def failure_label(error: Optional[str]) -> str:
    return STOP_FAILURE_LABELS.get(error or "", "Task failed")
