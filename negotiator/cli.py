"""Interactive terminal chat with the Negotiator agent.

Usage:
  python -m negotiator                    # choose a scenario
  python -m negotiator --scenario salary  # deal | salary | startup | other
  python -m negotiator --resume sesn_...  # continue an earlier session

In-chat commands:
  /practice   the agent role-plays your counterpart so you can rehearse
  /debrief    end practice and get feedback
  /brief      ask for the final brief and save it to ./briefs/
  /quit       exit (the session is kept; resume it with --resume)
"""

from __future__ import annotations

import argparse
import json
import sys

import anthropic

from .config import load_config
from .prompts import SCENARIOS
from .tools import run_tool

BUDGET_CENTS_DEFAULT = 500  # $5.00 hard cap per session at list price

COMMANDS = {
    "/practice": (
        "Let's practice. Role-play my counterpart realistically based on everything you "
        "know so far. Open the conversation in character and stay in character until I "
        "say /debrief."
    ),
    "/debrief": "/debrief - step out of character and give me specific feedback on how I did.",
    "/brief": (
        "Put together the final negotiation brief (strategy, key numbers, script, "
        "objections and responses, draft message) and save it with save_negotiation_brief."
    ),
}


def _say(text: str = "", end: str = "\n") -> None:
    print(text, end=end, flush=True)


def _confirm(event) -> bool:
    """Ask the human before a server tool call the platform flagged for review."""
    _say(f"\n[agent wants to run {event.name}: {json.dumps(event.input)[:200]}]")
    try:
        return input("Allow? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run_turn(client: anthropic.Anthropic, session_id: str, text: str) -> str:
    """Send one user message and stream the agent's work until it finishes the turn.

    Returns the idle stop_reason type ("end_turn", "retries_exhausted",
    "budget_reached") or "terminated".
    """
    # Stream-first: open the stream, then send, so no early events are missed.
    with client.beta.sessions.events.stream(session_id=session_id) as stream:
        client.beta.sessions.events.send(
            session_id=session_id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": text}]}],
        )
        printed_header = False
        for event in stream:
            t = event.type
            if t == "agent.message":
                if not printed_header:
                    _say("\nNegotiator: ", end="")
                    printed_header = True
                for block in event.content:
                    if block.type == "text":
                        _say(block.text, end="")
                _say()
            elif t == "agent.tool_use":
                if event.name in ("web_search", "web_fetch"):
                    q = event.input.get("query") or event.input.get("url") or ""
                    _say(f"  · researching: {q}")
                if getattr(event, "evaluated_permission", None) == "ask":
                    ok = _confirm(event)
                    client.beta.sessions.events.send(
                        session_id=session_id,
                        events=[{
                            "type": "user.tool_confirmation",
                            "tool_use_id": event.id,
                            "result": "allow" if ok else "deny",
                        }],
                    )
            elif t == "agent.custom_tool_use":
                _say(f"  · computing: {event.name}")
                result, is_error = run_tool(event.name, event.input)
                if event.name == "save_negotiation_brief" and not is_error:
                    _say(f"  · saved brief to {json.loads(result)['saved_to']}")
                client.beta.sessions.events.send(
                    session_id=session_id,
                    events=[{
                        "type": "user.custom_tool_result",
                        "custom_tool_use_id": event.id,
                        "content": [{"type": "text", "text": result}],
                        "is_error": is_error,
                    }],
                )
            elif t == "session.error":
                _say(f"\n[session error: {getattr(event, 'error', event)}]")
            elif t == "session.status_terminated":
                return "terminated"
            elif t == "session.status_idle":
                reason = event.stop_reason.type
                if reason == "requires_action":
                    continue  # waiting on a tool result/confirmation we are sending
                return reason
    return "end_turn"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=list(SCENARIOS))
    ap.add_argument("--resume", metavar="SESSION_ID")
    ap.add_argument("--budget", type=float, default=BUDGET_CENTS_DEFAULT / 100,
                    help="hard spend cap per session in USD (list price), default 5")
    args = ap.parse_args(argv)

    cfg = load_config()
    if not cfg:
        _say("Not set up yet. Run:  python -m negotiator.setup_agent")
        return 1
    client = anthropic.Anthropic()

    if args.resume:
        session_id = args.resume
        first = "I'm back. Briefly recap where we were and what's next."
    else:
        key = args.scenario
        if not key:
            _say("What do you want to negotiate?")
            keys = list(SCENARIOS)
            for i, k in enumerate(keys, 1):
                _say(f"  {i}. {SCENARIOS[k][0]}")
            choice = input("> ").strip()
            key = keys[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(keys) else "other"
        title, first = SCENARIOS[key]
        session = client.beta.sessions.create(
            agent={"type": "agent", "id": cfg["agent_id"], "version": cfg["agent_version"]},
            environment_id=cfg["environment_id"],
            title=f"Negotiator: {title}",
            budget={
                "type": "limit",
                "max_list_cost": {"amount": str(max(1, round(args.budget * 100))), "currency": "USD"},
            },
        )
        session_id = session.id
        _say(f"(session {session_id} - resume later with --resume {session_id})")
        _say("Commands: /practice  /debrief  /brief  /quit\n")

    text = first
    while True:
        try:
            reason = run_turn(client, session_id, text)
        except anthropic.APIStatusError as e:
            _say(f"\n[API error {e.status_code}: {e.message}]")
            reason = "error"
        if reason == "budget_reached":
            _say("\n[Session spend cap reached. Start a new session or raise --budget.]")
            return 0
        if reason in ("terminated", "retries_exhausted"):
            _say(f"\n[Session ended: {reason}]")
            return 1
        try:
            text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            text = "/quit"
        while not text:
            text = input("You: ").strip()
        if text in ("/quit", "/exit"):
            _say(f"Bye. Resume with: python -m negotiator --resume {session_id}")
            return 0
        text = COMMANDS.get(text, text)


if __name__ == "__main__":
    sys.exit(main())
