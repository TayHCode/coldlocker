import json
import re
import sys

RECOMMENDED = re.compile(r"\(recommended\)", re.IGNORECASE)
ASK_HUMAN = re.compile(
    r"[$€£¥]|\b(pay|purchase|buy|spend|money|cost|price|bill|charge|subscri|invoice|refund|budget|renew|upgrad|trial|usd|eur|"
    r"publish|public|release|deploy|ship|visib|open.?source|"
    r"delet|remov|destroy|drop|wipe|eras|purg|truncat|terminat|uninstall|overwrit|force.?push|"
    r"send|email|mail|messag|post|notify|tweet|dm|sms|invit|share|announc|reply|comment)\w*",
    re.IGNORECASE,
)


def picks(tool_input):
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(questions, list) or not questions:
        return None
    chosen = []
    for question in questions:
        if not isinstance(question, dict):
            return None
        options = question.get("options") or []
        text = " ".join([str(question.get("question", "")), str(question.get("header", ""))]
                        + [f"{o.get('label', '')} {o.get('description', '')}" for o in options if isinstance(o, dict)])
        if ASK_HUMAN.search(text):
            return None
        recommended = [o.get("label", "") for o in options if isinstance(o, dict) and RECOMMENDED.search(str(o.get("label", "")))]
        if len(recommended) != 1:
            return None
        chosen.append(f"{question.get('question', '')} -> {recommended[0]}")
    return chosen


def decision(payload):
    chosen = picks(payload.get("tool_input"))
    if not chosen:
        return {}
    reason = ("Do not ask the user. Choose the recommended option and continue:\n"
              + "\n".join(chosen)
              + "\nException: if that choice spends money, publishes, deletes or messages people, "
              "ask the user in plain text instead.")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    print(json.dumps(decision(payload) if isinstance(payload, dict) else {}))


if __name__ == "__main__":
    main()
