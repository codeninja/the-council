# The Council

A council of AI Elder Personas for Claude Code to consult with.

The Council is a **Claude Code MCP plugin** that gives Claude Code access to a panel of AI advisors ("council members"), each with a distinct persona and expertise. Claude Code can present plans for formal approval, ask for advice when stuck, and manage the council membership — all from within a conversation.

---

## Features

- **Formal Presentations** – Claude Code presents a plan/work item; the council deliberates (privately) and issues a verdict: `approved`, `rejected`, or `modified`
- **Consultations** – Claude Code asks for advice; every council member shares their perspective
- **Multi-provider AI backends** – each council member can use a different AI provider:
  - **Anthropic** (Claude) via `ANTHROPIC_API_KEY`
  - **OpenAI** (GPT-4o, o1, …) via `OPENAI_API_KEY`
  - **OpenRouter** (any model) via `OPENROUTER_API_KEY`
  - **Ollama** (local models like Llama 3, Mistral, …) via `OLLAMA_BASE_URL`
- **Threaded Message Queue** – council members communicate via an in-memory event-driven topic queue; all deliberation is threaded and subscribable
- **Human-in-the-loop** – if the council is deadlocked (tie vote), the human is asked to cast the deciding vote
- **Persistent session logs** – every interaction is saved as human-readable Markdown in `.council/sessions/` for user review
- **Modular personas** – council members are defined as Markdown files in `.council/personas/`; add, remove, and customise them freely
- **Four built-in personas** out of the box:
  - **Linus Trivolds** – Chief Pragmatist & Systems Philosopher
  - **Ada Lovelace** – Visionary Mathematician & Algorithm Architect
  - **The Architect** – Senior Systems Designer & Reliability Engineer
  - **The Skeptic** – Security Researcher & Devil's Advocate

---

## Installation

```bash
pip install the-council          # from PyPI (once published)
# or install from source:
pip install -e .
```

Set your API keys for the providers you want to use:
```bash
export ANTHROPIC_API_KEY=sk-ant-...   # for Anthropic Claude members
export OPENAI_API_KEY=sk-...          # for OpenAI GPT members
export OPENROUTER_API_KEY=sk-or-...   # for OpenRouter members
# OLLAMA_BASE_URL defaults to http://localhost:11434/v1 (no key needed)
```

---

## Claude Code Setup

Add the server to your Claude Code MCP configuration (`~/.claude/mcp.json` or project-level `.mcp.json`):

```json
{
  "mcpServers": {
    "the-council": {
      "command": "the-council",
      "env": {
        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
        "OPENAI_API_KEY": "${OPENAI_API_KEY}",
        "OPENROUTER_API_KEY": "${OPENROUTER_API_KEY}",
        "THE_COUNCIL_DIR": ".council",
        "THE_COUNCIL_PROJECT_ROOT": "."
      }
    }
  }
}
```

Or with `uvx` (no install required):
```json
{
  "mcpServers": {
    "the-council": {
      "command": "uvx",
      "args": ["the-council"],
      "env": {
        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}"
      }
    }
  }
}
```

---

## Usage in Claude Code

Once configured, Claude Code can invoke the council using natural language:

> "Present your plan to the council for approval."

> "Consult the council – I'm not sure whether to use REST or GraphQL."

> "Create a new council member with the persona of a Database Expert."

> "Show me the council members."

> "What was the council's decision in session abc12345?"

### Available Tools

| Tool | Description |
|------|-------------|
| `council_present` | Present a plan/work for formal council review (Q&A → deliberation → vote) |
| `council_consult` | Ask the council for advice on a question or problem |
| `council_create_member` | Create a new council member with a custom persona |
| `council_list_members` | List all current council members |
| `council_remove_member` | Remove a council member by name |
| `council_list_sessions` | List past council sessions |
| `council_get_session` | Retrieve the full transcript of a past session |

---

## Session Flow

### Presentation (`council_present`)

```
1. Presentation Phase      Claude Code states its request + evidence
        ↓
2. Q&A Phase               Each member asks ≤3 clarifying questions
        ↓
3. Private Deliberation    Members debate privately (not shown to Claude Code)
        ↓
4. Public Summary          Each member gives a brief public statement
        ↓
5. Voting Phase            Each member votes: approved / rejected / modified
        ↓
6. Verdict                 Majority wins → final decision returned
        ↓ (on tie)
7. Human Consulted         User asked to break the deadlock
        ↓
8. Session Saved           Full transcript written to .council/sessions/<id>.md
```

