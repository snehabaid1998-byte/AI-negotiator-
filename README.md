# AI Negotiator

A virtual negotiation coach you chat with in the terminal. It helps you:

- **Get the best deal.** Buying or selling a car, a home, a contract, rent, a freelance rate.
- **Negotiate a salary or raise.** Offer counters, market data, the full package, scripts.
- **Talk your way into a startup.** Pitch a founder, design a proof-of-value offer, and negotiate role, cash and equity.

It runs an intake, researches market data on the web, computes the numbers with
local tools (ZOPA and anchor, concession ladder, salary counter, equity value,
offer comparison), and writes a brief with a script, likely objections and a draft
message. It can also **role-play your counterpart** so you can practice first.

Built on **Claude Managed Agents** (beta) with `claude-opus-5-5`. Anthropic hosts
the agent loop. The number-crunching tools run on your machine as custom tools.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # or: ant auth login
python -m negotiator.setup_agent         # one time: creates the agent + environment
python -m negotiator                     # start chatting
```

`setup_agent` saves the agent and environment IDs to `.negotiator.json`. It creates
them once and reuses them. Run it again with `--force` after you change the prompt or tools.

Options:

| Command | What it does |
|---|---|
| `python -m negotiator --scenario salary` | Skip the menu (`deal`, `salary`, `startup`, `other`) |
| `python -m negotiator --resume sesn_...` | Continue an earlier session |
| `python -m negotiator --budget 2` | Hard spend cap for the session in USD (default $5) |
| `python -m negotiator.setup_agent --allow-host www.autotrader.com` | Let web research use another site |
| `python -m negotiator.setup_agent --no-web` | Turn off web research |

In-chat commands: `/practice` (the agent plays your counterpart), `/debrief`
(feedback on how you did), `/brief` (save the final brief to `./briefs/`), `/quit`.

## How it works

```
you ⇄ negotiator/cli.py ──events──▶ Managed Agents session (Claude Opus 5.5)
                ▲                         │  web_search / web_fetch (allow-listed sites)
                └── custom tool results ◀─┘  custom tool calls → negotiator/tools.py
```

- `negotiator/prompts.py`: the coach's system prompt (intake, strategy principles, the three modes, ethics) and the scenario openers.
- `negotiator/tools.py`: deterministic tools (`analyze_bargaining_range`, `plan_concessions`, `salary_counter`, `value_startup_equity`, `compare_offers`, `save_negotiation_brief`).
- `negotiator/setup_agent.py`: creates the environment (limited networking, research sites only) and the agent. Shell and file tools are off. Only web search and fetch are on.
- `negotiator/cli.py`: the chat loop. It streams events, runs custom tools, and asks you before any server tool call the platform flags.

## Cost and data

Managed Agents is in beta. It bills session runtime (about $0.08/hour) on top of
tokens and web searches. Each session has a hard spend cap (`--budget`). Session
history is kept on Anthropic's side until you delete it. You can view sessions in
the Claude Console.

The coach helps you negotiate honestly. It won't fabricate offers or deadlines. It
is not legal, tax or financial advice.

## Tests

```bash
pip install pytest && python -m pytest -q
```
