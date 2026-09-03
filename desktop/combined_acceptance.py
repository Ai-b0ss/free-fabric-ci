from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

try:
    from desktop.local_http import opener as _local_http_opener
except ModuleNotFoundError:
    from local_http import opener as _local_http_opener


MAX_BODY_BYTES = 256 * 1024
MAX_FAILURE_SAMPLES = 20
_LOCAL_HTTP_OPENER = _local_http_opener()
_PROTOCOLS = {"health", "openai-chat", "responses"}


@dataclass(frozen=True)
class Surface:
    name: str
    base_url: str
    expected_models: frozenset[str]
    protocol: str = "health"
    auth_env: str | None = None


def _local_base_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise RuntimeError("surface_url_invalid") from exc
    if parsed.scheme != "http" or not parsed.hostname:
        raise RuntimeError("surface_url_invalid")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise RuntimeError("surface_url_invalid")
    host = parsed.hostname.lower().rstrip(".")
    local = host == "localhost"
    if not local:
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
    if not local:
        raise RuntimeError("surface_not_loopback")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise RuntimeError("surface_url_invalid") from exc
    return value.rstrip("/")


def _endpoint_identity(value: str) -> tuple[str, int]:
    parsed = urllib.parse.urlsplit(_local_base_url(value))
    host = parsed.hostname.lower().rstrip(".") if parsed.hostname else ""
    if host == "localhost":
        host = "127.0.0.1"
    else:
        try:
            host = ipaddress.ip_address(host).compressed
        except ValueError:
            pass
    port = parsed.port or 80
    return host, port


def _headers(surface: Surface) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if surface.auth_env:
        secret = os.environ.get(surface.auth_env, "")
        if not secret:
            raise RuntimeError("auth_env_missing")
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _request_json(surface: Surface, path: str, timeout: float, *, method: str = "GET", payload: object | None = None) -> object:
    base_url = _local_base_url(surface.base_url)
    raw_body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(base_url + path, data=raw_body, headers=_headers(surface), method=method)
    try:
        with _LOCAL_HTTP_OPENER.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"http_status_{response.status}")
            raw = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"http_status_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"transport_error:{type(exc).__name__}") from exc
    if len(raw) > MAX_BODY_BYTES:
        raise RuntimeError("response_too_large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("invalid_json") from exc


def probe_models(surface: Surface, timeout: float) -> dict[str, object]:
    payload = _request_json(surface, "/v1/models", timeout)
    if not isinstance(payload, dict) or payload.get("object") != "list":
        raise RuntimeError("models_contract_invalid")
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("models_contract_invalid")
    actual = {item.get("id") for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)}
    missing = sorted(surface.expected_models - actual)
    if missing:
        raise RuntimeError("missing_models:" + ",".join(missing))
    return {"name": surface.name, "models": sorted(actual)}


def _one_model(surface: Surface) -> str:
    if len(surface.expected_models) != 1:
        raise RuntimeError("tool_roundtrip_requires_one_model")
    return next(iter(surface.expected_models))


