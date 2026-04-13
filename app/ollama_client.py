"""
Ollama client wrapper — calls the local Ollama server's OpenAI-compatible API.

Ollama runs LLMs entirely on the user's Mac. Zero cost, zero internet.
API: POST http://localhost:11434/v1/chat/completions (OpenAI-compatible)

Usage:
    from app.ollama_client import ollama_chat, ollama_chat_stream

    # Non-streaming
    text = ollama_chat(messages, model="qwen2.5:14b")

    # Streaming (yields token strings)
    for token in ollama_chat_stream(messages, model="qwen2.5:14b"):
        print(token, end="", flush=True)
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from typing import Iterator, List, Dict, Any

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """Raised when Ollama is not running or returns an error."""
    pass


def _call(
    messages: List[Dict[str, str]],
    model: str,
    base_url: str = "http://localhost:11434",
    max_tokens: int = 4096,
    temperature: float = 0.3,
    stream: bool = False,
) -> urllib.request.Request:
    """Build the HTTP request for Ollama's OpenAI-compatible endpoint."""
    url = f"{base_url}/v1/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return req


def ollama_chat(
    messages: List[Dict[str, str]],
    model: str = "qwen2.5:14b",
    base_url: str = "http://localhost:11434",
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    """
    Non-streaming call to Ollama. Returns the full response text.
    Raises OllamaError if Ollama is not running.
    """
    req = _call(messages, model, base_url, max_tokens, temperature, stream=False)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except urllib.error.URLError as e:
        raise OllamaError(
            f"Cannot connect to Ollama at {base_url}. "
            f"Is it running? Start with: ollama serve\n"
            f"Error: {e}"
        ) from e
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise OllamaError(f"Unexpected Ollama response format: {e}") from e


def ollama_chat_stream(
    messages: List[Dict[str, str]],
    model: str = "qwen2.5:14b",
    base_url: str = "http://localhost:11434",
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> Iterator[str]:
    """
    Streaming call to Ollama. Yields text tokens as they arrive.
    Raises OllamaError if Ollama is not running.
    """
    req = _call(messages, model, base_url, max_tokens, temperature, stream=True)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for line in resp:
                line = line.decode("utf-8").strip()
                if not line:
                    continue
                # SSE format: "data: {...}"
                if line.startswith("data: "):
                    line = line[6:]
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
    except urllib.error.URLError as e:
        raise OllamaError(
            f"Cannot connect to Ollama at {base_url}. "
            f"Is it running? Start with: ollama serve\n"
            f"Error: {e}"
        ) from e


def is_ollama_available(base_url: str = "http://localhost:11434") -> bool:
    """Quick check if Ollama server is reachable."""
    try:
        req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def list_models(base_url: str = "http://localhost:11434") -> List[str]:
    """List available Ollama models."""
    try:
        req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []
