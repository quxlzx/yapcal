"""OpenAI-compatible chat client using only urllib. Works with Ollama,
llama.cpp server, LM Studio. Tries JSON-schema constrained decoding,
falls back to plain JSON + tolerant extraction."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, endpoint: str, model: str | None = None, timeout: int = 300):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout

    # ---- model discovery -------------------------------------------------
    def models(self) -> list[str]:
        req = urllib.request.Request(f"{self.endpoint}/models")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.load(r)
            return [m["id"] for m in data.get("data", [])]
        except Exception as e:
            raise LLMError(f"can't reach {self.endpoint}/models: {e}") from e

    def pick_model(self) -> str:
        if self.model:
            return self.model
        ms = self.models()
        chat = [m for m in ms if not re.search(r"embed|bge|mpnet|minilm", m, re.I)]
        if not chat:
            raise LLMError(f"no chat model found at {self.endpoint}; models: {ms}")
        self.model = chat[0]
        return self.model

    # ---- chat ------------------------------------------------------------
    def chat(self, messages: list[dict], schema: dict | None = None,
             temperature: float = 0.0) -> str:
        body = {
            "model": self.pick_model(),
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if "qwen3" in (self.model or "").lower():
            body["think"] = False  # Ollama extension; llama.cpp ignores unknown keys
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": schema, "strict": True},
            }
        try:
            return self._post(body)
        except urllib.error.HTTPError as e:
            if schema and e.code in (400, 404, 422, 500):
                body.pop("response_format", None)  # backend can't do schema mode
                return self._post(body)
            raise LLMError(f"LLM request failed: HTTP {e.code} {e.read()[:300]!r}") from e
        except TimeoutError as e:
            raise LLMError(
                f"LLM at {self.endpoint} timed out after {self.timeout}s — "
                "it may be running on CPU; try a smaller/faster model") from e
        except (urllib.error.URLError, ConnectionError) as e:
            raise LLMError(
                f"can't reach LLM at {self.endpoint}. Start one:\n"
                "  ollama serve            (Ollama)\n"
                "  llama-server -m model.gguf --port 8080   (llama.cpp)") from e

    def _post(self, body: dict) -> str:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.endpoint}/chat/completions", data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            out = json.load(r)
        try:
            return out["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"unexpected LLM response: {str(out)[:300]}") from e

    def chat_json(self, messages: list[dict], schema: dict | None = None) -> dict | list:
        raw = self.chat(messages, schema=schema)
        return extract_json(raw)


def extract_json(text: str):
    """Tolerant JSON extraction from small-model output."""
    text = text.strip()
    # strip markdown fences
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # find first balanced {...} or [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"model didn't return JSON: {text[:200]!r}")
