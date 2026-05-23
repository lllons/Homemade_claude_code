import json
import ast
import re
import subprocess
from collections import Counter
from pathlib import Path
import requests
from rich.console import Console
from rich.panel import Panel

console = Console()
ROOT_DIR = Path(r"C:\Users\ALiam\OneDrive\Desktop\Coding\chat\Dist").resolve()
ROOT_DIR.mkdir(parents=True, exist_ok=True)
MODEL = "qwen2.5-coder:7b"
OLLAMA_URL = "http://localhost:11434/api/chat"  # Update to your Ollama URL if different

# Actions that change files on disk — trigger a prompt cache invalidation
MUTATING_ACTIONS = {"write_file", "write_files", "patch_file", "delete_file", "rename_file"}

# Required keys for each action — used for pre-execution validation
REQUIRED_KEYS = {
    "write_file":  ["path", "content"],
    "write_files": ["files"],
    "patch_file":  ["path", "search", "replace"],
    "delete_file": ["path"],
    "read_file":   ["path"],
    "rename_file": ["path"],
    "run_python":  ["path"],
    "create_folder": ["path"],
    "list_files":  [],
    "done":        [],
}

# --- Path safety ---

def safe_path(user_path):
    target = (ROOT_DIR / user_path).resolve()
    if ROOT_DIR != target and ROOT_DIR not in target.parents:
        raise ValueError(f"Blocked unsafe path: {user_path}")
    return target

# --- File operations ---

def write_file(path, content):
    p = safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    content = content.replace("\\n", "\n").replace("\\t", "\t")
    if p.suffix == '.py':
        try:
            ast.parse(content)
        except SyntaxError as e:
            return f"SyntaxError in generated code: {e}. File NOT written. Please fix."
    p.write_text(content, encoding="utf-8")
    return f"Wrote file: {path}"

def write_files(files):
    """Write multiple files in one action."""
    if not isinstance(files, list):
        return "Error: 'files' must be a list of {path, content} objects"
    results = []
    for entry in files:
        path = entry.get("path", "")
        content = entry.get("content", "")
        if not path:
            results.append("Skipped entry with no path")
            continue
        results.append(write_file(path, content))
    return "\n".join(results)

def patch_file(path, search_text, replace_text):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    content = p.read_text(encoding="utf-8")
    search_text = search_text.replace("\\n", "\n").replace("\\t", "\t")
    replace_text = replace_text.replace("\\n", "\n").replace("\\t", "\t")
    if search_text not in content:
        return f"Search text not found in {path}"
    new_content = content.replace(search_text, replace_text, 1)
    if p.suffix == '.py':
        try:
            ast.parse(new_content)
        except SyntaxError as e:
            return f"SyntaxError introduced by patch: {e}. Patch aborted."
    p.write_text(new_content, encoding="utf-8")
    return f"Patched file: {path}"

def delete_file(path):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    if p.is_dir():
        return f"Refusing to delete directory: {path}"
    confirm = input(f"Delete {path}? (y/N): ").strip().lower()
    if confirm != "y":
        return "Delete cancelled"
    p.unlink()
    return f"Deleted file: {path}"

def read_file(path):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    return p.read_text(encoding="utf-8")

def list_files():
    files = [str(p.relative_to(ROOT_DIR)) for p in ROOT_DIR.rglob("*") if p.is_file()]
    return "\n".join(files) if files else "(empty workspace)"

def create_folder(path):
    p = safe_path(path)
    if p.exists():
        return f"Already exists: {path}"
    p.mkdir(parents=True, exist_ok=True)
    return f"Created folder: {path}"

