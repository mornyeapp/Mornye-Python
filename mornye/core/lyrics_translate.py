"""Lyrics translation with the user's own AI provider.

Same provider set as the mobile app: OpenAI, OpenRouter, Google Gemini,
Anthropic, or any OpenAI-compatible server ("custom": Ollama, LM Studio…).
There is no Mornye-run service in the middle: the key is the user's, it is kept
in its own 0600 file next to the settings (never in gui-settings.json, which
the frontend reads wholesale) and requests go straight to the provider.

The translation keeps the shape of the original: every line is translated on
its own, so line count, blank lines and LRC timestamps survive and the result
can be saved as a synced ``.lrc`` next to the track.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from .atomic_io import write_json_atomic
from .errors import ErrorKind, MornyeError
from .paths import cache_path

logger = logging.getLogger(__name__)

PROVIDERS: dict[str, dict[str, object]] = {
    "openai": {"label": "OpenAI", "model": "gpt-6-luna", "key": True},
    "openrouter": {
        "label": "OpenRouter",
        "model": "google/gemini-3.5-flash-lite",
        "key": True,
    },
    "gemini": {"label": "Google Gemini", "model": "gemini-3.5-flash-lite", "key": True},
    "anthropic": {"label": "Anthropic Claude", "model": "claude-haiku-5-5", "key": True},
    "custom": {"label": "Custom (OpenAI-compatible)", "model": "", "key": False},
}

LANGUAGES: tuple[tuple[str, str], ...] = (
    ("en", "English"),
    ("it", "Italiano"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("pt", "Português"),
    ("nl", "Nederlands"),
    ("pl", "Polski"),
    ("ru", "Русский"),
    ("tr", "Türkçe"),
    ("ar", "العربية"),
    ("hi", "हिन्दी"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("zh", "中文"),
)

_CHUNK = 60
_TIMEOUT = 90.0
_TS_RE = re.compile(r"^((?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\])+)\s*")
_WORD_TAG_RE = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
_META_RE = re.compile(r"^\[[a-zA-Z#]+:[^\]]*\]\s*$")


@dataclass(frozen=True)
class AiConfig:
    provider: str = "openai"
    model: str = ""
    api_key: str = ""
    base_url: str = ""

    @property
    def effective_model(self) -> str:
        return self.model or str(PROVIDERS.get(self.provider, {}).get("model") or "")

    @property
    def configured(self) -> bool:
        spec = PROVIDERS.get(self.provider)
        if spec is None or not self.effective_model:
            return False
        if spec["key"] and not self.api_key:
            return False
        return self.provider != "custom" or bool(self.base_url)


def _config_file() -> Path:
    return cache_path("lyrics-ai.json")


def load_config() -> AiConfig:
    try:
        raw = json.loads(_config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AiConfig()
    if not isinstance(raw, dict):
        return AiConfig()
    provider = str(raw.get("provider") or "openai")
    return AiConfig(
        provider=provider if provider in PROVIDERS else "openai",
        model=str(raw.get("model") or ""),
        api_key=str(raw.get("api_key") or ""),
        base_url=str(raw.get("base_url") or ""),
    )


def save_config(
    provider: str, model: str, base_url: str, api_key: str | None
) -> AiConfig:
    """Stores the config. `api_key=None` keeps the stored key (the form never
    receives it back, so an untouched field must not erase it)."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    current = load_config()
    cfg = AiConfig(
        provider=provider,
        model=model.strip(),
        api_key=current.api_key if api_key is None else api_key.strip(),
        base_url=normalize_base_url(base_url),
    )
    write_json_atomic(
        _config_file(),
        {
            "provider": cfg.provider,
            "model": cfg.model,
            "api_key": cfg.api_key,
            "base_url": cfg.base_url,
        },
        private=True,
    )
    return cfg


def normalize_base_url(value: str) -> str:
    url = (value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)]
    return url


# ── Lyrics ↔ translatable lines ─────────────────────────────────────────────


def split_lyrics(lyrics: str) -> list[tuple[str, str]]:
    """(timestamp prefix, text) per line. Metadata tags ([ar:…]) come back as
    a prefix with no text so they pass through untouched."""
    out: list[tuple[str, str]] = []
    for line in lyrics.replace("\r\n", "\n").split("\n"):
        if _META_RE.match(line.strip()):
            out.append((line.strip(), ""))
            continue
        m = _TS_RE.match(line)
        prefix = m.group(1) if m else ""
        text = _WORD_TAG_RE.sub("", line[m.end() :] if m else line).strip()
        out.append((prefix, text))
    return out


