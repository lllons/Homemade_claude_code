# Local File AI Agent

A terminal-based AI agent that uses a locally running Ollama model to create, read, write and delete files in a sandboxed workspace directory.

## What it does

You type a task in plain English. The agent figures out what files to create or modify, does it, and confirms when done. It loops automatically until the task is complete.

Example tasks:
- `make a file called notes.txt with my shopping list inside`
- `create a weather app with index.html, style.css and script.js`
- `read config.json and rewrite it with proper formatting`

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally
- A compatible model pulled via Ollama

## Setup

**1 — Install Ollama**

Download from [ollama.com](https://ollama.com) and install it. Then pull a model:

```bash
ollama pull qwen2.5-coder:7b
```

Or any other model you prefer.

**2 — Clone the repo**

```bash
git clone https://github.com/lllons/local-file-ai.git
cd local-file-ai
```

**3 — Create a virtual environment**

```bash
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Mac/Linux
```

**4 — Install dependencies**

```bash
pip install -r requirements.txt
```

**5 — Configure**

Open `config.py` and set your values:

```python
MODEL = "qwen2.5-coder:7b"
OLLAMA_URL = "http://localhost:11434/api/chat"
API_KEY = ""  # only needed for remote Ollama instances
```

For local use you don't need to change anything. If you're pointing at a remote Ollama instance, update the URL and add your key.

**6 — Run it**

Make sure Ollama is running first:

```bash
ollama serve
```

Then in a new terminal:

```bash
python agent.py
```

## Workspace

All files are created inside the `Dist/` folder by default. This folder is gitignored so your generated files never get pushed. You can change the workspace path in `agent.py` by editing the `ROOT_DIR` variable.

## Changing the model

Open `config.py` and edit:

```python
MODEL = "qwen2.5-coder:7b"
```

Any model you've pulled via Ollama will work. Coding models like `qwen2.5-coder` tend to work best for file generation tasks.

## How it works

1. You type a task
2. The agent sends it to the model with a strict system prompt
3. The model responds with a JSON action
4. The agent executes the action on your filesystem
5. The result is sent back to the model
6. This loops until the model says it's done

The agent is sandboxed — it can only touch files inside the workspace directory and will refuse any path that tries to escape it.

## Limitations

- Works best on small focused tasks
- Large multi-file projects may lose context over many iterations
- No internet access — purely local file operations
- Each session starts fresh with no memory of previous runs

## License

MIT
