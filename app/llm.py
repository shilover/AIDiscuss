"""统一模型调用层：支持本地 CLI（claude / dsh / gemini）与云端 API。"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from .config import ModelSpec

T = TypeVar("T", bound=BaseModel)

TRUNCATED_NOTE = "\n\n... (内容超长已截断，请基于已有信息作答)"


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------- JSON 解析


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出里抠出 JSON 对象，容忍 ```json 包裹和前后废话。"""
    cleaned = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned.strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMError(f"响应中没有 JSON 对象: {text[:200]!r}")
    return json.loads(cleaned[start : end + 1])


# ---------------------------------------------------------------- CLI 传输


def _render_prompt(spec: ModelSpec, text: str) -> list[str]:
    """按 CLI 的约定把 prompt 拼进 argv（有的要 -p，有的直接位置传）。"""
    return [spec.cli_prompt_flag, text] if spec.cli_prompt_flag else [text]


def _build_cli_invocation(
    spec: ModelSpec, system: str, user: str
) -> tuple[list[str], str | None, str | None]:
    """构造 CLI 调用。返回 (argv, stdin_text, temp_dir)。"""
    argv = list(spec.cli_command)
    if spec.cli_model_flag and spec.cli_model:
        argv += [spec.cli_model_flag, spec.cli_model]
    body = user

    if spec.cli_system_flag:
        argv += [spec.cli_system_flag, system]
    elif system:
        # 没有 system 参数通道的 CLI，把 system 拼进正文
        body = f"{system}\n\n{user}"

    temp_dir: str | None = None

    if spec.cli_prompt_mode == "file":
        # 走临时文件：不受命令行长度限制
        temp_dir = tempfile.mkdtemp(prefix="aidiscuss_")
        prompt_file = Path(temp_dir) / "prompt.md"
        prompt_file.write_text(body, encoding="utf-8")
        argv += _render_prompt(spec, spec.cli_file_task.format(path=prompt_file))
        return argv, None, temp_dir

    overhead = sum(len(part) for part in argv)
    if spec.cli_max_prompt_chars:
        budget = spec.cli_max_prompt_chars - overhead
        if budget > 0 and len(body) > budget:
            body = body[: max(0, budget - len(TRUNCATED_NOTE))] + TRUNCATED_NOTE

    stdin_text: str | None = None
    if spec.cli_prompt_mode == "arg":
        argv += _render_prompt(spec, body)
    else:
        stdin_text = body
    return argv, stdin_text, None


def _resolve_program(name: str) -> str:
    """Windows 下 subprocess 不认 PATHEXT，需要显式解析 dsh.cmd 这类包装。"""
    found = shutil.which(name)
    return found or name


def _run_cli(spec: ModelSpec, system: str, user: str) -> str:
    argv, stdin_text, temp_dir = _build_cli_invocation(spec, system, user)
    argv[0] = _resolve_program(argv[0])
    try:
        proc = subprocess.run(
            argv,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=spec.cli_timeout,
        )
    except FileNotFoundError as exc:
        raise LLMError(f"找不到 CLI 命令 {argv[0]!r}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LLMError(f"CLI 超时（{spec.cli_timeout}s）: {' '.join(argv[:3])}") from exc
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        raise LLMError(f"CLI 退出码 {proc.returncode}: {detail}")

    out = proc.stdout or ""
    if spec.cli_output_mode == "json_result":
        try:
            payload = json.loads(out)
        except json.JSONDecodeError as exc:
            raise LLMError(f"CLI 输出不是 JSON: {out[:300]!r}") from exc
        if payload.get("is_error"):
            raise LLMError(
                f"CLI 返回错误: {str(payload.get(spec.cli_result_field))[:300]}"
            )
        return str(payload.get(spec.cli_result_field, ""))
    return out


# ---------------------------------------------------------------- API 传输


async def _post(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.post(url, headers=headers, json=body, timeout=240.0)
            if resp.status_code >= 400:
                raise LLMError(f"HTTP {resp.status_code}: {resp.text[:400]}")
            return resp.json()
        except (httpx.HTTPError, LLMError, json.JSONDecodeError) as exc:
            last = exc
            if attempt < 2:
                await asyncio.sleep(2**attempt)
    raise LLMError(f"请求失败: {last}")


async def _call_anthropic(
    client: httpx.AsyncClient, spec: ModelSpec, system: str, user: str, max_tokens: int
) -> str:
    body = {
        "model": spec.model,
        "max_tokens": max_tokens,
        "temperature": spec.temperature,
        "system": system,
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": "{"},
        ],
    }
    headers = {
        "x-api-key": spec.api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    data = await _post(client, spec.base_url, headers=headers, body=body)
    text = "".join(
        part.get("text", "")
        for part in data.get("content", [])
        if part.get("type") == "text"
    )
    return "{" + text


async def _call_openai_compat(
    client: httpx.AsyncClient, spec: ModelSpec, system: str, user: str, max_tokens: int
) -> str:
    body = {
        "model": spec.model,
        "temperature": spec.temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {
        "Authorization": f"Bearer {spec.api_key}",
        "Content-Type": "application/json",
    }
    data = await _post(client, spec.base_url, headers=headers, body=body)
    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"响应缺少 choices: {json.dumps(data)[:300]}")
    return choices[0]["message"]["content"]


async def _call_gemini(
    client: httpx.AsyncClient, spec: ModelSpec, system: str, user: str, max_tokens: int
) -> str:
    url = f"{spec.base_url}/{spec.model}:generateContent?key={spec.api_key}"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": spec.temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }
    data = await _post(
        client, url, headers={"Content-Type": "application/json"}, body=body
    )
    candidates = data.get("candidates") or []
    if not candidates:
        raise LLMError(f"响应缺少 candidates: {json.dumps(data)[:300]}")
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts)


_API_DISPATCH = {
    "anthropic": _call_anthropic,
    "openai_compat": _call_openai_compat,
    "gemini": _call_gemini,
}


# ---------------------------------------------------------------- 对外入口


async def complete(
    spec: ModelSpec, system: str, user: str, *, max_tokens: int | None = None
) -> str:
    if spec.is_cli:
        return await asyncio.to_thread(_run_cli, spec, system, user)

    if not spec.api_key:
        raise LLMError(
            f"缺少 API Key: 请设置环境变量 {spec.api_key_env}（模型 {spec.alias}）"
        )
    handler = _API_DISPATCH.get(spec.provider)
    if handler is None:
        raise LLMError(f"未知 provider: {spec.provider}")
    async with httpx.AsyncClient() as client:
        return await handler(client, spec, system, user, max_tokens or spec.max_tokens)


async def complete_json(
    spec: ModelSpec,
    system: str,
    user: str,
    schema: type[T],
    *,
    attempts: int = 2,
) -> T:
    """调用模型并把结果解析成 schema；解析失败会自动重试。"""
    last: Exception | None = None
    for i in range(attempts):
        prompt = user
        if i > 0:
            prompt += (
                "\n\n【重要】上一次输出不是合法 JSON，"
                "请严格只输出一个 JSON 对象，不要任何解释文字、不要 Markdown 代码块。"
            )
        try:
            raw = await complete(spec, system, prompt)
            return schema.model_validate(extract_json(raw))
        except (LLMError, ValueError) as exc:
            last = exc
    raise LLMError(f"无法把模型输出解析为 {schema.__name__}: {last}")