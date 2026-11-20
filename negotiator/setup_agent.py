"""One-time setup: create the Managed Agents environment and agent.

Run once:   python -m negotiator.setup_agent
Re-create:  python -m negotiator.setup_agent --force

The IDs are stored in .negotiator.json and reused by every chat session
(agents are persistent, versioned objects - never create one per chat).
"""

from __future__ import annotations

import argparse
import json
import sys

import anthropic

from .config import CONFIG_PATH, MODEL, load_config
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS

# Sites the agent's web search / fetch may use for market data. Under
# `limited` networking these are the ONLY hosts web tools can reach.
# Add your own (e.g. a local car or real-estate site) with --allow-host.
RESEARCH_HOSTS = [
    # compensation
    "www.levels.fyi", "www.glassdoor.com", "www.payscale.com", "www.bls.gov",
    "www.indeed.com", "www.linkedin.com", "builtin.com", "wellfound.com",
    # startups / equity
    "www.crunchbase.com", "carta.com", "techcrunch.com", "www.ycombinator.com",
    "news.ycombinator.com", "pitchbook.com",
    # prices / deals
    "www.kbb.com", "www.edmunds.com", "www.zillow.com", "www.redfin.com",
    "www.amazon.com", "camelcamelcamel.com", "www.consumerreports.org",
    # general
    "en.wikipedia.org",
]


def build_tools(hosts: list[str]) -> list[dict]:
    return [
        {
            "type": "agent_toolset_20260401",
            # The coach needs no shell or files in its sandbox - only the web.
            "default_config": {"enabled": False, "permission_policy": {"type": "auto"}},
            "configs": [
                {"name": "web_search", "enabled": True, "allowed_domains": hosts},
                {"name": "web_fetch", "enabled": True, "allowed_domains": hosts},
            ],
        },
        *TOOL_DEFINITIONS,
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="create a new agent/environment even if configured")
    ap.add_argument("--allow-host", action="append", default=[], help="extra research host (repeatable)")
    ap.add_argument("--no-web", action="store_true", help="disable web research entirely")
    args = ap.parse_args(argv)

    existing = load_config()
    if existing and not args.force:
        print(f"Already set up ({CONFIG_PATH}): agent {existing['agent_id']}. Use --force to recreate.")
        return 0

    client = anthropic.Anthropic()
    hosts = sorted(set(RESEARCH_HOSTS + args.allow_host))

    environment = client.beta.environments.create(
        name="negotiator-env",
        config={
            "type": "cloud",
            "networking": {"type": "limited", "allowed_hosts": [] if args.no_web else hosts},
        },
    )
    tools = build_tools(hosts)
    if args.no_web:
        tools[0]["configs"] = [
            {"name": "web_search", "enabled": False},
            {"name": "web_fetch", "enabled": False},
        ]

    agent = client.beta.agents.create(
        name="Negotiator",
        description="Virtual negotiation coach: deals, salary, joining startups.",
        model=MODEL,
        system=SYSTEM_PROMPT,
        tools=tools,
    )

    cfg = {"agent_id": agent.id, "agent_version": agent.version, "environment_id": environment.id}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"Created environment {environment.id} and agent {agent.id} (v{agent.version}).")
    print(f"Saved to {CONFIG_PATH}. Now run:  python -m negotiator")
    return 0


if __name__ == "__main__":
    sys.exit(main())
