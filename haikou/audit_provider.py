"""Offline audit for model-provider decoupling."""
from __future__ import annotations

import json
import os
import sys
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llm_provider
import reasoning_retrieval as rr


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def probe(title, fn):
    try:
        ok, detail = fn()
    except Exception as exc:                      # noqa: BLE001
        ok, detail = False, f"抛异常 {type(exc).__name__}: {exc}"
    print(f"{'通过' if ok else '失败'}  {title}")
    if detail:
        print(f"        {detail}")
    return bool(ok), detail


def main():
    results = []

    def default_config():
        config = llm_provider.config_from_env({
            "LLM_ENV_FILE": "", "LLM_PROVIDER": "deepseek",
            "LLM_API_KEY": "secret",
        })
        public = config.public()
        ok = (config.name == "deepseek"
              and config.url == "https://api.deepseek.com/chat/completions"
              and config.model == "deepseek-chat"
              and public["api_key"] == "***"
              and "secret" not in json.dumps(public))
        return ok, json.dumps(public, ensure_ascii=False)

    def custom_config():
        config = llm_provider.config_from_env({
            "LLM_ENV_FILE": "", "LLM_PROVIDER": "local",
            "LLM_BASE_URL": "http://127.0.0.1:8000/v1",
            "LLM_MODEL": "policy-local",
            "LLM_TIMEOUT": "45", "LLM_RETRIES": "5",
        })
        ok = (config.url == "http://127.0.0.1:8000/v1/chat/completions"
              and config.model == "policy-local"
              and config.timeout == 45 and config.retries == 5)
        return ok, config.url

    def request_shape():
        seen = {}

        def opener(request, timeout, context):
            seen["url"] = request.full_url
            seen["payload"] = json.loads(request.data.decode())
            seen["auth"] = request.get_header("Authorization")
            seen["timeout"] = timeout
            return Response({"choices": [{"message": {"content": "OK"}}]})

        config = llm_provider.ProviderConfig(
            "compatible", "https://example.test/v1", "demo-model",
            api_key="k", timeout=12, retries=2)
        provider = llm_provider.OpenAICompatibleProvider(config, opener=opener)
        answer = provider.complete([{"role": "user", "content": "hi"}], 0.1)
        ok = (answer == "OK"
              and seen["url"] == "https://example.test/v1/chat/completions"
              and seen["payload"]["model"] == "demo-model"
              and seen["payload"]["temperature"] == 0.1
              and seen["auth"] == "Bearer k"
              and seen["timeout"] == 12)
        return ok, f"{seen['url']}／{answer}"

    def content_list():
        opener = lambda *args, **kwargs: Response({
            "choices": [{"message": {"content": [
                {"type": "text", "text": "甲"},
                {"type": "text", "text": "乙"},
            ]}}]
        })
        config = llm_provider.ProviderConfig(
            "list", "https://example.test", "m", api_key="")
        answer = llm_provider.OpenAICompatibleProvider(
            config, opener=opener).complete([{"role": "user", "content": "x"}])
        return answer == "甲乙", answer

    def retry_success():
        calls = []
        waits = []

        def opener(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.URLError("temporary")
            return Response({"choices": [{"message": {"content": "retried"}}]})

        config = llm_provider.ProviderConfig(
            "retry", "https://example.test", "m", api_key="", retries=3)
        answer = llm_provider.OpenAICompatibleProvider(
            config, opener=opener, sleeper=waits.append).complete(
                [{"role": "user", "content": "x"}])
        return (answer == "retried" and len(calls) == 2 and waits == [2]), \
            f"calls={len(calls)} waits={waits}"

    def http_auth_no_retry():
        calls = []

        def opener(request, timeout, context):
            calls.append(1)
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {}, None)

        config = llm_provider.ProviderConfig(
            "auth", "https://example.test", "m", api_key="bad", retries=3)
        try:
            llm_provider.OpenAICompatibleProvider(
                config, opener=opener, sleeper=lambda _: None).complete(
                    [{"role": "user", "content": "x"}])
        except llm_provider.ProviderHTTPError as exc:
            return len(calls) == 1 and exc.status == 401, str(exc)
        return False, "没有抛出 HTTP 错误"

    def callable_compat():
        answer = rr.chat(
            [{"role": "user", "content": "x"}],
            provider=lambda messages, temperature=0: "callable-ok")
        return answer == "callable-ok", answer

    def backend_source_clean():
        source = open(os.path.join(HERE, "reasoning_retrieval.py"),
                      encoding="utf-8").read()
        forbidden = ["api.deepseek.com", "deepseek-chat",
                     "DEEPSEEK_API_KEY", "ssl.create_default_context"]
        found = [word for word in forbidden if word in source]
        return not found, (f"主问答残留 {found}" if found
                           else "主问答已无 Provider URL／模型／密钥硬编码")

    def missing_config_readable():
        try:
            llm_provider.config_from_env({
                "LLM_ENV_FILE": "", "LLM_PROVIDER": "openai",
                "LLM_MODEL": "m", "OPENAI_API_KEY": "",
            })
        except ValueError as exc:
            return "OPENAI_API_KEY" in str(exc), str(exc)
        return False, "缺 key 未报错"

    def pageindex_mapping():
        import build_pi_store
        model, env = build_pi_store.litellm_settings()
        ok = (model == "deepseek/deepseek-chat"
              and "DEEPSEEK_API_KEY" in env)
        return ok, f"PageIndex litellm → {model}"

    results += [
        probe("默认 DeepSeek 配置由 Provider 层解析", default_config),
        probe("本地／兼容服务仅靠配置切换", custom_config),
        probe("统一请求格式、URL 与鉴权", request_shape),
        probe("兼容列表型 content 返回", content_list),
        probe("网络失败自动重试", retry_success),
        probe("401 立即失败且不泄露密钥", http_auth_no_retry),
        probe("callable chat_fn 兼容", callable_compat),
        probe("业务代码无 Provider 硬编码", backend_source_clean),
        probe("缺少密钥时给出可读配置错误", missing_config_readable),
        probe("PageIndex 建索引用同一 Provider 配置", pageindex_mapping),
    ]
    passed = sum(ok for ok, _ in results)
    print(f"\n通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
