# llm-languages

Ask Claude and ChatGPT "What should I have for breakfast?" 10 times each in 10
languages (English, Mandarin, Hindi, Spanish, French, Modern Standard Arabic,
Bengali, Portuguese, Russian, Urdu) and compare the answers.

## Models

| Key | Provider | Model ID |
|---|---|---|
| haiku | Anthropic | claude-haiku-4-5 |
| sonnet | Anthropic | claude-sonnet-5 |
| opus | Anthropic | claude-opus-5 |
| fable | Anthropic | claude-fable-5 |
| sol | OpenAI | gpt-5.6-sol |
| terra | OpenAI | gpt-5.6-terra |
| luna | OpenAI | gpt-5.6-luna |

## Setup

```bash
uv sync
```

The default backend shells out to the `claude` and `codex` CLIs using your
existing logins — no API keys needed. Both sides are stripped as close to a
raw model call as the CLIs allow: the identical minimal system prompt
("You are a helpful assistant.") on both, no tools / MCP servers / settings /
skills on the Claude side, replaced base instructions + no AGENTS.md + no user
config on the Codex side, and both run from an empty directory so no project
files leak into context.

What can't be stripped: the Claude CLI still injects the current date, account
email, and a token-budget reminder; Codex still injects date/timezone, cwd,
and its built-in tool schemas. For a byte-for-byte raw model call, use
`--backend api`.

To use the raw APIs instead, `cp .env.example .env`, fill in
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, and pass `--backend api`.

## Run

```bash
uv run ask.py --dry-run              # see the plan (700 calls)
uv run ask.py --runs 1 --models haiku,luna --languages en   # cheap smoke test
uv run ask.py                        # full experiment
uv run ask.py --backend api          # same, via the APIs
```

Each response is appended to `results/results.jsonl` as it arrives. Re-running
skips combos that already succeeded, so an interrupted run resumes where it
left off. Failed calls are recorded with an `error` field and retried on the
next invocation.

Each JSONL record: `timestamp, provider, model, model_id, language,
language_name, prompt, run, text, stop_reason, input_tokens, output_tokens,
latency_s` (or `error`).
