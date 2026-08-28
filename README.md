# OUTDATED USE "TMT" INSTEAD


> "I can read, write, and execute. And if you press F2, I can listen, too." 
> — *Local File AI Agent*

# `[ Local File AI Agent ]`

A terminal-based, entirely local AI agent powered by Ollama. It doesn't just write code—it manages files, runs scripts, opens applications, and converses with you in a secure, sandboxed workspace.

---

## `>_ WHAT IT DOES`

You type (or speak) a task in plain English. The agent figures out what files to create or modify, executes the actions, and confirms when done. It loops automatically until the task is complete.

**Example Prompts:**
* `"Make a file called notes.txt with my shopping list inside."`
* `"Create a weather app with index.html, style.css, and script.js."`
* `"Run the python script I just made."`
* `"Open my workspace in File Explorer."`

### `System Capabilities`

| Capability | Description |
| :--- | :--- |
| **File Operations** | Write, append, patch, rename, read, and delete files. |
| **Code Execution** | Automatically compiles and runs C/C++/Java, and executes Python, JS, Go, and more. |
| **Voice Control** | Press **F2** to trigger local Whisper STT. The agent replies using system TTS. |
| **App Launching** | Can open permitted local apps like Notepad and File Explorer directly from the chat. |
| **Action Batching** | Performs multiple independent file operations simultaneously for faster results. |

---

## `>_ REQUIREMENTS`

* **Python:** 3.10+
* **LLM Backend:** [Ollama](https://ollama.com) running locally
* **Model:** A compatible coding model (default: `qwen2.5-coder:7b`)
* **OS:** Windows is recommended for full Voice and App Launching capabilities.

---

## `>_ INITIALIZATION_SEQUENCE`

**0. Set Your Workspace**
Open `agent.py` and modify the `ROOT_DIR` variable to dictate where the agent is allowed to see and edit files. It cannot escape this directory.

**1. Install Ollama & Pull Model**
Download from [ollama.com](https://ollama.com), install it, and pull the default model:
```bash
ollama pull qwen2.5-coder:7b
2. Clone the Repository
```
```Bash
git clone [https://github.com/lllons/Homemade_claude_code.git](https://github.com/lllons/Homemade_claude_code.git)
cd local-file-ai
3. Initialize Virtual Environment
```

```Bash
python -m venv venv
venv\Scripts\activate
4. Install Dependencies
```
```Bash
pip install requests rich
pip install pyttsx3 faster-whisper sounddevice soundfile numpy
5. Start the Engine
Ensure your Ollama server is running:
```
```Bash
ollama serve
Then, execute the agent:
```
```Bash
python agent.py
>_ CONFIGURATION
There is no separate config file. To change settings, open agent.py and modify the constants at the top of the script:
```
```Python
MODEL = "qwen2.5-coder:7b"
OLLAMA_URL = "http://localhost:11434/api/generate"
ROOT_DIR = Path(r"C:\Your\Custom\Path\Here").resolve()
```
>_ HOW IT WORKS
Input: You provide a prompt via text or voice.

Context: The agent sends the prompt and a snapshot of your workspace state to the model.

Action: The model responds with strict, formatted JSON.

Execution: The agent validates the JSON, ensures paths are safe, and executes the filesystem or terminal command.

Feedback: The result is fed back into the model's context window.

Resolution: The loop continues until the model explicitly calls the done or respond action.

>_ SYSTEM_LIMITATIONS
Context Window: Works best on focused tasks. Large, multi-file projects may cause the model to lose context over many iterations.

Air-Gapped: No internet access. It relies entirely on your local filesystem and the knowledge baked into the LLM weights.

Ephemeral Memory: Each session starts fresh. Context from previous terminal sessions is not saved.

License: MIT
