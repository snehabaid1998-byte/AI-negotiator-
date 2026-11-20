"""Interactive terminal chat with the Negotiator agent (runs locally).

Usage:
  python -m negotiator                    # choose a scenario
  python -m negotiator --scenario salary  # deal | salary | startup | other
  python -m negotiator --list             # list saved conversations
  python -m negotiator --resume ID        # continue a saved conversation
  python -m negotiator --no-web           # no web research

In-chat commands:
  /practice   the agent role-plays your counterpart so you can rehearse
  /debrief    end practice and get feedback
  /brief      ask for the final brief and save it to ./briefs/
  /cost       show the estimated API cost so far
  /quit       exit (the conversation is saved; resume it with --resume)
"""

from __future__ import annotations

import argparse
import sys

import anthropic

from .agent import NegotiatorAgent
from .prompts import SCENARIOS

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


def _read(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return "/quit"


def _pick_scenario() -> str:
    print("What do you want to negotiate?")
    keys = list(SCENARIOS)
    for i, k in enumerate(keys, 1):
        print(f"  {i}. {SCENARIOS[k][0]}")
    choice = _read("> ")
    return keys[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(keys) else "other"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=list(SCENARIOS))
    ap.add_argument("--resume", metavar="ID")
    ap.add_argument("--list", action="store_true", help="list saved conversations")
    ap.add_argument("--no-web", action="store_true", help="disable web research")
    args = ap.parse_args(argv)

    if args.list:
        ids = NegotiatorAgent.list_sessions()
        print("\n".join(ids) if ids else "No saved conversations yet.")
        return 0

    try:
        if args.resume:
            agent = NegotiatorAgent.load(args.resume, **({"web": False} if args.no_web else {}))
            first = "I'm back. Briefly recap where we were and what's next."
        else:
            key = args.scenario or _pick_scenario()
            agent = NegotiatorAgent(session_id=None, web=not args.no_web)
            agent.session_id += f"-{key}"
            first = SCENARIOS[key][1]
    except FileNotFoundError:
        print(f"No saved conversation '{args.resume}'. Try --list.")
        return 1
    except anthropic.AnthropicError as e:  # e.g. no API key configured
        print(f"Could not start: {e}")
        return 1

    print(f"(conversation {agent.session_id} - saved to {agent.path})")
    print("Commands: /practice  /debrief  /brief  /cost  /quit")

    text = first
    while True:
        print("\nNegotiator:", end="")
        try:
            agent.send(text)
        except anthropic.AuthenticationError:
            print("\n[Your API key was rejected. Check ANTHROPIC_API_KEY.]")
            return 1
        except anthropic.RateLimitError:
            print("\n[Rate limited - wait a moment and send your message again.]")
        except anthropic.APIStatusError as e:
            print(f"\n[API error {e.status_code}: {e.message}]")
        except anthropic.APIConnectionError:
            print("\n[Could not reach the Claude API - check your internet connection.]")
        print()

        while True:
            text = _read("\nYou: ")
            if text == "/cost":
                print(f"Estimated API cost so far: ${agent.cost_usd():.2f}")
            elif text:
                break
        if text in ("/quit", "/exit"):
            print(f"Saved. Resume with: python -m negotiator --resume {agent.session_id}")
            return 0
        text = COMMANDS.get(text, text)


if __name__ == "__main__":
    sys.exit(main())
