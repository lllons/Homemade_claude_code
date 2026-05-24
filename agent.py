import json
import ast
import re
import subprocess
import platform
import tempfile
import threading
import sys
import site
from collections import Counter

# Make user site-packages visible when running inside a venv whose
# site-packages directory is not writable (pip falls back to user install).
_user_site = site.getusersitepackages()
if isinstance(_user_site, str) and _user_site not in sys.path:
    sys.path.insert(0, _user_site)

# pywin32 (needed by pyttsx3) installs its DLLs to a subfolder that the
# venv's DLL loader can't see by default — register it explicitly.
import os as _os
_pywin32_dlls = _os.path.join(_user_site, "pywin32_system32")
if _os.path.isdir(_pywin32_dlls) and hasattr(_os, "add_dll_directory"):
    _os.add_dll_directory(_pywin32_dlls)
from pathlib import Path
import requests
from rich.console import Console
from rich.panel import Panel

console = Console()
ROOT_DIR = Path(r"C:\Users\ALiam\OneDrive\Desktop\Coding\chat\Dist").resolve()
ROOT_DIR.mkdir(parents=True, exist_ok=True)
MODEL = "qwen2.5-coder:7b"
OLLAMA_URL = "http://localhost:11434/api/generate"

# Actions that change files on disk — trigger a prompt cache invalidation
MUTATING_ACTIONS = {"write_file", "append_file", "write_files", "patch_file", "delete_file", "rename_file"}

