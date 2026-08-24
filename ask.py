"""Ask Claude and ChatGPT "What should I have for breakfast?" in 10 languages.

Runs every (model x language) combination N times (default 10) and appends
each response as one JSON line to the output file. Safe to re-run: completed
(model, language, run) combos already in the output file are skipped.

Two backends:
  cli (default) - shells out to `claude -p` and `codex exec`, using your
                  existing Claude Code / Codex logins. No API keys needed.
  api           - calls the Anthropic and OpenAI APIs directly. Needs
                  ANTHROPIC_API_KEY and OPENAI_API_KEY (reads .env).

Usage:
    uv run ask.py                          # full run: 7 models x 10 languages x 10 runs
    uv run ask.py --runs 1                 # smoke test, one run per combo
    uv run ask.py --models haiku,luna      # subset of models
    uv run ask.py --languages en,zh        # subset of languages
    uv run ask.py --dry-run                # print the plan, no calls
"""

import argparse
import asyncio
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

QUESTION = "What should I have for breakfast?"

# language key -> (English name, native-language prompt)
LANGUAGES = {
    "en": ("English", "What should I have for breakfast?"),
    "zh": ("Mandarin Chinese", "我早餐应该吃什么？"),
    "hi": ("Hindi", "मुझे नाश्ते में क्या खाना चाहिए?"),
    "es": ("Spanish", "¿Qué debería desayunar?"),
    "fr": ("French", "Que devrais-je manger au petit-déjeuner ?"),
    "ar": ("Modern Standard Arabic", "ماذا يجب أن آكل على الإفطار؟"),
    "bn": ("Bengali", "আমার সকালের নাস্তায় কী খাওয়া উচিত?"),
    "pt": ("Portuguese", "O que eu deveria comer no café da manhã?"),
    "ru": ("Russian", "Что мне съесть на завтрак?"),
    "ur": ("Urdu", "مجھے ناشتے میں کیا کھانا چاہیے؟"),
}

# model key -> (provider, model id understood by both the CLI and the API)
MODELS = {
    "haiku": ("anthropic", "claude-haiku-4-5"),
    "sonnet": ("anthropic", "claude-sonnet-5"),
    "opus": ("anthropic", "claude-opus-5"),
    "fable": ("anthropic", "claude-fable-5"),
    "sol": ("openai", "gpt-5.6-sol"),
    "terra": ("openai", "gpt-5.6-terra"),
    "luna": ("openai", "gpt-5.6-luna"),
}

# Fable/Opus/Sonnet 5 think before answering and thinking tokens count toward
# max_tokens, so leave generous headroom; the visible answer itself is short.
CLAUDE_MAX_TOKENS = 8000

CLI_TIMEOUT_S = 300

# Same minimal system prompt on both sides. Codex cannot run with an empty
# base prompt, so a shared one-liner is the closest to a raw call both CLIs
# can be pushed to.
MINIMAL_SYSTEM = "You are a helpful assistant."

# Run both CLIs from an empty directory so no project files leak into context.
EMPTY_DIR = Path(tempfile.gettempdir()) / "llm-languages-empty"


# ---------------------------------------------------------------- CLI backend

async def _run_subprocess(cmd: list[str]) -> tuple[bytes, bytes]:
    EMPTY_DIR.mkdir(exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=EMPTY_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=CLI_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"timed out after {CLI_TIMEOUT_S}s: {' '.join(cmd[:3])}")
    if proc.returncode != 0:
        detail = (err or out).decode(errors="replace").strip()
        raise RuntimeError(f"exit {proc.returncode}: {detail[:300]}")
    return out, err


