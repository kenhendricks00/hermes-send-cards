#!/usr/bin/env python3
"""Draft rules: which recipients may skip the draft card, and which agents have cards turned off.

Usage:
  draft_rules.py check <email|chat|dm> <recipient> [--bot NAME]   prints allow | approval | disabled
  draft_rules.py allow <email|chat|dm> <recipient>                 add an Allow rule
  draft_rules.py approve <email|chat|dm> <recipient>               remove the Allow rule (needs approval again)
  draft_rules.py disable-bot <name>                                 turn draft cards off for an agent
  draft_rules.py enable-bot <name>                                  turn them back on
  draft_rules.py list                                               print all rules as JSON

Rules live in $HERMES_HOME/draft_rules.json. Without HERMES_HOME the default is
%LOCALAPPDATA%\\hermes on Windows and ~/.hermes elsewhere. Nothing else is read or written.
"""
import json
import os
import sys
import tempfile

KINDS = ("email", "chat", "dm")
DEFAULT_BOT = "Hermes"


def home():
    h = os.environ.get("HERMES_HOME")
    if h:
        return h
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def rules_path():
    return os.path.join(home(), "draft_rules.json")


def empty():
    return {"allow": [], "disabled_bots": []}


def load():
    try:
        with open(rules_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return empty()
    if not isinstance(d, dict):
        return empty()
    return {"allow": [x for x in d.get("allow", []) if isinstance(x, str)],
            "disabled_bots": [x for x in d.get("disabled_bots", []) if isinstance(x, str)]}


def save(d):
    path = rules_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".draft_rules.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def key(kind, recipient):
    kind = kind.strip().lower()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    r = recipient.strip().lower()
    if not r:
        raise ValueError("recipient is empty")
    return f"{kind}:{r}"


def check(kind, recipient, bot=DEFAULT_BOT):
    d = load()
    if bot.strip().lower() in (b.lower() for b in d["disabled_bots"]):
        return "disabled"
    return "allow" if key(kind, recipient) in d["allow"] else "approval"


def allow(kind, recipient):
    k = key(kind, recipient)
    d = load()
    if k not in d["allow"]:
        d["allow"].append(k)
        save(d)
    return k


def approve(kind, recipient):
    k = key(kind, recipient)
    d = load()
    d["allow"] = [x for x in d["allow"] if x != k]
    save(d)
    return k


def disable_bot(bot):
    bot = bot.strip()
    if not bot:
        raise ValueError("agent name is empty")
    d = load()
    if bot.lower() not in (b.lower() for b in d["disabled_bots"]):
        d["disabled_bots"].append(bot)
        save(d)
    return bot


def enable_bot(bot):
    bot = bot.strip()
    d = load()
    d["disabled_bots"] = [b for b in d["disabled_bots"] if b.lower() != bot.lower()]
    save(d)
    return bot


USAGE = __doc__.split("Rules live")[0].strip()


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    bot = DEFAULT_BOT
    if "--bot" in a:
        i = a.index("--bot")
        if i + 1 >= len(a):
            print("draft_rules: --bot needs a name", file=sys.stderr)
            return 2
        bot = a[i + 1]
        del a[i:i + 2]
    cmd, args = (a[0], a[1:]) if a else ("list", [])
    need = {"list": 0, "check": 2, "allow": 2, "approve": 2, "disable-bot": 1, "enable-bot": 1}
    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if cmd not in need:
        print(f"draft_rules: unknown command {cmd!r}\n\n{USAGE}", file=sys.stderr)
        return 2
    if len(args) != need[cmd]:
        print(f"draft_rules: {cmd} takes {need[cmd]} argument(s)\n\n{USAGE}", file=sys.stderr)
        return 2
    try:
        if cmd == "list":
            print(json.dumps(load(), indent=1))
        elif cmd == "check":
            print(check(args[0], args[1], bot))
        elif cmd == "allow":
            print("allowed", allow(*args))
        elif cmd == "approve":
            print("needs approval", approve(*args))
        elif cmd == "disable-bot":
            print("draft cards off for", disable_bot(args[0]))
        elif cmd == "enable-bot":
            print("draft cards on for", enable_bot(args[0]))
    except ValueError as e:
        print(f"draft_rules: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