def _openai_tool_roundtrip(surface: Surface, timeout: float, nonce: str) -> dict[str, object]:
    model = _one_model(surface)
    tool = {"type": "function", "function": {"name": "ff_echo", "description": "Return the supplied value unchanged. Use this tool exactly once for acceptance.", "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}}}
    user_text = f"Call ff_echo with value {nonce}. After its result, answer with exactly {nonce}."
    first_body = {"model": model, "stream": False, "messages": [{"role": "user", "content": user_text}], "tools": [tool], "tool_choice": "required"}
    first = _request_json(surface, "/v1/chat/completions", timeout, method="POST", payload=first_body)
    try:
        message = first["choices"][0]["message"]
        calls = message["tool_calls"]
        if not isinstance(calls, list) or len(calls) != 1:
            raise RuntimeError("tool_call_count_invalid")
        call = calls[0]
        if call["function"]["name"] != "ff_echo":
            raise RuntimeError("tool_name_mismatch")
        args = json.loads(call["function"]["arguments"])
        if not isinstance(args, dict) or set(args) != {"value"}:
            raise RuntimeError("tool_call_contract_invalid")
        if args.get("value") != nonce:
            raise RuntimeError("tool_arguments_mismatch")
        call_id = call["id"]
        if not isinstance(call_id, str) or not call_id.strip():
            raise RuntimeError("tool_call_contract_invalid")
    except RuntimeError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("tool_call_contract_invalid") from exc
    second_body = {"model": model, "stream": False, "messages": [{"role": "user", "content": user_text}, message, {"role": "tool", "tool_call_id": call_id, "name": "ff_echo", "content": nonce}], "tools": [tool]}
    second = _request_json(surface, "/v1/chat/completions", timeout, method="POST", payload=second_body)
    try:
        final_message = second["choices"][0]["message"]
        if not isinstance(final_message, dict):
            raise RuntimeError("tool_result_contract_invalid")
        if final_message.get("tool_calls"):
            raise RuntimeError("tool_called_more_than_once")
        final_text = final_message["content"]
    except RuntimeError:
        raise
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("tool_result_contract_invalid") from exc
    if not isinstance(final_text, str) or final_text.strip() != nonce:
        raise RuntimeError("tool_result_marker_invalid")
    return {"name": surface.name, "protocol": surface.protocol, "tool": "ff_echo", "ok": True}


def _responses_tool_roundtrip(surface: Surface, timeout: float, nonce: str) -> dict[str, object]:
    model = _one_model(surface)
    tool = {"type": "function", "name": "ff_echo", "description": "Return the supplied value unchanged. Use this tool exactly once for acceptance.", "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}}
    user_text = f"Call ff_echo with value {nonce}. After its result, answer with exactly {nonce}."
    first_body = {"model": model, "stream": False, "input": [{"type": "message", "role": "user", "content": user_text}], "tools": [tool]}
    first = _request_json(surface, "/v1/responses", timeout, method="POST", payload=first_body)
    output = first.get("output") if isinstance(first, dict) else None
    if not isinstance(output, list):
        raise RuntimeError("tool_call_contract_invalid")
    calls = [item for item in output if isinstance(item, dict) and item.get("type") == "function_call"]
    if len(calls) != 1:
        raise RuntimeError("tool_call_count_invalid")
    call = calls[0]
    call_id = call.get("call_id")
    if call.get("name") != "ff_echo" or not isinstance(call_id, str) or not call_id.strip():
        raise RuntimeError("tool_call_contract_invalid")
    try:
        args = json.loads(str(call.get("arguments", "")))
    except json.JSONDecodeError as exc:
        raise RuntimeError("tool_call_contract_invalid") from exc
    if not isinstance(args, dict) or set(args) != {"value"}:
        raise RuntimeError("tool_call_contract_invalid")
    if args.get("value") != nonce:
        raise RuntimeError("tool_arguments_mismatch")
    second_body = {"model": model, "stream": False, "input": [{"type": "message", "role": "user", "content": user_text}, call, {"type": "function_call_output", "call_id": call_id, "output": nonce}], "tools": [tool]}
    second = _request_json(surface, "/v1/responses", timeout, method="POST", payload=second_body)
    output2 = second.get("output") if isinstance(second, dict) else None
    if not isinstance(output2, list):
        raise RuntimeError("tool_result_contract_invalid")
    if any(isinstance(item, dict) and item.get("type") == "function_call" for item in output2):
        raise RuntimeError("tool_called_more_than_once")
    texts: list[str] = []
    for item in output2:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise RuntimeError("tool_result_contract_invalid")
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    if "\n".join(texts).strip() != nonce:
        raise RuntimeError("tool_result_marker_invalid")
    return {"name": surface.name, "protocol": surface.protocol, "tool": "ff_echo", "ok": True}


def probe_tool_roundtrip(surface: Surface, timeout: float, nonce: str) -> dict[str, object]:
    if surface.protocol == "openai-chat":
        return _openai_tool_roundtrip(surface, timeout, nonce)
    if surface.protocol == "responses":
        return _responses_tool_roundtrip(surface, timeout, nonce)
    raise RuntimeError("tool_protocol_not_configured")


def _validate_combined_surfaces(surfaces: list[Surface], *, require_tools: bool = False) -> None:
    if len(surfaces) < 2:
        raise ValueError("combined acceptance requires at least two surfaces")
    names = [surface.name for surface in surfaces]
    if len(set(names)) != len(names):
        raise ValueError("surface names must be unique")
    endpoints = [_endpoint_identity(surface.base_url) for surface in surfaces]
    if len(set(endpoints)) != len(endpoints):
        raise ValueError("surface endpoints must be unique")
    if require_tools:
        if any(surface.protocol == "health" for surface in surfaces):
            raise ValueError("tool round-trip requires a protocol for every surface")
        protocols = {surface.protocol for surface in surfaces}
        if not {"openai-chat", "responses"}.issubset(protocols):
            raise ValueError("combined tool acceptance requires both openai-chat and responses protocols")


def run_soak(surfaces: list[Surface], *, duration: float, interval: float, timeout: float, require_tools: bool = False) -> dict[str, object]:
    _validate_combined_surfaces(surfaces, require_tools=require_tools)
    timings = (duration, interval, timeout)
    if any(not math.isfinite(value) for value in timings) or duration < 0 or interval <= 0 or timeout <= 0:
        raise ValueError("invalid timing")
    started = time.monotonic()
    deadline = started + duration
    checks = 0
    failure_count = 0
    failures: list[dict[str, object]] = []
    last_ok: dict[str, object] = {}
    tool_ok: dict[str, object] = {}
    while True:
        checks += 1
        for surface in surfaces:
            try:
                last_ok[surface.name] = probe_models(surface, timeout)
                if require_tools:
                    nonce = f"ff-f51-{surface.name}-{checks}"
                    tool_ok[surface.name] = probe_tool_roundtrip(surface, timeout, nonce)
            except RuntimeError as exc:
                failure_count += 1
                if len(failures) < MAX_FAILURE_SAMPLES:
                    failures.append({"surface": surface.name, "check": checks, "error": str(exc)[:240]})
        now = time.monotonic()
        if now >= deadline:
            break
        time.sleep(min(interval, max(0.0, deadline - now)))
    elapsed = round(time.monotonic() - started, 3)
    return {"schema": "ff-combined-acceptance-v2", "ok": failure_count == 0, "checks": checks, "elapsed_seconds": elapsed, "surfaces": sorted(last_ok), "tool_roundtrips": sorted(tool_ok) if require_tools else [], "failures": failures, "failure_count": failure_count, "failure_samples_truncated": failure_count > len(failures)}


def _surface(value: str) -> Surface:
    parts = value.split("|")
    if len(parts) not in {3, 4, 5}:
        raise argparse.ArgumentTypeError("surface must be NAME|BASE_URL|MODEL[,MODEL]|[PROTOCOL]|[AUTH_ENV]")
    while len(parts) < 5:
        parts.append("")
    name, base_url, model_csv, protocol, auth_env = (part.strip() for part in parts)
    models = frozenset(model.strip() for model in model_csv.split(",") if model.strip())
    protocol = protocol or "health"
    auth_env = auth_env or None
    if not name or not base_url or not models:
        raise argparse.ArgumentTypeError("surface name, URL, and model set are required")
    if protocol not in _PROTOCOLS:
        raise argparse.ArgumentTypeError("unsupported surface protocol")
    if auth_env and not auth_env.replace("_", "").isalnum():
        raise argparse.ArgumentTypeError("auth env name is invalid")
    try:
        base_url = _local_base_url(base_url)
    except RuntimeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return Surface(name=name, base_url=base_url, expected_models=models, protocol=protocol, auth_env=auth_env)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded combined local Qwen + Notion acceptance gate")
    parser.add_argument("--surface", action="append", type=_surface, required=True)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--require-tools", action="store_true")
    args = parser.parse_args()
    try:
        result = run_soak(args.surface, duration=args.duration, interval=args.interval, timeout=args.timeout, require_tools=args.require_tools)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
