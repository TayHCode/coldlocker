"""Single-process local proofs, not a durable production executor or Eve runtime."""
import json
import os
from pathlib import Path


class Interrupted(Exception):
    pass


def run_steps(steps, state_file, interrupt_after=None, ledger=None):
    state_file = Path(state_file)
    done = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    for name, step in steps:
        if name in done:
            continue
        result = step(ledger)
        if interrupt_after == (name, "before-receipt"):
            raise Interrupted(name)
        done[name] = result
        temporary = state_file.with_suffix(".pending")
        temporary.write_text(json.dumps(done), encoding="utf-8")
        os.replace(temporary, state_file)
        if interrupt_after == (name, "after-receipt"):
            raise Interrupted(name)
    return done


def keyed_effect(key, ledger_file, payload):
    ledger_file = Path(ledger_file)
    lines = ledger_file.read_text(encoding="utf-8").splitlines() if ledger_file.exists() else []
    if any(json.loads(line)["key"] == key for line in lines):
        return "already-applied"
    with ledger_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"key": key, "payload": payload}) + "\n")
    return "applied"


def unkeyed_effect(ledger_file, payload):
    with Path(ledger_file).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"payload": payload}) + "\n")
    return "applied"


def total(trace):
    return {field: sum(turn[field] for turn in trace) for field in ("input", "cached", "output")}


def record_turn(trace, input_tokens, cached_tokens, output_tokens):
    if any(type(value) is not int or value < 0 for value in (input_tokens, cached_tokens, output_tokens)):
        raise ValueError("token counts must be nonnegative integers")
    if cached_tokens > input_tokens:
        raise ValueError("cached tokens cannot exceed input tokens")
    trace.append({"turn": len(trace) + 1, "input": input_tokens, "cached": cached_tokens, "output": output_tokens})


def median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def median_buggy(values):
    return sorted(values)[len(values) // 2]


EVAL_CASES = [([3, 1, 2], 2), ([4, 1, 3, 2], 2.5), ([7], 7)]


def run_eval(function):
    failures = []
    for values, expected in EVAL_CASES:
        actual = function(values)
        if actual != expected:
            failures.append((values, expected, actual))
    return {"passed": len(EVAL_CASES) - len(failures), "total": len(EVAL_CASES), "failures": failures}
