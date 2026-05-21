import json
from pathlib import Path
import requests
from rich.console import Console
from rich.panel import Panel

console = Console()
ROOT_DIR = Path(r"C:\Users\ALiam\OneDrive\Desktop\Coding\chat").resolve()
ROOT_DIR.mkdir(parents=True, exist_ok=True)
MODEL = "qwen2.5-coder:7b"
OLLAMA_URL = "http://localhost:11434/api/chat"

def safe_path(user_path: str) -> Path:
    target = (ROOT_DIR / user_path).resolve()
    if ROOT_DIR != target and ROOT_DIR not in target.parents:
        raise ValueError(f"Blocked unsafe path: {user_path}")
    return target

def create_file(path, content=""):
    p = safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Created file: {path}"

def write_file(path, content):
    p = safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote file: {path}"

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

SYSTEM_PROMPT = f"""
You are a local file editor.
Only output valid JSON.
Only allowed actions: create_file, write_file, delete_file, read_file, list_files, done.
Never run commands.

Workspace root: {ROOT_DIR}

JSON examples:
{{"action":"create_file","path":"app.txt","content":"hello"}}
{{"action":"write_file","path":"app.txt","content":"new text"}}
{{"action":"delete_file","path":"app.txt"}}
{{"action":"read_file","path":"app.txt"}}
{{"action":"list_files"}}
{{"action":"done","message":"finished"}}
""".strip()

def ask_model(messages):
    r = requests.post(
        OLLAMA_URL,
        json={"model": MODEL, "messages": messages, "stream": False, "format": "json"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]

def execute_action(obj):
    action = obj.get("action")
    if action == "create_file":
        return create_file(obj["path"], obj.get("content", ""))
    if action == "write_file":
        return write_file(obj["path"], obj.get("content", ""))
    if action == "delete_file":
        return delete_file(obj["path"])
    if action == "read_file":
        return read_file(obj["path"])
    if action == "list_files":
        return list_files()
    if action == "done":
        return obj.get("message", "done")
    return f"Unknown action: {action}"

def main():
    console.print(Panel.fit(f"[bold green]Local File AI[/bold green]\nWorkspace: {ROOT_DIR}"))
    while True:
        task = console.input("\n[bold cyan]Task> [/bold cyan]").strip()
        if task.lower() in {"quit", "exit"}:
            break
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        for _ in range(20):
            raw = ask_model(messages)
            console.print(Panel(raw, title="Model Response", border_style="blue"))
            try:
                obj = json.loads(raw)
            except Exception as e:
                console.print(f"[red]Bad JSON:[/red] {e}")
                break
            result = execute_action(obj)
            console.print(Panel(str(result), title=obj.get("action", "result"), border_style="green"))
            if obj.get("action") == "done":
                break
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"Result: {result}"})

if __name__ == "__main__":
    main()