### Consultation (`council_consult`)

Same as above but skips formal voting in favour of synthesised advice from each member.

---

## Directory Layout

```
.council/
  personas/
    linus_trivolds.md        ← built-in personas (auto-created on first run)
    ada_lovelace.md
    the_architect.md
    the_skeptic.md
    your_custom_elder.md     ← personas you add via council_create_member
  sessions/
    abc12345.md              ← human-readable session transcripts
    abc12345.json            ← machine-readable session data
  index.json                 ← session index (most-recent-first)
```

### Persona File Format

```markdown
# Elder Name

**Title:** Role / Title  
**Model:** claude-opus-4-5  
**Provider:** anthropic  

## Description

A detailed description of this elder's background and expertise.

## Traits

- Trait one
- Trait two

## System Prompt

Additional instructions that shape how this member reasons and responds.
```

The `Provider` field controls which AI backend this member uses.  Supported values: `anthropic`, `openai`, `openrouter`, `ollama`.

---

## Multi-Provider Support

Each council member can run on a different AI provider, configured per persona.

### Creating members via Claude Code

```
# Anthropic (default)
"Create a council member called 'The Pragmatist' using claude-opus-4-5"

# OpenAI
"Create an OpenAI council member using gpt-4o as the pragmatist"
→ provider: openai, model: gpt-4o

# OpenRouter (access hundreds of models)
"Add a council member using openrouter with mistralai/mistral-7b-instruct"
→ provider: openrouter, model: mistralai/mistral-7b-instruct

# Local Ollama
"Add a local council member using ollama with llama3"
→ provider: ollama, model: llama3
```

### Setting up providers

| Provider | Env var | Notes |
|----------|---------|-------|
| `anthropic` | `ANTHROPIC_API_KEY` | Default provider |
| `openai` | `OPENAI_API_KEY` | Any OpenAI model |
| `openrouter` | `OPENROUTER_API_KEY` | Access to 100+ models |
| `ollama` | `OLLAMA_BASE_URL` (optional) | No API key required; defaults to `http://localhost:11434/v1` |

### Mixed-provider council

You can have council members on completely different providers deliberating together:

```markdown
# Linus Trivolds   → provider: anthropic  (claude-opus-4-5)
# Ada Lovelace     → provider: openai     (gpt-4o)
# The Architect    → provider: openrouter (meta-llama/llama-3.1-70b-instruct)
# The Skeptic      → provider: ollama     (llama3)
```

---

## Architecture

```
src/the_council/
  main.py           # MCP server entry point + dispatch_tool()
  council.py        # Council orchestration (present, consult, tally votes)
  member.py         # CouncilMember – provider-agnostic LLM agent
  backends.py       # LLM backends: Anthropic, OpenAI-compatible (OpenAI/OpenRouter/Ollama)
  message_queue.py  # In-memory event-driven pub/sub queue (async)
  session.py        # Session data models (CouncilSession, Vote, Verdict)
  storage.py        # File persistence (.council/sessions/)
  personas.py       # Persona config (provider, model, api_key) + PersonaManager
  defaults.py       # Default persona definitions
```

### Message Queue

Council communication is handled by an **async event-driven message queue** (`EventQueue`). Messages are published to named topics and delivered to all subscribers. Topics are threaded (replies reference a `parent_id`). This allows:

- Per-session Q&A threads: `session:<id>:qa`
- Per-session private deliberation: `session:<id>:deliberation`
- Per-session public statements: `session:<id>:public`

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=the_council --cov-report=term-missing

# Lint
ruff check src tests

# Type check
mypy src
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (required for `anthropic` provider members) |
| `OPENAI_API_KEY` | — | OpenAI API key (required for `openai` provider members) |
| `OPENROUTER_API_KEY` | — | OpenRouter API key (required for `openrouter` provider members) |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama server URL (no key needed for `ollama` members) |
| `THE_COUNCIL_DIR` | `.council` | Path to the council data directory |
| `THE_COUNCIL_PROJECT_ROOT` | `.` | Project root for council member file exploration |

---

## License

MIT
