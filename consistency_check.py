"""Deterministic checks for known contradictions in shipped agent instructions."""
import argparse
from pathlib import Path
import re

ROOTS = ("skills", "rules", "hooks", "routines")
MODEL_REFERENCE = Path("rules/model-routing.md")
TEXT_SUFFIXES = {".md", ".mdc", ".txt", ".py", ".sh", ".ps1", ".json", ".toml",
                 ".yaml", ".yml", ".js", ".jsx", ".ts", ".tsx", ".csv"}
EFFORT = re.compile(
    r"\b(?:(?:model[_ -])?reasoning[_ -]effort|effort)\s*[`\"']*\s*(?::|=|\bis\b|\bto\b)\s*[`\"']*"
    r"(?:none|minimal|low|medium|high|xhigh|max|ultra|default)\b|"
    r"(?P<prose>\b(?:low|medium|high|xhigh|max|ultra)\s+(?:reasoning\s+)?effort\b)|"
    r"--(?:reasoning-)?effort\s+(?:low|medium|high|xhigh|max|ultra)\b", re.I)
MODEL_ID = re.compile(r"\b(?:o[1-9][0-9]*(?:-[\w.*-]+)?|grok[-:][0-9*][\w.*-]*|gpt-[\w.*-]+|claude-(?:opus|sonnet|haiku|[0-9*])[\w.*-]*|(?:gemini|deepseek|qwen|llama|devstral|lfm)[-:][\w.*:-]+)", re.I)
MODEL = re.compile(MODEL_ID.pattern + r"|\b(?:opus|sonnet|haiku|astra|terra|luna|gemini|deepseek|qwen|llama|devstral|lfm)\b|\bsol\b(?![- ]loop\b)", re.I)
MODEL_PREFIX = r"(?:the\s+)?(?:latest\s+)?(?:(?:Claude|OpenAI|Google|Meta)\s+(?:[0-9.]+\s+)?)?[`\"']*"
ROUTE_BEFORE = re.compile(r"\b(?:model(?:_name)?|reviewer|verifier|provider|agent|builder|delegate)[`\"']*\s*(?:(?:is|uses?|is not|must be|to)\s*|[:=]\s*)?" + MODEL_PREFIX + r"$", re.I)
ROUTE_AFTER = re.compile(r"^[`\"']*\s+(?:as\s+(?:the\s+)?)?(?:models?|reviewers?|verifiers?|agents?|builds?|reviews?|verdict)\b", re.I)
SELECTION_BEFORE = re.compile(r"\b(?:use|select|choose|call|invoke|run|tell|ask)\s+" + MODEL_PREFIX + r"$", re.I)
NEGATION_BEFORE = re.compile(
    r"\b(?:never|not|don't|cannot|can't|avoid|forbid|reject|excluded?)\s+"
    r"(?:(?:ever|run|use|using|invoke|execute|call|require|report|return|declare|activate|enable|the|retired|independent)\s+)*[`\"']*$", re.I)
NEGATION_AFTER = re.compile(
    r"^[`\"']*\s+(?:is|are|must|should)\s+(?:(?:temporarily|currently)\s+)?"
    r"(?:retired|forbidden|prohibited|disabled|not\b|never\b)", re.I)
TERMINAL = re.compile(r"\b(?:(?i:end|finish|stop)(?:\s+(?i:the turn|the task))?\s+(?i:with|as)|(?i:final|terminal)\s+(?i:status|state|report))\s*[:=]?\s*[`\"']*(?:PARTIAL|INCOMPLETE|SUCCESS|FAILED|COMPLETE|READY|WAITING)\b", re.I)
STATUS = re.compile(r"\b(?:(?:final|terminal)\s+(?:task\s+)?(?:status|state)\s*(?::|=|\bis\b)|status\s*(?::|\bis\b))\s*[`\"']*([A-Za-z][A-Za-z_-]+(?:\s*(?:[/|,]|\bor\b)\s*(?!Status\s*:)(?:(?-i:[A-Z][A-Z_-]+)|DONE|BLOCKED|PARTIAL|INCOMPLETE|SUCCESS|FAILED|COMPLETE|READY|WAITING)\b)*)", re.I)
RETIRED = re.compile(r"\b(?:cold|fleet)\s+gate\s+(?:run|scan|authorize|complete|install|enroll)\b|\b(?:independent\s+)?Windows\s+gate\b", re.I)
ORDER = re.compile(r"\b(?:mark|report|declare|claim)\b.{0,55}?\b(?:done|complete|success)\b.{0,45}?\b(?:before|then)\b.{0,35}?\b(?:verify|verification|test|tests|proof)\b", re.I)
BYPASS = re.compile(r"\b(?:skip|bypass|ignore)\b(?!\s+when\s*:).{0,45}?\b(?:verification|proof|required checks|failing tests)\b", re.I)


def prohibited(line, match):
    before, after = line[:match.start()], line[match.end():]
    return (bool(NEGATION_BEFORE.search(before)) or bool(NEGATION_AFTER.match(after))
            or match.re is RETIRED and bool(re.match(r"^[`\"']*(?:the\s+)?retired\b", line, re.I)))


