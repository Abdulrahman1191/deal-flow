"""
One tiny JSON call through claude_agent with the configured provider, to prove
a key works BEFORE assessments depend on it. Writes nothing (no DB, no Copper).

    LLM_PROVIDER=gemini python scripts/llm_preflight.py

Exit 0 = the provider answered with parseable JSON; 1 = it did not (the error
says why: bad key, no credits, empty answer...). The key is read from the
environment like the app reads it; it is never printed.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import claude_agent  # noqa: E402


def main() -> int:
    provider, model = "?", "?"
    try:
        provider, model = claude_agent._provider(), claude_agent.active_model()
        r = claude_agent._chat_completion(
            model=model, max_tokens=200, temperature=0.0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": "Reply with JSON only."},
                      {"role": "user", "content": 'Return {"ok": true}.'}],
        )
        data = json.loads(r.choices[0].message.content)
    except Exception as exc:
        print(f"FAIL provider={provider} model={model}: {type(exc).__name__}: {exc}")
        return 1
    u = r.usage
    print(f"OK provider={provider} model={model} answer={data} finish={r.choices[0].finish_reason} "
          f"prompt_tokens={u.prompt_tokens} completion_tokens={u.completion_tokens}")
    return 0 if data.get("ok") is True else 1


if __name__ == "__main__":
    sys.exit(main())
