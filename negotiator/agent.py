"""The negotiator agent loop - runs entirely on your machine.

Each user turn: stream a Claude response; if Claude calls a local tool, run it
here and send the result back; repeat until Claude finishes the turn. The
conversation is kept in memory and saved to ./sessions/<id>.json so you can
resume it later. Only the model calls go to the Claude API.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import anthropic

from .prompts import SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, run_tool

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TOKENS = 32000
MAX_STEPS_PER_TURN = 12  # safety cap on tool round-trips per user message
SESSIONS_DIR = Path("sessions")

# List prices (USD per million tokens) for the running cost estimate.
PRICE_IN, PRICE_OUT = 4.00, 20.00
PRICE_CACHE_WRITE, PRICE_CACHE_READ = 5.00, 0.20  # write = 1.25x input; read from pricing table
PRICE_PER_SEARCH = 0.01

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


def build_tools(web: bool = True) -> list[dict[str, Any]]:
    local = [{**t, "eager_input_streaming": True} for t in TOOL_DEFINITIONS]
    return ([WEB_SEARCH_TOOL] if web else []) + local


class Printer:
    """Callbacks for showing progress. Override for a different UI."""

    def text(self, chunk: str) -> None:
        print(chunk, end="", flush=True)

    def status(self, msg: str) -> None:
        print(f"\n  · {msg}", flush=True)


class NegotiatorAgent:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        session_id: str | None = None,
        web: bool = True,
        printer: Printer | None = None,
        sessions_dir: Path = SESSIONS_DIR,
    ) -> None:
        self.client = client or anthropic.Anthropic()
        self.web = web
        self.tools = build_tools(web)
        self.printer = printer or Printer()
        self.sessions_dir = sessions_dir
        self.session_id = session_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.messages: list[dict[str, Any]] = []
        self.usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "searches": 0}

    # ------------------------------------------------------------ persistence
    @property
    def path(self) -> Path:
        return self.sessions_dir / f"{self.session_id}.json"

    def save(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        data = {"session_id": self.session_id, "model": MODEL, "web": self.web,
                "usage": self.usage, "messages": self.messages}
        self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, session_id: str, **kw: Any) -> "NegotiatorAgent":
        sessions_dir = kw.get("sessions_dir", SESSIONS_DIR)
        data = json.loads((Path(sessions_dir) / f"{session_id}.json").read_text(encoding="utf-8"))
        kw.setdefault("web", data.get("web", True))
        agent = cls(session_id=session_id, **kw)
        agent.messages = data["messages"]
        agent.usage.update(data.get("usage", {}))
        return agent

    @staticmethod
    def list_sessions(sessions_dir: Path = SESSIONS_DIR) -> list[str]:
        return sorted(p.stem for p in Path(sessions_dir).glob("*.json"))

    # ------------------------------------------------------------ cost
    def cost_usd(self) -> float:
        u = self.usage
        return (
            u["input"] * PRICE_IN + u["output"] * PRICE_OUT
            + u["cache_write"] * PRICE_CACHE_WRITE + u["cache_read"] * PRICE_CACHE_READ
        ) / 1e6 + u["searches"] * PRICE_PER_SEARCH

    def _track(self, usage: Any) -> None:
        self.usage["input"] += usage.input_tokens or 0
        self.usage["output"] += usage.output_tokens or 0
        self.usage["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.usage["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        stu = getattr(usage, "server_tool_use", None)
        self.usage["searches"] += (getattr(stu, "web_search_requests", 0) or 0) if stu else 0

    # ------------------------------------------------------------ the loop
    def _stream_once(self) -> Any:
        with self.client.beta.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=self.tools,
            messages=self.messages,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            cache_control={"type": "ephemeral"},
            # If a safety classifier declines, retry server-side on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "server_tool_use" and block.name == "web_search":
                        self.printer.status("searching the web…")
                    elif block.type == "tool_use":
                        self.printer.status(f"computing: {block.name}")
                    elif block.type == "text":
                        self.printer.text("\n")
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    self.printer.text(event.delta.text)
            return stream.get_final_message()

    def send(self, text: str) -> str:
        """Send one user message and run until Claude finishes. Returns the stop reason."""
        block = {"type": "text", "text": text}
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages[-1]["content"].append(block)  # e.g. after tool results
        else:
            self.messages.append({"role": "user", "content": [block]})
        stop = "end_turn"
        for _ in range(MAX_STEPS_PER_TURN):
            msg = self._stream_once()
            self._track(msg.usage)
            self.messages.append(
                {"role": "assistant", "content": [b.model_dump(mode="json", exclude_none=True) for b in msg.content]}
            )
            stop = msg.stop_reason

            if stop == "pause_turn":  # server-side search loop paused; resend to resume
                continue
            if stop in ("refusal", "max_tokens"):
                self.printer.status(
                    "Claude declined to continue this request. Try rephrasing."
                    if stop == "refusal" else "Response hit the length limit; ask it to continue."
                )
                # Never run a possibly truncated tool call, but answer it so the history stays valid.
                self._answer_pending_tools("not run: response was cut off")
                break
            if stop != "tool_use":
                break

            results = []
            for block in msg.content:
                if block.type != "tool_use":
                    continue
                out, is_error = run_tool(block.name, block.input)
                if block.name == "save_negotiation_brief" and not is_error:
                    self.printer.status(f"saved brief to {json.loads(out)['saved_to']}")
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": is_error}
                )
            self.messages.append({"role": "user", "content": results})
        else:
            self.printer.status("Stopped after too many tool steps this turn.")

        self.save()
        return stop

    def _answer_pending_tools(self, reason: str) -> None:
        last = self.messages[-1]
        ids = [b["id"] for b in last["content"] if last["role"] == "assistant" and b.get("type") == "tool_use"]
        if ids:
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": i, "content": reason, "is_error": True} for i in ids
            ]})