def violations(text, *, model_reference=False, data=False):
    text = re.sub(r"(?<!\w)(\*\*|__|\*|_)(?=\S)([^\n]+?)(?<=\S)\1(?!\w)",
                  lambda m: " " * len(m[1]) + m[2] + " " * len(m[1]), text)
    if not model_reference:
        lines = set()
        for pattern in (MODEL, EFFORT):
            for match in pattern.finditer(text):
                prefix, suffix = text[:match.start()], text[match.end():]
                selected = ROUTE_BEFORE.search(prefix) or SELECTION_BEFORE.search(prefix)
                if pattern is EFFORT and match.group("prose") and "reasoning" not in match[0].lower():
                    if not selected:
                        continue
                if pattern is MODEL and not MODEL_ID.fullmatch(match[0]):
                    literary = (match[0].lower() in {"haiku", "sonnet"}
                                and re.match(r"\s+as\s+(?:(?:a|the)\s+)?(?:title|heading|poem)\b", suffix, re.I))
                    if literary or not (selected or ROUTE_AFTER.match(suffix)
                            or re.match(r"-\d", suffix)
                            or not data and text.strip() == match[0]):
                        continue
                lines.add(text.count("\n", 0, match.start()) + 1)
        for number in sorted(lines):
            yield number, "model-routing", "Use the model and effort selected by model routing."

    # Fold prose wrapping while retaining instruction boundaries.
    for paragraph in re.finditer(r"[^\n]+(?:\n(?!\s*(?:$|[-*#]|\d+\.))[^\n]+)*", text):
        report_frame = re.search(r"\b(?:Goal|Done condition|Remaining):", paragraph[0], re.I)
        offset = 0
        for sentence in re.split(r"(?<=[.!?;])\s+|\s+(?i:but|however|yet)\s+", paragraph[0]):
            start = paragraph[0].index(sentence, offset)
            offset = start + len(sentence)
            line = " ".join(sentence.split())
            number = text.count("\n", 0, paragraph.start() + start) + 1
            terminal = any(not prohibited(line, m) for m in TERMINAL.finditer(line))
            last_status_end, last_status_task = 0, False
            for m in STATUS.finditer(sentence):
                prefix = sentence[:m.start()]
                task_status = (re.match(r"(?:final|terminal)\b", m[0], re.I)
                               or re.search(r"\b(?:report|return|end|finish)\s+(?:with\s+)?$", prefix, re.I)
                               or report_frame and re.search(r"(?:^|\n)\s*$", prefix)
                               or last_status_task and re.fullmatch(r"\s*(?:or|/|\||,)\s*", sentence[last_status_end:m.start()], re.I))
                last_status_end, last_status_task = m.end(), bool(task_status)
                if task_status and not prohibited(sentence, m) and any(
                    status not in {"DONE", "BLOCKED"}
                    for status in re.split(r"\s*(?:[/|,]|\bOR\b)\s*", m[1].upper())):
                    terminal = True
            if terminal:
                yield number, "terminal-status", "Task completion must use DONE or BLOCKED."
            for pattern, code, message in (
                (RETIRED, "retired-gate", "The independent gate is retired; use current project verification."),
                (ORDER, "verification-order", "Verify before claiming completion; keep-going does not waive proof."),
                (BYPASS, "verification-order", "Verify before claiming completion; keep-going does not waive proof."),
            ):
                if any(not prohibited(line, m) for m in pattern.finditer(line)):
                    yield number, code, message


def scan(root):
    root = Path(root)
    if not root.is_dir():
        return ["instruction root is missing or is not a directory"]
    errors = []
    directories = [Path(name) for name in ROOTS]
    directories += [Path(".hub-store/catalog") / name for name in ROOTS]
    for directory in directories:
        parent = root / directory
        if any((root / part).is_symlink() for part in (directory, *directory.parents)):
            errors.append(f"{directory.as_posix()}: unsafe symlink in instruction tree")
            continue
        if not parent.exists():
            continue
        if not parent.is_dir():
            errors.append(f"{directory.as_posix()}: instruction tree is not a directory")
            continue
        for path in sorted(parent.rglob("*")):
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts:
                continue
            if path.is_symlink():
                errors.append(f"{relative.as_posix()}: unsafe symlink in instruction tree")
                continue
            if not path.is_file():
                continue
            try:
                data = path.read_bytes()
            except OSError:
                errors.append(f"{relative.as_posix()}: cannot read instruction file")
                continue
            if b"\0" in data and path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                if path.suffix.lower() in TEXT_SUFFIXES:
                    errors.append(f"{relative.as_posix()}: cannot read instruction file as UTF-8")
                continue
            for line, code, message in violations(text, model_reference=relative == MODEL_REFERENCE,
                                                  data=path.suffix in {".csv", ".json"}):
                errors.append(f"{relative.as_posix()}:{line}: {code}: {message}")
    return errors


def require_consistent(root):
    errors = scan(root)
    if errors:
        raise ValueError("\n".join(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    errors = scan(args.root)
    for error in errors:
        print(error)
    print(f"FAIL: {len(errors)} consistency violation(s)" if errors else "PASS: instruction consistency")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