async def ask_claude_cli(model_id: str, prompt: str) -> dict:
    # Stripped as far as the CLI allows: minimal system prompt, no tools, no
    # MCP servers, no user/project settings, no skills. Residual context the
    # CLI still injects: current date, account email, token-budget reminder.
    out, _ = await _run_subprocess([
        "claude", "-p", prompt,
        "--model", model_id,
        "--output-format", "json",
        "--system-prompt", MINIMAL_SYSTEM,
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--disable-slash-commands",
    ])
    data = json.loads(out)
    usage = data.get("usage") or {}
    return {
        "text": data.get("result", ""),
        "stop_reason": data.get("subtype"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }


async def ask_codex_cli(model_id: str, prompt: str) -> dict:
    with tempfile.NamedTemporaryFile(
        mode="r", suffix=".txt", prefix="codex-out-", delete=False
    ) as tf:
        out_file = Path(tf.name)
    try:
        # Stripped as far as the CLI allows: base instructions replaced with
        # the minimal system prompt, no AGENTS.md, no user config, empty cwd.
        # Residual context codex still injects: date/timezone, cwd, its
        # built-in tool schemas (shell etc.).
        await _run_subprocess([
            "codex", "exec", prompt,
            "-m", model_id,
            "-s", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color", "never",
            "--ignore-user-config",
            "-c", f"instructions={MINIMAL_SYSTEM}",
            "-c", "project_doc_max_bytes=0",
            "-c", "tools.web_search=false",
            "-C", str(EMPTY_DIR),
            "-o", str(out_file),
        ])
        text = out_file.read_text().strip()
    finally:
        out_file.unlink(missing_ok=True)
    return {
        "text": text,
        "stop_reason": "completed",
        "input_tokens": None,
        "output_tokens": None,
    }


# ---------------------------------------------------------------- API backend

def make_api_askers():
    from dotenv import load_dotenv

    load_dotenv()
    import anthropic
    import openai

    anthropic_client = anthropic.AsyncAnthropic(max_retries=3)
    openai_client = openai.AsyncOpenAI(max_retries=3)

    async def ask_anthropic(model_id: str, prompt: str) -> dict:
        response = await anthropic_client.messages.create(
            model=model_id,
            max_tokens=CLAUDE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return {
            "text": text,
            "stop_reason": response.stop_reason,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

    async def ask_openai(model_id: str, prompt: str) -> dict:
        response = await openai_client.responses.create(model=model_id, input=prompt)
        usage = response.usage
        return {
            "text": response.output_text,
            "stop_reason": response.status,
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
        }

    return {"anthropic": ask_anthropic, "openai": ask_openai}


# --------------------------------------------------------------------- runner

def load_completed(out_path: Path) -> set:
    done = set()
    if out_path.exists():
        with out_path.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not rec.get("error"):
                    done.add((rec["model"], rec["language"], rec["run"]))
    return done


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=["cli", "api"], default="cli",
                        help="cli = claude/codex CLIs on your logins (default); api = direct API calls")
    parser.add_argument("--runs", type=int, default=10, help="runs per (model, language) combo")
    parser.add_argument("--models", default=",".join(MODELS), help="comma-separated model keys")
    parser.add_argument("--languages", default=",".join(LANGUAGES), help="comma-separated language keys")
    parser.add_argument("--out", default="results/results.jsonl", help="output JSONL file")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="max in-flight requests per provider (default: 4 for cli, 8 for api)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without making any calls")
    args = parser.parse_args()

    model_keys = [m.strip() for m in args.models.split(",") if m.strip()]
    lang_keys = [l.strip() for l in args.languages.split(",") if l.strip()]
    for m in model_keys:
        if m not in MODELS:
            parser.error(f"unknown model {m!r} (choose from {', '.join(MODELS)})")
    for l in lang_keys:
        if l not in LANGUAGES:
            parser.error(f"unknown language {l!r} (choose from {', '.join(LANGUAGES)})")

    out_path = Path(args.out)
    done = load_completed(out_path)
    tasks_spec = [
        (m, l, r)
        for m in model_keys
        for l in lang_keys
        for r in range(1, args.runs + 1)
        if (m, l, r) not in done
    ]

    total = len(model_keys) * len(lang_keys) * args.runs
    print(f"backend={args.backend}  {total} total calls; "
          f"{total - len(tasks_spec)} already done; {len(tasks_spec)} to go")
    if args.dry_run or not tasks_spec:
        if args.dry_run:
            for m, l, r in tasks_spec[:20]:
                print(f"  {m:8s} {l}  run {r}")
            if len(tasks_spec) > 20:
                print(f"  ... and {len(tasks_spec) - 20} more")
        return 0

    if args.backend == "cli":
        askers = {
            "anthropic": ask_claude_cli,
            "openai": ask_codex_cli,
        }
        concurrency = args.concurrency or 4
    else:
        askers = make_api_askers()
        concurrency = args.concurrency or 8

    out_path.parent.mkdir(parents=True, exist_ok=True)
    semaphores = {
        "anthropic": asyncio.Semaphore(concurrency),
        "openai": asyncio.Semaphore(concurrency),
    }
    write_lock = asyncio.Lock()
    progress = {"ok": 0, "err": 0}

    async def run_one(model_key: str, lang_key: str, run: int) -> None:
        provider, model_id = MODELS[model_key]
        lang_name, prompt = LANGUAGES[lang_key]
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backend": args.backend,
            "provider": provider,
            "model": model_key,
            "model_id": model_id,
            "language": lang_key,
            "language_name": lang_name,
            "prompt": prompt,
            "run": run,
        }
        async with semaphores[provider]:
            start = time.monotonic()
            try:
                result = await askers[provider](model_id, prompt)
                record.update(result)
                progress["ok"] += 1
            except Exception as e:
                record["error"] = f"{type(e).__name__}: {e}"
                progress["err"] += 1
            record["latency_s"] = round(time.monotonic() - start, 2)
        async with write_lock:
            with out_path.open("a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        n = progress["ok"] + progress["err"]
        if "error" in record:
            status = "ERROR " + record["error"][:80]
        else:
            status = f"{len(record['text'])} chars in {record['latency_s']}s"
        print(f"[{n}/{len(tasks_spec)}] {model_key:8s} {lang_key} run {run:2d}  {status}")

    await asyncio.gather(*(run_one(m, l, r) for m, l, r in tasks_spec))
    print(f"\nDone: {progress['ok']} succeeded, {progress['err']} failed -> {out_path}")
    return 1 if progress["err"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
