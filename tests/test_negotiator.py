import json
from types import SimpleNamespace as NS

import pytest

from negotiator import cli, tools
from negotiator.setup_agent import RESEARCH_HOSTS, build_tools


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


def test_agent_tools_only_enable_web():
    t = build_tools(RESEARCH_HOSTS)
    toolset = t[0]
    assert toolset["default_config"]["enabled"] is False
    assert {c["name"] for c in toolset["configs"] if c["enabled"]} == {"web_search", "web_fetch"}
    assert len(RESEARCH_HOSTS) <= 64


# ---------------- event loop ----------------
class FakeStream:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return iter(self.events)

    def __exit__(self, *a):
        return False


class FakeClient:
    def __init__(self, events):
        self.sent = []
        events_api = NS(
            stream=lambda session_id: FakeStream(events),
            send=lambda session_id, events: self.sent.extend(events),
        )
        self.beta = NS(sessions=NS(events=events_api))


def idle(reason):
    return NS(type="session.status_idle", stop_reason=NS(type=reason))


def test_run_turn_executes_custom_tool_and_stops_on_end_turn(capsys):
    events = [
        NS(type="agent.message", content=[NS(type="text", text="Let me compute.")]),
        NS(type="agent.custom_tool_use", id="sevt_1", name="salary_counter",
           input={"current_offer": 100_000}),
        idle("requires_action"),
        NS(type="agent.message", content=[NS(type="text", text="Counter at 109,750.")]),
        idle("end_turn"),
        NS(type="agent.message", content=[NS(type="text", text="SHOULD NOT PRINT")]),
    ]
    client = FakeClient(events)
    assert cli.run_turn(client, "sesn_x", "hi") == "end_turn"
    assert client.sent[0]["type"] == "user.message"
    result = client.sent[1]
    assert result["type"] == "user.custom_tool_result"
    assert result["custom_tool_use_id"] == "sevt_1"
    assert result["is_error"] is False
    assert "recommended_counter" in result["content"][0]["text"]
    out = capsys.readouterr().out
    assert "Counter at 109,750." in out and "SHOULD NOT PRINT" not in out


def test_run_turn_denies_flagged_call_without_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(EOFError))
    events = [
        NS(type="agent.tool_use", id="sevt_2", name="web_fetch",
           input={"url": "https://x"}, evaluated_permission="ask"),
        idle("requires_action"),
        idle("budget_reached"),
    ]
    client = FakeClient(events)
    assert cli.run_turn(client, "s", "go") == "budget_reached"
    assert client.sent[1] == {"type": "user.tool_confirmation", "tool_use_id": "sevt_2", "result": "deny"}
