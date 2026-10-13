"""Tool calls become short labels; approval text never leaks arguments."""
import pytest

from ovo_companion.activity import (
    classify_command, failure_label, permission_action, program_name, tool_activity,
)


@pytest.mark.parametrize("command, label", [
    ("pytest -q", "Running tests"),
    ("cd tests/host && ./test_touch", "Running tests"),
    ("python -m pytest companion/tests", "Running tests"),
    ("npm test", "Running tests"),
    ("npm run test:unit", "Running tests"),
    ("cargo test --all", "Running tests"),
    ("go test ./...", "Running tests"),
    ("make -j4 check", "Running tests"),
    ("CI=1 npx jest --watch=false", "Running tests"),
    ("arduino-cli compile --fqbn esp32:esp32:esp32c3 firmware/MochiEyes", "Building"),
    ("g++ -std=c++11 test_claude.cpp -o t", "Building"),
    ("npm run build", "Building"),
    ("cmake --build build", "Building"),
    ("pip install -e companion", "Installing deps"),
    ("npm ci", "Installing deps"),
    ("git status", "Using git"),
    ("gh pr view 12", "Using GitHub CLI"),
    ("ls -la", "Running command"),
    ("", "Running command"),
])
def test_shell_commands_are_classified(command, label):
    assert classify_command(command) == label


@pytest.mark.parametrize("tool, label", [
    ("Read", "Reading files"), ("Grep", "Searching code"), ("Glob", "Searching code"),
    ("Edit", "Editing code"), ("MultiEdit", "Editing code"), ("Write", "Writing files"),
    ("WebFetch", "Reading the web"), ("WebSearch", "Searching the web"), ("Task", "Running subagent"),
    ("Agent", "Running subagent"), ("TodoWrite", "Planning tasks"),
])
def test_tool_labels(tool, label):
    assert tool_activity(tool, {}) == label


def test_unknown_and_mcp_tools():
    assert tool_activity("mcp__github__create_issue", {}) == "MCP: github"
    assert tool_activity("SomeNewTool", {}) == "Using SomeNewTool"
    assert tool_activity(None, None) == "Working"
    assert tool_activity("Bash", {"command": "pytest"}) == "Running tests"
    assert tool_activity("Bash", {"command": 42}) == "Running command"


def test_program_name_drops_arguments_env_and_paths():
    assert program_name("FOO=1 sudo /usr/bin/npm install") == "npm"
    assert program_name('cd x && "C:\\Tools\\curl.exe" -H x') == "curl.exe"
    assert program_name("") == ""


def test_approval_text_never_contains_command_arguments():
    secret = "sk-ant-THIS-IS-A-SECRET"
    action = permission_action("Bash", {"command": f"curl -H 'Authorization: Bearer {secret}' https://x.io"})
    assert action == "Run: curl"
    assert secret not in action and "Bearer" not in action


def test_approval_text_for_files_and_urls_is_minimal():
    assert permission_action("Edit", {"file_path": "/home/me/private/project/src/main.cpp"}) == "Edit: main.cpp"
    assert permission_action("Write", {"file_path": "C:\\Users\\me\\notes\\todo.md"}) == "Write: todo.md"
    action = permission_action("WebFetch", {"url": "https://user:pw@example.com:8443/a?token=abc"})
    assert action == "Fetch: example.com"
    assert permission_action("WebSearch", {"query": "my secret query"}) == "Search the web"
    assert permission_action("Edit", {}) == "Edit a file"
    assert permission_action(None, None) == "Permission request"
    assert len(permission_action("Edit", {"file_path": "x" * 300})) <= 32


def test_failure_labels():
    assert failure_label("rate_limit") == "Rate limited"
    assert failure_label("overloaded") == "API overloaded"
    assert failure_label("something_new") == "Task failed"
    assert failure_label(None) == "Task failed"