def run_python(path):
    """Run a Python file in the workspace and return its output (stdout + stderr)."""
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    if p.suffix != ".py":
        return f"Not a Python file: {path}"
    try:
        result = subprocess.run(
            ["python", str(p)],
            capture_output=True, text=True, timeout=10, cwd=ROOT_DIR,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:2000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: script timed out after 10 seconds"
    except FileNotFoundError:
        return "Error: 'python' interpreter not found — try renaming to 'python3' in run_python()"

# --- System prompt (cached; rebuilt only after mutating actions) ---

_cached_prompt = None
_prompt_dirty = True

def invalidate_prompt():
    global _prompt_dirty
    _prompt_dirty = True

def get_system_prompt():
    global _cached_prompt, _prompt_dirty
    if not _prompt_dirty and _cached_prompt is not None:
        return _cached_prompt

    # Feed file contents for small files so the model can patch without read_file round-trips
    files_snapshot = ""
    for p in sorted(ROOT_DIR.rglob("*")):
        if p.is_file() and p.stat().st_size < 8000:
            rel = p.relative_to(ROOT_DIR)
            text = p.read_text(encoding="utf-8", errors="replace")
            files_snapshot += f"\n--- {rel} ---\n{text}\n"

    if not files_snapshot:
        files_snapshot = "(empty workspace)"

    _cached_prompt = f"""
You are a local file editor.
Only output valid JSON — no markdown, no explanation, nothing except a single JSON object.
Allowed actions: write_file, write_files, patch_file, delete_file, read_file, list_files, rename_file, create_folder, run_python, done.
Never run shell commands.

IMPORTANT: When a task requires multiple independent steps that do not depend on each other's results,
batch them in a single response using the "actions" array format. This is faster and preferred.
Only use separate responses when a later action depends on the result of an earlier one (e.g. read then patch).

RULE: Only perform actions the user explicitly asked for. Do not create, modify, or delete files
unless directly instructed. When the task is complete, always output done immediately.

Workspace root: {ROOT_DIR}
Current workspace files and contents:
{files_snapshot}

Single action examples:
{{"action":"write_file","path":"app.py","content":"print('hello')"}}
{{"action":"patch_file","path":"app.py","search":"x = 5","replace":"x = 10"}}
{{"action":"delete_file","path":"app.txt"}}
{{"action":"read_file","path":"app.txt"}}
{{"action":"list_files"}}
{{"action":"rename_file","path":"app.txt","new_name":"app_v2.txt"}}
{{"action":"create_folder","path":"myfolder"}}
{{"action":"run_python","path":"app.py"}}
{{"action":"done","message":"Finished. What should we work on now?"}}

Batch action example (preferred when steps are independent):
{{"actions":[
  {{"action":"write_file","path":"a.py","content":"x=1"}},
  {{"action":"write_file","path":"b.py","content":"x=2"}},
  {{"action":"done","message":"Created both files."}}
]}}
""".strip()

    _prompt_dirty = False
    return _cached_prompt

# --- Action validation ---

def validate_action(obj):
    """Returns an error string if the action is malformed, else None."""
    action = obj.get("action")
    if not action:
        return "Missing 'action' key in JSON"
    if action not in REQUIRED_KEYS:
        return f"Unknown action: '{action}'. Allowed: {list(REQUIRED_KEYS)}"
    missing = [k for k in REQUIRED_KEYS[action] if k not in obj]
    if missing:
        return f"Action '{action}' is missing required keys: {missing}"
    return None

# --- JSON cleanup for common model output mistakes ---

def clean_model_json(raw):
    # Fix "key"= instead of "key": (common LLM slip)
    raw = re.sub(r'"(\w+)"=(?!")', r'"\1":', raw)
    return raw

# --- Model call ---

def ask_model(messages):
    with console.status("[bold cyan]Thinking...[/bold cyan]", spinner="dots"):
        r = requests.post(
            OLLAMA_URL,
            json={"model": MODEL, "messages": messages, "stream": False, "format": "json"},
            headers={"x-api-key": "YOURAPIHERE"},
            timeout=260,
        )
    r.raise_for_status()

    try:
        full = r.json()["message"]["content"]
    except Exception:
        return '{"action":"done","message":"empty response from model"}'

    # Bracket-matching JSON extractor, then clean up common model mistakes
    start = full.find('{')
    if start == -1:
        return '{"action":"done","message":"no JSON object found in response"}'

    brace_count = 0
    for i, char in enumerate(full[start:]):
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                return clean_model_json(full[start : start + i + 1])

    return '{"action":"done","message":"invalid JSON structure"}'

# --- Execute a validated action ---

def execute_action(obj):
    action = obj["action"]
    if action == "write_file":
        return write_file(obj["path"], obj.get("content", ""))
    if action == "write_files":
        return write_files(obj["files"])
    if action == "patch_file":
        return patch_file(obj["path"], obj.get("search", ""), obj.get("replace", ""))
    if action == "delete_file":
        return delete_file(obj["path"])
    if action == "read_file":
        return read_file(obj["path"])
    if action == "list_files":
        return list_files()
    if action == "rename_file":
        old = safe_path(obj["path"])
        new_name = obj.get("new_name") or obj.get("new_path", "")
        new = safe_path(new_name)
        if not old.exists():
            return f"File not found: {obj['path']}"
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        return f"Renamed {obj['path']} to {new_name}"
    if action == "create_folder":
        return create_folder(obj["path"])
    if action == "run_python":
        return run_python(obj["path"])
    if action == "done":
        return obj.get("message", "done")
    return f"Unknown action: {action}"

# --- Build a targeted follow-up message based on what went wrong ---

def build_result_message(action, result):
    result_str = str(result)
    if "SyntaxError" in result_str:
        return (
            f"FAILED with SyntaxError: {result_str}\n"
            "Output a corrected action that fixes the exact syntax error above."
        )
    if action == "patch_file" and "Search text not found" in result_str:
        return (
            f"FAILED: {result_str}\n"
            "The search string didn't match exactly. Use read_file first to get the exact text, then retry patch_file."
        )
    if "not found" in result_str.lower() and action in ("read_file", "run_python", "patch_file"):
        return (
            f"FAILED: {result_str}\n"
            "Check the file path with list_files and retry with the correct path."
        )
    if action in ("list_files", "read_file"):
        return f"Result:\n{result_str}\nIf this answers the user's request, output done now. Do not take any further actions unless asked."
    return f"Result: {result_str}"

# --- Batch summary label ---

ACTION_LABELS = {
    "write_file":    "Write file",
    "write_files":   "Write files",
    "patch_file":    "Patch file",
    "delete_file":   "Delete file",
    "read_file":     "Read file",
    "list_files":    "List files",
    "rename_file":   "Rename file",
    "create_folder": "Create folder",
    "run_python":    "Run Python",
    "done":          "Done",
}

def batch_summary(batch):
    counts = Counter(sub.get("action", "unknown") for sub in batch)
    parts = []
    for action, count in counts.items():
        label = ACTION_LABELS.get(action, action)
        parts.append(f"{label} x{count}" if count > 1 else label)
    return "Running: " + ", ".join(parts)

# --- Context trimming ---

MAX_TURNS = 10  # keep last N assistant/user exchange pairs

def trim_messages(messages):
    """
    Keep: messages[0] (system), messages[1] (original task), last MAX_TURNS*2 messages.
    Injects a notice line so the model knows context was cut.
    """
    fixed = messages[:2]
    turns = messages[2:]
    max_msgs = MAX_TURNS * 2
    if len(turns) <= max_msgs:
        return messages
    kept = turns[-max_msgs:]
    notice = {
        "role": "user",
        "content": f"[{len(turns) - max_msgs} earlier messages trimmed to stay within context limits.]"
    }
    return fixed + [notice] + kept

# --- Main loop ---

def main():
    console.print(Panel.fit(f"[bold green]Local File AI[/bold green]\nWorkspace: {ROOT_DIR}"))
    while True:
        try:
            task = console.input("\n[bold cyan]Task> [/bold cyan]").strip()
        except KeyboardInterrupt:
            console.print("\n[yellow]Use 'quit' or 'exit' to close.[/yellow]")
            continue

        if task.lower() in {"quit", "exit"}:
            break
        if not task:
            continue

        messages = [
            {"role": "system", "content": get_system_prompt()},
            {"role": "user", "content": task},
        ]

        last_raw = ""
        identical_count = 0

        try:
            for _ in range(35):
                # Refresh system prompt only if workspace changed
                messages[0]["content"] = get_system_prompt()

                messages = trim_messages(messages)
                raw = ask_model(messages)

                # Circuit breaker
                if raw == last_raw:
                    identical_count += 1
                else:
                    identical_count = 0
                last_raw = raw

                if identical_count >= 3:
                    console.print(
                        "[bold red]Circuit Breaker Tripped:[/bold red] "
                        "Identical response 3 times in a row. Stopping."
                    )
                    break

                try:
                    obj = json.loads(raw)
                except Exception as e:
                    console.print(f"[red]Bad JSON:[/red] {e}")
                    break

                SILENT_ACTIONS = {"done", "list_files"}

                # --- Batch action path ---
                if "actions" in obj:
                    batch = obj["actions"]
                    if not isinstance(batch, list) or not batch:
                        console.print("[red]Invalid batch:[/red] 'actions' must be a non-empty list.")
                        messages.append({"role": "assistant", "content": raw})
                        messages.append({"role": "user", "content": "INVALID: 'actions' must be a non-empty list. Try again."})
                        continue

                    console.print(f"[dim]{batch_summary(batch)}[/dim]")
                    batch_results = []
                    done_found = False

                    for sub_obj in batch:
                        err = validate_action(sub_obj)
                        if err:
                            console.print(f"[red]Invalid action in batch:[/red] {err}")
                            batch_results.append(f"INVALID: {err}")
                            break

                        sub_action = sub_obj["action"]
                        result = execute_action(sub_obj)

                        if sub_action in MUTATING_ACTIONS:
                            invalidate_prompt()

                        if sub_action == "done":
                            console.print(Panel(str(result), border_style="green"))
                            done_found = True
                            break

                        console.print(Panel(str(result), title=sub_action, border_style="green"))

                        batch_results.append(f"{sub_action}: {result}")

                    if done_found:
                        break

                    combined = "\n".join(batch_results)
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": f"Batch results:\n{combined}\nOutput your next action, or {{\"action\":\"done\",\"message\":\"...\"}}."})
                    continue

                # --- Single action path ---
                validation_error = validate_action(obj)
                if validation_error:
                    # Model output a completion-like JSON with no "action" key — treat as done
                    if validation_error == "Missing 'action' key in JSON" and any(k in obj for k in ("message", "result", "status", "summary")):
                        msg = obj.get("message") or obj.get("result") or obj.get("status") or obj.get("summary", "Done.")
                        console.print(Panel(str(msg), border_style="green"))
                        break
                    console.print(f"[red]Invalid action:[/red] {validation_error}")
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": f"INVALID: {validation_error}. Output a corrected action JSON."})
                    continue

                action = obj["action"]

                if action not in SILENT_ACTIONS:
                    console.print(Panel(raw, title="Model Response", border_style="blue"))

                result = execute_action(obj)
                if action == "done":
                    console.print(Panel(str(result), border_style="green"))
                else:
                    console.print(Panel(str(result), title=action, border_style="green"))

                if action in MUTATING_ACTIONS:
                    invalidate_prompt()

                if action == "done":
                    break

                follow_up = build_result_message(action, result)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": follow_up})

        except KeyboardInterrupt:
            console.print("\n[yellow]Task cancelled. Returning to prompt.[/yellow]")

if __name__ == "__main__":
    main()