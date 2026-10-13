"""Serial, opt-in synthetic OpenRouter evaluation with a persistent USD ceiling.

Paid execution is Windows-only and restricted to fixtures in synthetic_dialogues.
No application configuration or real conversation is opened by this module.
"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import difflib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
import unicodedata
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import draft
from core.adaptive_analysis import (build_adaptive_questions, build_adaptive_state,
    normalize_messages, parse_adaptive_result, select_turn_targets)
from core.jev_client import API_URL as JEV_URL, MODEL as JEV_MODEL
from app.result_view import project_result
from scripts.synthetic_dialogues import selected_cases


CAP_USD = Decimal("5")
BUILD = ROOT / "build" / "experience-eval"
CATALOG_URL = "https://openrouter.ai/api/v1/models"
BASE_REF = "f8c3d66abedc7f4ec4985c2e8b838ee751d6dffa"
BASE_SOURCE_SHA256 = "d02d5864611907b64666b8431996bbb113c8900521349f8f322d3c28a9421464"
OVERHEAD_TOKENS = 4096
DRAFT_CONTEXT_KEEP = 10


class EvaluationError(RuntimeError):
    """Fixed, non-secret-bearing evaluation failure."""


class _ProviderResponseError(EvaluationError, ValueError):
    """Provider response could not be parsed; carries only the returned object."""

    def __init__(self, response: object):
        super().__init__("invalid_response")
        self.response = response


def _amount(value: object) -> Decimal:
    if isinstance(value, bool):
        raise EvaluationError("invalid amount")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise EvaluationError("invalid amount") from None
    if not number.is_finite() or number < 0:
        raise EvaluationError("invalid amount")
    return number


class BudgetLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"cap_usd": "5", "reservations": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (data["cap_usd"] != "5" or not isinstance(data["reservations"], list)
                    or any(not isinstance(row, dict) or "id" not in row or "charged_usd" not in row
                           for row in data["reservations"])):
                raise ValueError()
            return data
        except (OSError, ValueError, KeyError, TypeError):
            raise EvaluationError("invalid ledger") from None

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def total_usd(self) -> Decimal:
        return sum((_amount(row["charged_usd"]) for row in self._read()["reservations"]), Decimal(0))

    def reserve(self, amount: Decimal) -> str:
        amount = _amount(amount)
        if amount <= 0:
            raise EvaluationError("invalid reservation")
        data = self._read()
        if any(row.get("status") == "overrun" for row in data["reservations"]):
            raise EvaluationError("budget overrun")
        total = sum((_amount(row["charged_usd"]) for row in data["reservations"]), Decimal(0))
        if total + amount > CAP_USD:
            raise EvaluationError("budget exhausted")
        identifier = uuid.uuid4().hex
        data["reservations"].append({"id": identifier, "reserved_usd": str(amount),
                                     "charged_usd": str(amount), "status": "reserved"})
        self._write(data)
        return identifier

    def settle(self, identifier: str, response: dict | None) -> str:
        data = self._read()
        row = next((item for item in data["reservations"] if item["id"] == identifier), None)
        if row is None or row["status"] != "reserved":
            raise EvaluationError("invalid reservation")
        row["status"] = "unknown"
        if isinstance(response, dict):
            usage = response.get("usage")
            if isinstance(usage, dict) and "cost" in usage:
                try:
                    actual = _amount(usage["cost"])
                    row["charged_usd"] = str(actual)
                    row["status"] = "settled" if actual <= _amount(row["reserved_usd"]) else "overrun"
                except EvaluationError:
                    pass
        self._write(data)
        return row["status"]


def budgeted_call(ledger: BudgetLedger, reserved_usd: Decimal, transport):
    identifier = ledger.reserve(reserved_usd)
    try:
        response = transport()
    except Exception:
        ledger.settle(identifier, None)
        raise
    status = ledger.settle(identifier, response)
    if status == "overrun" or ledger.total_usd() > CAP_USD:
        raise EvaluationError("budget overrun")
    return response


def _ceiling_prices(pricing: dict) -> tuple[Decimal, Decimal]:
    if not isinstance(pricing, dict):
        raise EvaluationError("unpriced model")
    overrides = pricing.get("overrides", [])
    if not isinstance(overrides, list):
        raise EvaluationError("unpriced model")
    known = {"prompt", "completion", "request", "overrides", "input_cache_read",
             "input_cache_write", "image", "web_search", "internal_reasoning",
             "input_audio", "output_audio"}
    for row in [pricing, *overrides]:
        if not isinstance(row, dict):
            raise EvaluationError("unpriced model")
        for key, value in row.items():
            if key in ("min_prompt_tokens", "max_prompt_tokens", "overrides",
                       "input_cache_read", "input_cache_write"):
                continue
            if key not in known or key in ("request", "image", "web_search",
                                           "internal_reasoning", "input_audio", "output_audio"):
                if _amount(value) != 0:
                    raise EvaluationError("unpriced extra fee")
    prompt = max(_amount(row.get("prompt", pricing.get("prompt"))) for row in [pricing, *overrides])
    completion = max(_amount(row.get("completion", pricing.get("completion"))) for row in [pricing, *overrides])
    if prompt <= 0:
        raise EvaluationError("unpriced model")
    return prompt, completion


def reservation_usd(body: dict, pricing: dict) -> Decimal:
    """One UTF-8 byte per possible input token plus overhead, at most max_tokens output."""
    prompt, completion = _ceiling_prices(pricing)
    if prompt <= 0 or not isinstance(body.get("max_tokens"), int) or body["max_tokens"] <= 0:
        raise EvaluationError("unpriced model")
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return (Decimal(len(payload) + OVERHEAD_TOKENS) * prompt
            + Decimal(body["max_tokens"]) * completion)


def safe_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http:{exc.code}"
    if isinstance(exc, EvaluationError):
        return str(exc) if str(exc) in {
            "invalid amount", "invalid ledger", "invalid reservation", "budget exhausted",
            "budget overrun", "unpriced request fee", "unpriced extra fee", "unpriced model",
            "invalid model catalog", "invalid_response", "concurrent runner", "missing saved key",
            "paid evaluation requires native Windows",
        } else "evaluation_error"
    if isinstance(exc, urllib.error.URLError):
        return "network"
    return "invalid_response"


_DIAGNOSTIC_USAGE_KEYS = ("cost", "prompt_tokens", "completion_tokens", "total_tokens")
_DIAGNOSTIC_NUMBER = re.compile(r"\d{1,32}(?:\.\d{1,32})?\Z")
_DIAGNOSTIC_SECRET = re.compile(
    r"(?:Bearer\s+|sk-[A-Za-z0-9._~+/=-]+|"
    r"[\"']?(?:openrouter[_-])?api[_-]?key[\"']?\s*[:=]\s*[\"']?|"
    r"[\"']?authorization[\"']?\s*[:=]\s*[\"']?|"
    r"[\"']?(?:access[_-]?)?token[\"']?\s*[:=]\s*[\"']?|"
    r"[?&](?:api[_-]?key|token)=)[A-Za-z0-9._~+/=-]+",
    re.I,
)
_DIAGNOSTIC_FINISH_REASONS = frozenset({
    "stop", "length", "content_filter", "tool_calls", "function_call",
})


def _diagnostic_usage_value(value: object) -> int | float | str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 10**18 else None
    if isinstance(value, float):
        return value if math.isfinite(value) and 0 <= value <= 10**18 else None
    if isinstance(value, str) and _DIAGNOSTIC_NUMBER.fullmatch(value) and len(value) <= 64:
        return value
    return None


def _provider_diagnostic(response: object) -> dict:
    """Keep bounded provider-message evidence without copying request/error data."""
    diagnostic: dict[str, object] = {}
    if not isinstance(response, dict):
        return diagnostic
    choices = response.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    if isinstance(first, dict):
        finish_reason = first.get("finish_reason")
        if finish_reason in _DIAGNOSTIC_FINISH_REASONS:
            diagnostic["finish_reason"] = finish_reason
        message = first.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            text = _DIAGNOSTIC_SECRET.sub("[已隐藏]", message["content"])
            diagnostic["content"] = text[:6400]
    usage = response.get("usage")
    if isinstance(usage, dict):
        safe_usage = {}
        for key in _DIAGNOSTIC_USAGE_KEYS:
            value = _diagnostic_usage_value(usage.get(key))
            if value is not None:
                safe_usage[key] = value
        if safe_usage:
            diagnostic["usage"] = safe_usage
    return diagnostic


def _urlopen_json(request: urllib.request.Request, *, timeout: int = 30) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except urllib.error.HTTPError as exc:
        exc.close()
        raise


def public_pricing(model: str) -> dict:
    request = urllib.request.Request(CATALOG_URL, headers={"Accept-Encoding": "gzip"})
    catalog = _urlopen_json(request)
    rows = catalog.get("data")
    if not isinstance(rows, list):
        raise EvaluationError("invalid model catalog")
    entry = next((row for row in rows if isinstance(row, dict) and row.get("id") == model), None)
    if entry is None or not isinstance(entry.get("pricing"), dict):
        raise EvaluationError("unpriced model")
    return entry["pricing"]


def _base_namespace():
    """Compile fixed base request and candidate handling without transport or imports."""
    try:
        payload = (ROOT / "scripts" / "fixtures" / "draft_f8c3d66.py.txt").read_bytes()
    except OSError:
        raise EvaluationError("baseline_fixture_unavailable") from None
    if hashlib.sha256(payload).hexdigest() != BASE_SOURCE_SHA256:
        raise EvaluationError("baseline_fixture_mismatch")
    source = payload.decode("utf-8")
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "CHAT_URL", "DEFAULT_MODEL", "SYSTEM", "DRAFT_RESPONSE_FORMAT", "_INJECT", "_LAUGH"
                ):
                    selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in (
            "_line", "_suspects", "_her_recent", "_norm", "_sanitize",
            "_parse_candidates", "_candidate_signature", "_deduplicate_candidates"
        ):
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "draft_candidates":
            builder = deepcopy(node)
            builder.name = "build_base_request"
            builder.returns = None
            builder.args.args = [arg for arg in builder.args.args if arg.arg != "api_key"]
            builder.body = [statement for statement in builder.body
                            if not (isinstance(statement, ast.Assign)
                                    and any(isinstance(target, ast.Name) and target.id == "key"
                                            for target in statement.targets))
                            and not (isinstance(statement, ast.If)
                                     and isinstance(statement.test, ast.UnaryOp)
                                     and isinstance(statement.test.operand, ast.Name)
                                     and statement.test.operand.id == "key")]
            # Keep the base implementation through its `body` assignment.
            body_index = next(index for index, statement in enumerate(builder.body)
                              if isinstance(statement, ast.Assign)
                              and any(isinstance(target, ast.Name) and target.id == "body"
                                      for target in statement.targets))
            builder.body = builder.body[:body_index + 1] + [ast.Return(value=ast.Name(id="body", ctx=ast.Load()))]
            selected.append(builder)
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    namespace = {"re": re, "json": json, "difflib": difflib,
                 "unicodedata": unicodedata, "AnalysisMode": str,
                 "ServiceError": RuntimeError,
                 "invalid_response_error": lambda stage: EvaluationError("invalid_response")}
    exec(compile(module, f"{BASE_REF}:core/draft.py", "exec"), namespace)
    return namespace


def baseline_request(case: dict) -> dict:
    messages = case["messages"]
    return _base_namespace()["build_base_request"](messages, case["relationship"],
        keep=DRAFT_CONTEXT_KEEP,
        mode="follow_up" if messages[-1][0] == "me" else "reply",
        reply_goal=case["goal"], style=case["style"], reply_to=case.get("reply_to"))


def baseline_candidates(content: str, case: dict) -> list[str]:
    base = _base_namespace()
    messages = case["messages"]
    suspects = base["_suspects"](messages, DRAFT_CONTEXT_KEEP)
    return base["_deduplicate_candidates"](base["_sanitize"](
        base["_parse_candidates"](content), suspects, base["_her_recent"](messages)))


def _with_price_cap(body: dict, pricing: dict) -> dict:
    prompt, completion = _ceiling_prices(pricing)
    guarded = dict(body)
    guarded["provider"] = {**body["provider"], "max_price": {
        "prompt": float(prompt * 1_000_000),
        "completion": float(completion * 1_000_000),
        "request": 0,
    }}
    return guarded


def _paid_request(body: dict, api_key: str) -> dict:
    request = urllib.request.Request(draft.CHAT_URL, method="POST",
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    return _urlopen_json(request, timeout=60)


def _paid_jev_request(body: dict, api_key: str) -> dict:
    request = urllib.request.Request(JEV_URL, method="POST",
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    return _urlopen_json(request)


def run_jev(ledger: BudgetLedger, case: dict, candidates: tuple[str, ...], api_key: str) -> dict:
    messages = normalize_messages(case["messages"])
    targets = select_turn_targets(messages, limit=DRAFT_CONTEXT_KEEP)
    mode = "follow_up" if messages[-1]["from"] == "me" else "reply"
    state = build_adaptive_state(messages, case["relationship"], targets,
        case.get("reply_to"), mode=mode, reply_goal=case["goal"], style=case["style"])
    questions = build_adaptive_questions(targets, candidates, mode=mode, style=case["style"])
    body = {"model": JEV_MODEL, "state": state, "questions": questions,
            "provider": {"max_price": {"prompt": 0.05, "completion": 0, "request": 0}}}
    payload_bytes = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    state_bytes = len(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    question_bound = sum(
        state_bytes + len(json.dumps(question, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        + OVERHEAD_TOKENS for question in questions.values()
    )
    ceiling = Decimal(max(payload_bytes + OVERHEAD_TOKENS, question_bound)) * Decimal("0.05") / Decimal(1_000_000)
    response = budgeted_call(ledger, ceiling, lambda: _paid_jev_request(body, api_key))
    try:
        result = parse_adaptive_result(response, messages, targets, candidates, "ready",
            case.get("reply_to"), mode=mode, reply_goal=case["goal"])
    except Exception as exc:
        raise _ProviderResponseError(response) from exc
    view = project_result(result)
    return {"reply_state": view.reply_state, "best_index": result.best_index,
            "reply_scores": result.reply_scores,
            "safety_probabilities": result.candidate_safety_probabilities,
            "reply_worthwhile_probability": result.reply_worthwhile_probability,
            "turns": [{"id": turn.turn_id, "intent": turn.intent,
                       "tension": turn.tension_score, "action": turn.action,
                       "resolved_probability": turn.resolved_probability}
                      for turn in result.turns],
            "cards": [card.rows for card in view.turn_cards],
            "usage_cost_usd": response.get("usage", {}).get("cost")}


def _run_one(ledger: BudgetLedger, name: str, case: dict, arm: str, model: str,
             pricing: dict, api_key: str, with_jev: bool = False) -> dict:
    if arm == "baseline":
        body = baseline_request(case)
    else:
        mode = "follow_up" if case["messages"][-1][0] == "me" else "reply"
        body = draft.build_draft_request(case["messages"], case["relationship"],
            model=model, reply_to=case.get("reply_to"), style=case["style"],
            mode=mode, reply_goal=case["goal"], keep=DRAFT_CONTEXT_KEEP)
    body = _with_price_cap(body, pricing)
    ceiling = reservation_usd(body, pricing)
    response = budgeted_call(ledger, ceiling, lambda: _paid_request(body, api_key))
    usage_cost = None
    usage = response.get("usage") if isinstance(response, dict) else None
    if isinstance(usage, dict) and usage.get("cost") is not None:
        try:
            usage_cost = str(_amount(usage["cost"]))
        except EvaluationError:
            usage_cost = None
    output = {"case": name, "arm": arm, "model": body["model"],
              "usage_cost_usd": usage_cost, "reject_if": case["reject_if"]}
    try:
        content = response["choices"][0]["message"]["content"]
        if arm == "baseline":
            candidates = baseline_candidates(content, case)
        else:
            candidates = draft._parse_candidates(content)
            candidates = draft._deduplicate_candidates(draft._sanitize(
                candidates, draft._suspects(case["messages"], DRAFT_CONTEXT_KEEP),
                draft._her_recent(case["messages"]), case["messages"][-DRAFT_CONTEXT_KEEP:]))
        output["candidates"] = candidates
    except Exception as exc:
        output["error"] = safe_error(exc)
        output["diagnostic"] = _provider_diagnostic(response)
        output["halted"] = True
        return output
    if with_jev:
        try:
            output["jev"] = run_jev(ledger, case, tuple(candidates), api_key)
        except _ProviderResponseError as exc:
            output["jev"] = {"error": safe_error(exc),
                              "diagnostic": _provider_diagnostic(exc.response)}
            output["halted"] = True
        except Exception as exc:
            output["jev"] = {"error": safe_error(exc)}
            output["halted"] = True
    return output


def _run(args) -> int:
    if os.name != "nt":
        raise EvaluationError("paid evaluation requires native Windows")
    cases = selected_cases(args.case)
    BUILD.mkdir(parents=True, exist_ok=True)
    lock = BUILD / "runner.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise EvaluationError("concurrent runner") from None
    try:
        os.close(fd)
        # Public metadata first. No key is loaded until models and ceiling are checked.
        prices = {args.candidate_model: public_pricing(args.candidate_model)}
        if args.baseline:
            base_model = baseline_request(cases[0][1])["model"]
            prices[base_model] = public_pricing(base_model)
        from app.credentials import load_saved_key
        key = load_saved_key()
        if not key:
            raise EvaluationError("missing saved key")
        ledger = BudgetLedger(BUILD / "ledger.json")
        output = []
        halted = False
        for name, case in cases:
            if halted:
                break
            arms = (("baseline", baseline_request(case)["model"]), ("candidate", args.candidate_model)) if args.baseline else (("candidate", args.candidate_model),)
            for arm, model in arms:
                try:
                    row = _run_one(ledger, name, case, arm, model, prices[model], key, args.with_jev)
                    output.append(row)
                    if row.get("halted"):
                        halted = True
                        break
                except Exception as exc:
                    output.append({"case": name, "arm": arm, "model": model,
                                   "error": safe_error(exc), "reject_if": case["reject_if"]})
                    halted = True
                    break
        path = BUILD / ("synthetic-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                        + "-" + uuid.uuid4().hex[:8] + ".json")
        path.write_text(json.dumps({"source": "named synthetic cases only", "results": output},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"synthetic output: {path}")
        print(f"ledger total USD: {ledger.total_usd()}")
        return 1 if halted else 0
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list fixed fictional case IDs")
    parser.add_argument("--run", action="store_true", help="authorize paid synthetic evaluation")
    parser.add_argument("--case", action="append", help="fixed case ID; repeatable")
    parser.add_argument("--candidate-model", default=draft.DEFAULT_MODEL)
    parser.add_argument("--baseline", action="store_true", help="also call the base prompt")
    parser.add_argument("--with-jev", action="store_true", help="also evaluate each candidate set through Jev")
    args = parser.parse_args(argv)
    try:
        if args.list:
            for name, _ in selected_cases(args.case):
                print(name)
            return 0
        if not args.run:
            parser.error("use --list or explicit --run")
        return _run(args)
    except Exception as exc:
        print(safe_error(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
