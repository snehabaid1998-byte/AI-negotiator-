# AI Negotiator

A virtual negotiation coach you chat with in the terminal. It helps you:

- **Get the best deal.** Buying or selling a car, a home, a contract, rent, a freelance rate.
- **Negotiate a salary or raise.** Offer counters, market data, the full package, scripts.
- **Talk your way into a startup.** Pitch a founder, design a proof-of-value offer, and negotiate role, cash and equity.

It runs an intake, researches market data on the web, computes the numbers with
local tools (ZOPA and anchor, concession ladder, salary counter, equity value,
offer comparison), and writes a brief with a script, likely objections and a draft
message. It can also **role-play your counterpart** so you can practice first.

Everything runs **locally on your computer**: the agent loop, the calculators,
your saved conversations and briefs. The only thing that leaves your machine is
the call to the Claude API (`claude-opus-5-5`), which does the thinking and the
web searches. No server, no hosted agent, no database.

## Setup

1. Get an API key at **console.anthropic.com** (add a few dollars of credit under Billing, then **API Keys → Create Key**).
2. Install and run (Python 3.10+):

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
python -m negotiator
```

That's it. There's no separate setup step.

| Command | What it does |
|---|---|
| `python -m negotiator --scenario salary` | Skip the menu (`deal`, `salary`, `startup`, `other`) |
| `python -m negotiator --list` | List saved conversations |
| `python -m negotiator --resume 20261120-101500-salary` | Continue a saved conversation |
| `python -m negotiator --no-web` | Don't search the web |

In-chat commands: `/practice` (the agent plays your counterpart), `/debrief`
(feedback on how you did), `/brief` (save the final brief to `./briefs/`),
`/cost` (estimated API spend so far), `/quit`.

## How it works

```
you ⇄ cli.py ⇄ agent.py (loop, on your machine) ──▶ Claude API (model + web search)
                    │
                    ├─ tools.py        calculators, run locally
                    ├─ sessions/*.json your conversations
                    └─ briefs/*.md     saved negotiation briefs
```

- `negotiator/agent.py`: the agent loop. It streams Claude's reply, runs any tool Claude asks for locally, sends the result back, and repeats until the turn is done. It saves the conversation after every turn.
- `negotiator/tools.py`: deterministic calculators (`analyze_bargaining_range`, `plan_concessions`, `salary_counter`, `value_startup_equity`, `compare_offers`, `save_negotiation_brief`).
- `negotiator/prompts.py`: the coach's instructions and the scenario openers.
- `negotiator/cli.py`: the terminal chat.

## Cost and privacy

You pay only for API usage: about $4 per million input tokens and $20 per million
output tokens, plus $10 per 1,000 web searches. A typical coaching chat costs
cents to a dollar or so. Check with `/cost`. Conversations are stored only in
`./sessions/` on your machine. Delete the files to erase them.

The coach helps you negotiate honestly. It won't fabricate offers or deadlines. It
is not legal, tax or financial advice.

## Tests

```bash
pip install pytest && python -m pytest -q
```