def join_lyrics(lines: list[tuple[str, str]], translated: dict[int, str]) -> str:
    rows = []
    for i, (prefix, text) in enumerate(lines):
        body = translated.get(i, text)
        rows.append(f"{prefix}{body}" if prefix and body else (prefix or body))
    return "\n".join(rows)


# ── Providers ───────────────────────────────────────────────────────────────

_SYSTEM = (
    "You translate song lyrics. You receive a JSON array of strings, one per "
    "lyric line. Reply with ONLY a JSON array of the same length: element i is "
    "the translation of element i into {language}. Keep the tone, slang and "
    "line breaks; do not merge, split, add or omit lines; leave a line "
    "unchanged if it is already in {language} or has no words."
)


def _explain(resp: httpx.Response) -> MornyeError:
    code = resp.status_code
    if code in (401, 403):
        msg = "The provider rejected the API key"
    elif code == 404:
        msg = "The model was not found — check the model name"
    elif code == 429:
        msg = "Rate limit or quota reached — try again later"
    else:
        msg = f"The provider returned HTTP {code}"
    return MornyeError(ErrorKind.METADATA_ERROR, msg)


async def _chat(
    client: httpx.AsyncClient, cfg: AiConfig, system: str, user: str
) -> str:
    model = cfg.effective_model
    if cfg.provider == "gemini":
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        resp = await client.post(
            url,
            headers={"x-goog-api-key": cfg.api_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.2},
            },
        )
        if resp.status_code >= 400:
            raise _explain(resp)
        parts = resp.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    if cfg.provider == "anthropic":
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": model,
                "max_tokens": 8192,
                "temperature": 0.2,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        if resp.status_code >= 400:
            raise _explain(resp)
        return "".join(
            b.get("text", "") for b in resp.json()["content"] if b.get("type") == "text"
        )

    base = {
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    }.get(cfg.provider, cfg.base_url)
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    resp = await client.post(
        f"{base}/chat/completions",
        headers=headers,
        json={
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
    )
    if resp.status_code >= 400:
        raise _explain(resp)
    return resp.json()["choices"][0]["message"]["content"] or ""


def _parse_array(text: str, expected: int) -> list[str] | None:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, list) or len(data) != expected:
        return None
    return [str(x) for x in data]


async def translate_lyrics_async(
    lyrics: str,
    language: str,
    cfg: AiConfig | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """`lyrics` (plain or LRC) translated into `language` (a name or code)."""
    cfg = cfg or load_config()
    if not cfg.configured:
        raise MornyeError(
            ErrorKind.METADATA_ERROR,
            "Set up an AI provider and API key first",
        )
    if not lyrics or not lyrics.strip():
        raise MornyeError(ErrorKind.METADATA_ERROR, "There are no lyrics to translate")

    name = dict(LANGUAGES).get(language, language)
    lines = split_lyrics(lyrics)
    todo = [i for i, (_, text) in enumerate(lines) if text]
    translated: dict[int, str] = {}
    system = _SYSTEM.format(language=name)

    own = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        for start in range(0, len(todo), _CHUNK):
            idx = todo[start : start + _CHUNK]
            payload = json.dumps([lines[i][1] for i in idx], ensure_ascii=False)
            result = None
            for _attempt in range(2):
                try:
                    reply = await _chat(http, cfg, system, payload)
                except httpx.TimeoutException as exc:
                    raise MornyeError(
                        ErrorKind.METADATA_ERROR, "The provider timed out"
                    ) from exc
                except httpx.HTTPError as exc:
                    raise MornyeError(
                        ErrorKind.METADATA_ERROR, f"Could not reach the provider: {exc}"
                    ) from exc
                result = _parse_array(reply, len(idx))
                if result is not None:
                    break
            if result is None:
                raise MornyeError(
                    ErrorKind.METADATA_ERROR,
                    "The model did not return one translation per line",
                )
            translated.update(zip(idx, result, strict=True))
    finally:
        if own:
            await http.aclose()
    return join_lyrics(lines, translated)
