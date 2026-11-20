import json
from types import SimpleNamespace as NS

import pytest

from anthropic.types.beta import BetaMessage

from negotiator import tools
from negotiator.agent import NegotiatorAgent, Printer, build_tools


# ---------------- tools ----------------
def test_zopa_seller():
    r = tools.analyze_bargaining_range("seller", target=150_000, walk_away=130_000,
                                       counterpart_estimated_limit=160_000)
    assert r["zopa"] == {"low": 130_000, "high": 160_000, "width": 30_000}
    assert 150_000 <= r["recommended_anchor"] <= 176_000


def test_zopa_buyer_no_overlap():
    r = tools.analyze_bargaining_range("buyer", target=20_000, walk_away=22_000,
                                       counterpart_estimated_limit=25_000)
    assert r["zopa"] is None
    assert any("No ZOPA" in n for n in r["notes"])
    assert r["recommended_anchor"] <= 20_000


def test_zopa_rejects_inverted_targets():
    with pytest.raises(ValueError):
        tools.analyze_bargaining_range("seller", target=100, walk_away=200)


def test_concessions_shrink_and_reach_target():
    r = tools.plan_concessions(opening=180_000, target=160_000, walk_away=150_000, rounds=4)
    steps = [s["concede"] for s in r["schedule"]]
    assert steps == sorted(steps, reverse=True)
    assert r["schedule"][-1]["new_position"] == pytest.approx(160_000)


def test_concessions_buyer():
    r = tools.plan_concessions(opening=18_000, target=20_000, walk_away=22_000, rounds=3)
    assert r["direction"].startswith("buyer")
    assert r["schedule"][-1]["new_position"] == pytest.approx(20_000)


def test_equity_value():
    r = tools.value_startup_equity(ownership_percent=1.0, current_valuation=10_000_000,
                                   exit_valuation=500_000_000, future_dilution_percent=50,
                                   success_probability=0.1)
    assert r["paper_value_today"] == 100_000
    assert r["value_at_exit_if_success"] == 2_500_000
    assert r["expected_value"] == 250_000


def test_equity_from_shares():
    r = tools.value_startup_equity(shares=10_000, fully_diluted_shares=1_000_000)
    assert r["ownership_percent_now"] == 1.0


def test_compare_offers_ranks():
    r = tools.compare_offers([
        {"name": "A", "base": 150_000, "bonus": 15_000},
        {"name": "B", "base": 140_000, "equity_value_per_year": 40_000, "signing_bonus": 10_000},
    ], years=4)
    assert r["best"] == "B"
    assert r["ranked"][1]["gap_to_best_4y"] == (180_000 * 4 + 10_000) - 165_000 * 4


def test_salary_counter_with_competing_offer():
    r = tools.salary_counter(current_offer=120_000, market_p75=135_000, competing_offer=140_000)
    assert r["recommended_counter"] > 140_000
    assert r["recommended_counter"] <= 120_000 * 1.5


def test_salary_counter_capped_without_leverage():
    r = tools.salary_counter(current_offer=100_000, market_p75=200_000)
    assert r["recommended_counter"] <= 135_000


def test_save_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "BRIEFS_DIR", tmp_path / "briefs")
    r = tools.save_negotiation_brief("Salary @ Acme!", "Ask for 150k.")
    text = open(r["saved_to"]).read()
    assert text.startswith("# Salary @ Acme!") and "150k" in text
    assert r["saved_to"].endswith("salary-acme.md")


def test_run_tool_errors_are_reported_not_raised():
    out, err = tools.run_tool("plan_concessions", {"opening": 1})
    assert err and "error" in json.loads(out)
    out, err = tools.run_tool("nope", {})
    assert err


def test_tool_definitions_match_functions():
    names = {t["name"] for t in tools.TOOL_DEFINITIONS}
    assert names == set(tools.TOOL_FUNCTIONS)


def test_build_tools():
    t = build_tools(web=True)
    assert t[0]["type"] == "web_search_20260209"
    assert all(x.get("eager_input_streaming") for x in t[1:])
    assert all("type" not in x for x in t[1:])
    assert all(x["type"] != "web_search_20260209" for x in build_tools(web=False) if "type" in x)


# ---------------- agent loop (Claude API faked) ----------------
def make_message(content, stop_reason):
    return BetaMessage.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 100,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    })


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for b in self.msg.content:
            yield NS(type="content_block_start", content_block=b)
            if b.type == "text":
                yield NS(type="content_block_delta", delta=NS(type="text_delta", text=b.text))

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

        def stream(**kw):
            self.calls.append(json.loads(json.dumps(kw["messages"])))
            return FakeStream(self.replies.pop(0))

        self.beta = NS(messages=NS(stream=stream))


class Quiet(Printer):
    def __init__(self):
        self.out = []

    def text(self, chunk):
        self.out.append(chunk)

    def status(self, msg):
        self.out.append(f"[{msg}]")


def test_send_runs_local_tool_then_finishes(tmp_path):
    client = FakeClient([
        make_message([{"type": "text", "text": "Computing."},
                      {"type": "tool_use", "id": "toolu_1", "name": "salary_counter",
                       "input": {"current_offer": 100000}}], "tool_use"),
        make_message([{"type": "text", "text": "Counter at 109,750."}], "end_turn"),
    ])
    p = Quiet()
    agent = NegotiatorAgent(client=client, session_id="t", printer=p, sessions_dir=tmp_path)
    assert agent.send("hi") == "end_turn"

    second = client.calls[1]
    result = second[-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert result["is_error"] is False and "recommended_counter" in result["content"]
    assert "Counter at 109,750." in "".join(p.out)
    assert agent.usage["input"] == 2000 and agent.cost_usd() > 0

    # saved and resumable; next user text starts a new user turn
    again = NegotiatorAgent.load("t", client=FakeClient([]), sessions_dir=tmp_path)
    assert again.messages == agent.messages
    assert [m["role"] for m in again.messages] == ["user", "assistant", "user", "assistant"]


def test_truncated_tool_call_is_not_run_and_history_stays_valid(tmp_path):
    client = FakeClient([
        make_message([{"type": "tool_use", "id": "toolu_9", "name": "save_negotiation_brief",
                       "input": {"title": "x"}}], "max_tokens"),
        make_message([{"type": "text", "text": "ok"}], "end_turn"),
    ])
    agent = NegotiatorAgent(client=client, session_id="t2", printer=Quiet(), sessions_dir=tmp_path)
    assert agent.send("save it") == "max_tokens"
    last = agent.messages[-1]
    assert last["role"] == "user" and last["content"][0]["is_error"] is True
    assert not (tmp_path / "briefs").exists()
    # the next message merges into that user turn rather than making two user turns in a row
    agent.send("continue")
    roles = [m["role"] for m in client.calls[1]]
    assert roles == ["user", "assistant", "user"]
    assert client.calls[1][-1]["content"][-1] == {"type": "text", "text": "continue"}


def test_pause_turn_resumes_without_extra_user_message(tmp_path):
    client = FakeClient([
        make_message([{"type": "text", "text": "Searching"}], "pause_turn"),
        make_message([{"type": "text", "text": "Done"}], "end_turn"),
    ])
    agent = NegotiatorAgent(client=client, session_id="t3", printer=Quiet(), sessions_dir=tmp_path)
    agent.send("research")
    assert client.calls[1][-1]["role"] == "assistant"