# Required keys for each action — used for pre-execution validation
REQUIRED_KEYS = {
    "write_file":  ["path", "content"],
    "append_file": ["path", "content"],
    "write_files": ["files"],
    "patch_file":  ["path", "search", "replace"],
    "delete_file": ["path"],
    "read_file":   ["path"],
    "rename_file": ["path"],
    "run_python":  ["path"],
    "run_file":    ["path"],
    "create_folder": ["path"],
    "open_app":    ["app"],
    "list_files":  [],
    "respond":     ["message"],
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

def append_file(path, content):
    """Append content to the end of an existing file."""
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    content = content.replace("\\n", "\n").replace("\\t", "\t")
    existing = p.read_text(encoding="utf-8")
    separator = "\n" if existing and not existing.endswith("\n") else ""
    p.write_text(existing + separator + content, encoding="utf-8")
    return f"Appended to: {path}"

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

# Interpreted language runners — maps extension to command template
RUNNERS = {
    ".py":   ["python",    "{file}"],
    ".js":   ["node",      "{file}"],
    ".rb":   ["ruby",      "{file}"],
    ".php":  ["php",       "{file}"],
    ".lua":  ["lua",       "{file}"],
    ".pl":   ["perl",      "{file}"],
    ".r":    ["Rscript",   "{file}"],
    ".go":   ["go", "run", "{file}"],
    ".ts":   ["npx", "--yes", "ts-node", "{file}"],
}

def _run_cmd(cmd):
    """Run a command, return stdout+stderr capped at 2000 chars."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, cwd=ROOT_DIR
        )
        output = (result.stdout + result.stderr).strip()
        return output[:2000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 10 seconds"
    except FileNotFoundError:
        return f"Error: '{cmd[0]}' not found — is it installed and on PATH?"

def run_file(path):
    """Run any supported code file in the workspace sandbox."""
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"

    ext = p.suffix.lower()
    on_windows = platform.system() == "Windows"

    # Interpreted languages
    if ext in RUNNERS:
        cmd = [c.replace("{file}", str(p)) for c in RUNNERS[ext]]
        return _run_cmd(cmd)

    # C
    if ext == ".c":
        out = p.with_suffix(".exe" if on_windows else "")
        compile_out = _run_cmd(["gcc", str(p), "-o", str(out)])
        if not out.exists():
            return f"Compile error:\n{compile_out}"
        run_out = _run_cmd([str(out)])
        try: out.unlink()
        except: pass
        return run_out

    # C++
    if ext == ".cpp":
        out = p.with_suffix(".exe" if on_windows else "")
        compile_out = _run_cmd(["g++", str(p), "-o", str(out)])
        if not out.exists():
            return f"Compile error:\n{compile_out}"
        run_out = _run_cmd([str(out)])
        try: out.unlink()
        except: pass
        return run_out

    # Java
    if ext == ".java":
        compile_out = _run_cmd(["javac", str(p)])
        if "error" in compile_out.lower():
            return f"Compile error:\n{compile_out}"
        class_file = p.with_suffix(".class")
        run_out = _run_cmd(["java", "-cp", str(p.parent), p.stem])
        try: class_file.unlink()
        except: pass
        return run_out

    supported = ", ".join(list(RUNNERS) + [".c", ".cpp", ".java"])
    return f"Unsupported file type: '{ext}'. Supported: {supported}"

def run_python(path):
    """Alias kept for backward compatibility."""
    return run_file(path)

# --- App registry & open_app action ---
#
# To permit a new app, add an entry here. The model can only launch apps
# that appear in this dict — everything else is blocked.
#
# Keys per entry:
#   exe          : executable name (must be on PATH) or full path
#   description  : shown in the system prompt so the model knows what it can open
#   accepts_path : True if the app takes a file path as its first argument
#   accepts_url  : True if the app takes a URL as its first argument (e.g. browsers)

APP_REGISTRY = {
    "notepad": {
        "exe":          "notepad.exe",
        "description":  "Windows Notepad — opens .txt and other text files",
        "accepts_path": True,
        "accepts_url":  False,
    },
    # Uncomment to add more apps as permissions are granted:
    # "browser": {
    #     "exe":          "start",
    #     "description":  "Default web browser",
    #     "accepts_path": False,
    #     "accepts_url":  True,
    # },
    # "vscode": {
    #     "exe":          "code",
    #     "description":  "Visual Studio Code editor",
    #     "accepts_path": True,
    #     "accepts_url":  False,
    # },
    "explorer": {
        "exe":          "explorer.exe",
        "description":  "Windows File Explorer — opens folder with the file selected and highlighted",
        "accepts_path": True,
        "accepts_url":  False,
        "path_prefix":  "/select,",  # makes Explorer highlight the file, not just open its folder
    },
}

def open_app(app_name, file_path=None, url=None):
    """Launch a permitted app, optionally with a file or URL."""
    key = app_name.lower().strip()
    if key not in APP_REGISTRY:
        permitted = ", ".join(APP_REGISTRY.keys())
        return f"App '{app_name}' is not permitted. Permitted apps: {permitted}"

    cfg = APP_REGISTRY[key]
    cmd = [cfg["exe"]]

    if file_path:
        if not cfg["accepts_path"]:
            return f"'{key}' does not accept file paths."
        p = safe_path(file_path)
        if not p.exists():
            return f"File not found: {file_path}"
        prefix = cfg.get("path_prefix", "")
        cmd.append(f"{prefix}{str(p)}" if prefix else str(p))
    elif url:
        if not cfg["accepts_url"]:
            return f"'{key}' does not accept URLs."
        cmd.append(url)

    try:
        # Popen = non-blocking; the app opens and we continue immediately
        subprocess.Popen(cmd)
        target = file_path or url or ""
        return f"Opened {target} in {key}" if target else f"Launched {key}"
    except FileNotFoundError:
        return f"Could not find '{cfg['exe']}' — is it installed and on PATH?"
    except Exception as e:
        return f"Error launching {key}: {e}"

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

    permitted_apps = ", ".join(
        f"{k} ({v['description']})" for k, v in APP_REGISTRY.items()
    ) or "none"

    _cached_prompt = f"""
You are a helpful AI assistant and local file manager. You can chat naturally AND manage files.
Your respond messages are spoken aloud — write them as natural speech, not technical jargon.
Only output valid JSON. No markdown, no explanation, nothing except a single JSON object.

Allowed actions: write_file, append_file, write_files, patch_file, delete_file, read_file, list_files, rename_file, create_folder, run_file, open_app, respond.
Permitted apps for open_app: {permitted_apps}
Never run shell commands.

RESPONDING RULES:
- Always end every turn with a "respond" action containing a natural spoken reply.
- The respond message is read aloud — make it conversational, friendly, and complete.
- Include the actual answer in the respond message. Examples:
  BAD:  {{"action":"respond","message":"I checked the files."}}
  GOOD: {{"action":"respond","message":"You have 4 files: time finder, tomato salad, ugli fruit, and crossy road."}}
- For pure conversation with no file work needed, just output a respond action directly.
- Only perform file actions the user explicitly asked for. Never create or edit files unprompted.

BATCHING: When steps are independent, batch them with the actions array. End every batch with respond.

Workspace root: {ROOT_DIR}
Current workspace files and contents:
{files_snapshot}

Examples:
{{"action":"respond","message":"You have 3 files in your workspace."}}
{{"action":"write_file","path":"app.py","content":"print('hello')"}}
{{"action":"append_file","path":"notes.txt","content":"New line at the bottom."}}
{{"action":"list_files"}}
{{"action":"run_file","path":"app.py"}}
{{"action":"create_folder","path":"myfolder"}}
{{"action":"open_app","app":"notepad","path":"notes.txt"}}
{{"actions":[
  {{"action":"write_file","path":"a.py","content":"x=1"}},
  {{"action":"write_file","path":"b.py","content":"x=2"}},
  {{"action":"respond","message":"Done, I created a dot py and b dot py for you."}}
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
            headers={"x-api-key": "YourOllamaAPIKeyHere"},  # if your Ollama instance requires an API key, set it here; otherwise omit this header
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
    if action == "append_file":
        return append_file(obj["path"], obj.get("content", ""))
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
    if action in ("run_python", "run_file"):
        return run_file(obj["path"])
    if action == "open_app":
        return open_app(obj["app"], file_path=obj.get("path"), url=obj.get("url"))
    if action in ("respond", "done"):
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
        return f"Result:\n{result_str}\nNow output a respond action that naturally answers the user's question using this data. Speak the actual answer, not just that you checked."
    return f"Result: {result_str}"

# --- Voice (STT + TTS) -------------------------------------------------------
# Fully local: faster-whisper for speech-to-text, pyttsx3 for text-to-speech.
# If any dependency is missing the app falls back to text-only silently.

VOICE_ENABLED   = False   # flipped to True if all deps load successfully
WHISPER_MODEL   = "base"  # tiny | base | small | medium  (auto-downloaded on first use)
SILENCE_THRESH  = 0.015   # RMS level below which audio is considered silence
SILENCE_SECS    = 1.8     # seconds of silence before recording stops
SAMPLE_RATE     = 16000   # Hz — Whisper expects 16 kHz mono

_whisper_model  = None    # lazy-loaded on first listen()
_voice_lock     = threading.Lock()

def _load_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper_model

try:
    import sounddevice as sd
    import soundfile as sf
    import numpy as np
    from faster_whisper import WhisperModel
    VOICE_ENABLED = True
except Exception as _voice_import_err:
    print(f"[Voice disabled] Import failed: {_voice_import_err}")

def speak(text: str):
    """Speak text aloud using Windows System.Speech via PowerShell (no packages needed)."""
    if not VOICE_ENABLED or sys.platform != "win32":
        return
    # Sanitise text: remove single quotes and newlines to avoid PS injection
    safe = text.replace("'", "").replace("\n", ". ").replace('"', "")
    ps_cmd = (
        "Add-Type -AssemblyName System.Speech; "
        f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = 2; "
        f"$s.Speak('{safe}')"
    )
    # Run in a daemon thread so TTS never blocks the main loop
    threading.Thread(
        target=lambda: subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        ),
        daemon=True
    ).start()

def listen() -> str:
    """
    Record from the default mic until silence, transcribe with Whisper.
    Returns the transcribed string, or "" on failure.
    """
    if not VOICE_ENABLED:
        return ""

    import sounddevice as sd
    import numpy as np

    chunk_samples = int(SAMPLE_RATE * 0.1)      # 100 ms chunks
    max_silence   = int(SILENCE_SECS / 0.1)     # chunks of silence before stopping
    min_speech    = int(0.5 / 0.1)              # at least 500 ms of speech required

    chunks         = []
    silence_count  = 0
    speech_started = False

    console.print("[bold magenta]🎤 Listening...[/bold magenta] (speak now, stops after silence)")

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
            while True:
                chunk, _ = stream.read(chunk_samples)
                chunks.append(chunk.copy())
                rms = float(np.sqrt(np.mean(chunk ** 2)))

                if rms >= SILENCE_THRESH:
                    speech_started = True
                    silence_count  = 0
                else:
                    if speech_started:
                        silence_count += 1
                    # Stop if we have enough speech followed by enough silence
                    if speech_started and silence_count >= max_silence:
                        break
                    # Safety cap — never record more than 30 seconds
                    if len(chunks) > int(30 / 0.1):
                        break

        if not speech_started or len(chunks) < min_speech:
            console.print("[dim]No speech detected.[/dim]")
            return ""

        audio = np.concatenate(chunks, axis=0).flatten()

        # Write to a temp WAV and pass to Whisper
        import soundfile as sf
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        sf.write(tmp_path, audio, SAMPLE_RATE)

        with console.status("[bold magenta]Transcribing...[/bold magenta]", spinner="dots"):
            model   = _load_whisper()
            segs, _ = model.transcribe(tmp_path, language="en", beam_size=1)
            text    = " ".join(s.text.strip() for s in segs).strip()

        import os
        try: os.unlink(tmp_path)
        except: pass

        return text

    except Exception as e:
        console.print(f"[red]Voice error:[/red] {e}")
        return ""

# ---------------------------------------------------------------------------

# --- Batch summary label ---

ACTION_LABELS = {
    "write_file":    "Write file",
    "append_file":   "Append to file",
    "write_files":   "Write files",
    "patch_file":    "Patch file",
    "delete_file":   "Delete file",
    "read_file":     "Read file",
    "list_files":    "List files",
    "rename_file":   "Rename file",
    "create_folder": "Create folder",
    "run_python":    "Run file",
    "run_file":      "Run file",
    "open_app":      "Open app",
    "respond":       "Respond",
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

# --- Smart task input (F2 = voice trigger, msvcrt-based, Windows only) -----

# F2 extended key code via msvcrt.getwch()
_F2_CODE = 60  # ord of second byte after the 0x00/0xe0 prefix

def smart_input() -> str:
    """
    Replacement for console.input() at the Task prompt.
    - Type normally and press Enter as usual.
    - Press F2 at any point (even mid-word) to discard typed text and trigger voice.
    - Ctrl+C raises KeyboardInterrupt as normal.
    Falls back to plain console.input() on non-Windows.
    """
    if sys.platform != "win32":
        return console.input("\n[bold cyan]Task> [/bold cyan]").strip()

    import msvcrt
    sys.stdout.write("\n\033[1;36mTask (F2=voice)> \033[0m")
    sys.stdout.flush()

    buf = []
    while True:
        ch = msvcrt.getwch()

        if ch in ("\x00", "\xe0"):          # Extended / function key prefix
            ext = msvcrt.getwch()
            if ord(ext) == _F2_CODE:          # F2 pressed
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "__VOICE__"
            continue                           # Ignore other special keys

        if ch == "\r":                        # Enter
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(buf).strip()

        if ch == "\x03":                      # Ctrl+C
            sys.stdout.write("\n")
            raise KeyboardInterrupt

        if ch == "\x08":                      # Backspace
            if buf:
                buf.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue

        if ord(ch) >= 32:                      # Printable character
            buf.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()

# ---------------------------------------------------------------------------

# --- Main loop ---

def main():
    voice_status = "[bold magenta]Voice ON[/bold magenta] — press [bold]F2[/bold] to speak" if VOICE_ENABLED else "[dim]Voice OFF — pip install pyttsx3 faster-whisper sounddevice soundfile numpy[/dim]"
    console.print(Panel.fit(f"[bold green]Local File AI[/bold green]\nWorkspace: {ROOT_DIR}\n{voice_status}"))
    while True:
        try:
            task = smart_input()
        except KeyboardInterrupt:
            console.print("\n[yellow]Use 'quit' or 'exit' to close.[/yellow]")
            continue

        if task.lower() in {"quit", "exit"}:
            break
        if task == "__VOICE__":
            if VOICE_ENABLED:
                task = listen()
                if not task:
                    continue
                console.print(f"[bold magenta]You said:[/bold magenta] {task}")
            else:
                console.print("[yellow]Voice not available. Install: pip install pyttsx3 faster-whisper sounddevice soundfile numpy[/yellow]")
                continue
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

                SILENT_ACTIONS = {"done", "respond", "list_files"}

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

                        if sub_action in ("done", "respond"):
                            console.print(Panel(str(result), border_style="green"))
                            speak(str(result))
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
                        speak(str(msg))
                        break
                    console.print(f"[red]Invalid action:[/red] {validation_error}")
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": f"INVALID: {validation_error}. Output a corrected action JSON."})
                    continue

                action = obj["action"]

                if action not in SILENT_ACTIONS:
                    console.print(Panel(raw, title="Model Response", border_style="blue"))

                result = execute_action(obj)
                if action in ("done", "respond"):
                    console.print(Panel(str(result), border_style="green"))
                    speak(str(result))
                else:
                    console.print(Panel(str(result), title=action, border_style="green"))

                if action in MUTATING_ACTIONS:
                    invalidate_prompt()

                if action in ("done", "respond"):
                    break

                follow_up = build_result_message(action, result)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": follow_up})

        except KeyboardInterrupt:
            console.print("\n[yellow]Task cancelled. Returning to prompt.[/yellow]")

if __name__ == "__main__":
    main()