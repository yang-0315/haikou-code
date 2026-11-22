"""Provider-neutral chat completion for all retrieval backends.

Business modules call ``ChatProvider.complete(messages)``.  Provider details
(base URL, model, key, retry policy and response shape) live here only.
"""
from __future__ import annotations

import argparse
import dataclasses
import http.client
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Callable, Protocol

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_ENV_FILE = os.path.join(HERE, ".env")
LEGACY_ENV_FILE = os.path.abspath(
    os.path.join(HERE, "..", "PageIndex", ".env"))


def default_env_file() -> str:
    return LOCAL_ENV_FILE if os.path.exists(LOCAL_ENV_FILE) else LEGACY_ENV_FILE

PROVIDER_DEFAULTS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
        "requires_key": True,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "key_env": "OPENAI_API_KEY",
        "requires_key": True,
    },
    "openai_compatible": {
        "base_url": "",
        "model": "",
        "key_env": "OPENAI_API_KEY",
        "requires_key": False,
    },
    "local": {
        "base_url": "",
        "model": "",
        "key_env": "LOCAL_MODEL_API_KEY",
        "requires_key": False,
    },
}


class ProviderError(RuntimeError):
    """Readable provider failure with secrets and response bodies redacted."""


class ProviderHTTPError(ProviderError):
    def __init__(self, status: int, message: str, retryable: bool):
        super().__init__(f"HTTP {status}: {message[:300]}")
        self.status = status
        self.retryable = retryable


class ChatProvider(Protocol):
    config: "ProviderConfig"

    def complete(self, messages: list[dict], temperature: float = 0.0,
                 timeout: float | None = None,
                 retries: int | None = None) -> str:
        ...


@dataclasses.dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_key: str = dataclasses.field(repr=False)
    timeout: float = 180
    retries: int = 3
    endpoint: str = "chat/completions"
    requires_key: bool = True

    @property
    def url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/{self.endpoint.lstrip('/')}"

    def public(self) -> dict:
        return {
            "provider": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "api_key": "***" if self.api_key else "",
            "timeout": self.timeout,
            "retries": self.retries,
            "endpoint": self.endpoint,
        }


def _load_dotenv(path: str) -> None:
    if not path or not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _number(value, default, cast):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def config_from_env(env: dict | None = None) -> ProviderConfig:
    """Resolve config with precedence: explicit mapping > process env > .env."""
    explicit = dict(env or {})
    if "LLM_ENV_FILE" in explicit:
        env_file = explicit["LLM_ENV_FILE"]
    else:
        env_file = os.environ.get("LLM_ENV_FILE", default_env_file())
    _load_dotenv(env_file)
    values = dict(os.environ)
    values.update(explicit)
    name = (values.get("LLM_PROVIDER") or "deepseek").strip().lower()
    defaults = PROVIDER_DEFAULTS.get(name, PROVIDER_DEFAULTS["openai_compatible"])
    base_url = (values.get("LLM_BASE_URL") or defaults["base_url"]).strip()
    model = (values.get("LLM_MODEL") or defaults["model"]).strip()
    key_env = values.get("LLM_API_KEY_ENV") or defaults["key_env"]
    api_key = (values.get("LLM_API_KEY") or values.get(key_env) or "").strip()
    if not base_url:
        raise ValueError(f"{name} 缺少 LLM_BASE_URL")
    if not model:
        raise ValueError(f"{name} 缺少 LLM_MODEL")
    if defaults["requires_key"] and not api_key:
        raise ValueError(f"{name} 缺少 {key_env} 或 LLM_API_KEY")
    return ProviderConfig(
        name=name,
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=_number(values.get("LLM_TIMEOUT"), 180.0, float),
        retries=max(1, _number(values.get("LLM_RETRIES"), 3, int)),
        endpoint=values.get("LLM_ENDPOINT", "chat/completions"),
        requires_key=defaults["requires_key"],
    )


class OpenAICompatibleProvider:
    """Works with DeepSeek, OpenAI and local OpenAI-compatible endpoints."""

    def __init__(self, config: ProviderConfig,
                 opener: Callable | None = None,
                 sleeper: Callable[[float], None] | None = None):
        self.config = config
        self._opener = opener or urllib.request.urlopen
        self._sleeper = sleeper or time.sleep
        self._context = ssl.create_default_context()

    def _request(self, messages: list[dict], temperature: float,
                 timeout: float) -> str:
        payload = json.dumps({
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = urllib.request.Request(
            self.config.url, data=payload, headers=headers)
        try:
            with self._opener(request, timeout=timeout,
                              context=self._context) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            raise ProviderHTTPError(
                exc.code, body or exc.reason,
                retryable=exc.code == 429 or exc.code >= 500) from exc
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError(
                f"{self.config.name} 返回格式异常：{raw[:240]}") from exc
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content)
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(f"{self.config.name} 返回了空内容")
        return content

    def complete(self, messages: list[dict], temperature: float = 0.0,
                 timeout: float | None = None,
                 retries: int | None = None) -> str:
        timeout = self.config.timeout if timeout is None else timeout
        attempts = self.config.retries if retries is None else max(1, retries)
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._request(messages, temperature, timeout)
            except ProviderHTTPError as exc:
                last = exc
                if not exc.retryable:
                    break
            except (urllib.error.URLError, http.client.HTTPException,
                    TimeoutError, OSError) as exc:
                last = exc
            if attempt + 1 < attempts:
                self._sleeper(2 * (attempt + 1))
        if isinstance(last, ProviderError):
            raise last
        raise ProviderError(
            f"{self.config.name} 模型调用失败（重试 {attempts} 次）："
            f"{type(last).__name__}: {last}")


class CallableProvider:
    """Compatibility adapter for injected ``chat_fn`` implementations."""

    def __init__(self, fn: Callable, config: ProviderConfig | None = None):
        self.fn = fn
        self.config = config or ProviderConfig(
            "callable", "local://callable", "injected",
            api_key="", requires_key=False)

    def complete(self, messages: list[dict], temperature: float = 0.0,
                 timeout: float | None = None,
                 retries: int | None = None) -> str:
        return self.fn(messages, temperature=temperature)


def get_provider(provider=None, env: dict | None = None) -> ChatProvider:
    if hasattr(provider, "complete") and callable(provider.complete):
        return provider
    if callable(provider):
        return CallableProvider(provider)
    if isinstance(provider, ProviderConfig):
        return OpenAICompatibleProvider(provider)
    if provider:
        values = dict(env or {})
        values["LLM_PROVIDER"] = str(provider)
        config = config_from_env(values)
    else:
        config = config_from_env(env)
    return OpenAICompatibleProvider(config)


def main():
    parser = argparse.ArgumentParser(description="检查或测试模型 Provider")
    parser.add_argument("--show-config", action="store_true",
                        help="打印脱敏后的有效配置")
    parser.add_argument("--chat", default="", help="发送一条测试消息")
    parser.add_argument("--provider", default="")
    args = parser.parse_args()
    try:
        provider = get_provider(args.provider or None)
        if args.show_config or not args.chat:
            print(json.dumps(provider.config.public(),
                             ensure_ascii=False, indent=2))
            return
        answer = provider.complete(
            [{"role": "user", "content": args.chat}], temperature=0)
        print(answer)
    except (ProviderError, ValueError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
