"""Deterministic negotiation tools.

These run on the user's machine (they are Managed Agents *custom* tools): the
agent decides when to call them, the CLI executes them here and sends the
result back. Keeping the math local and deterministic means numbers in a
negotiation plan are computed, not guessed by the model.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

BRIEFS_DIR = Path("briefs")


# --------------------------------------------------------------------------
# 1. Bargaining range (ZOPA) and anchor
# --------------------------------------------------------------------------
def analyze_bargaining_range(
    role: str,
    target: float,
    walk_away: float,
    counterpart_estimated_limit: float | None = None,
    counterpart_opening: float | None = None,
    anchor_aggressiveness: float = 0.5,
) -> dict[str, Any]:
    """Compute the zone of possible agreement and a recommended opening anchor.

    role: "buyer" (wants a LOW number: buying, hiring someone, paying) or
          "seller" (wants a HIGH number: selling, salary, equity, rate).
    """
    role = role.lower()
    if role not in ("buyer", "seller"):
        raise ValueError("role must be 'buyer' or 'seller'")
    if not 0 <= anchor_aggressiveness <= 1:
        raise ValueError("anchor_aggressiveness must be between 0 and 1")
    seller = role == "seller"
    if seller and target < walk_away:
        raise ValueError("as a seller your target should be >= your walk-away")
    if not seller and target > walk_away:
        raise ValueError("as a buyer your target should be <= your walk-away")

    notes: list[str] = []
    zopa: dict[str, float] | None = None
    if counterpart_estimated_limit is not None:
        low, high = (
            (walk_away, counterpart_estimated_limit)
            if seller
            else (counterpart_estimated_limit, walk_away)
        )
        if low <= high:
            zopa = {"low": round(low, 2), "high": round(high, 2), "width": round(high - low, 2)}
        else:
            notes.append(
                "No ZOPA on price alone: your walk-away and their estimated limit do not "
                "overlap. Expand the pie (non-price terms) or strengthen your BATNA."
            )

    # Anchor: ambitious but defensible. 5%-25% beyond target, scaled by aggressiveness,
    # never past the counterpart's estimated limit by more than 10%.
    stretch = 0.05 + 0.20 * anchor_aggressiveness
    anchor = target * (1 + stretch) if seller else target * (1 - stretch)
    if counterpart_estimated_limit is not None:
        cap = counterpart_estimated_limit * (1.10 if seller else 0.90)
        anchor = min(anchor, cap) if seller else max(anchor, cap)
        # never anchor on the wrong side of your own target
        anchor = max(anchor, target) if seller else min(anchor, target)

    if counterpart_opening is not None:
        gap = abs(counterpart_opening - target)
        notes.append(
            f"They opened at {counterpart_opening:,.2f}; gap to your target is {gap:,.2f}. "
            "Do not split the difference from their anchor - re-anchor with your own number and rationale."
        )
        if (seller and counterpart_opening >= target) or (not seller and counterpart_opening <= target):
            notes.append("Their opening already meets your target: probe for more value before accepting.")

    if zopa:
        if seller and target > zopa["high"]:
            notes.append("Your target is above their estimated limit; expect to trade non-price terms.")
        if not seller and target < zopa["low"]:
            notes.append("Your target is below their estimated limit; expect to trade non-price terms.")

    return {
        "role": role,
        "target": target,
        "walk_away": walk_away,
        "zopa": zopa,
        "recommended_anchor": round(anchor, 2),
        "anchor_tip": "Use a precise number (e.g. 127,500 not 125,000) and lead with the rationale.",
        "notes": notes,
    }


# --------------------------------------------------------------------------
# 2. Concession schedule
# --------------------------------------------------------------------------
def plan_concessions(opening: float, target: float, walk_away: float, rounds: int = 4) -> dict[str, Any]:
    """Shrinking concession steps from opening toward target, never crossing walk-away.

    Shrinking steps (roughly halving) signal you're approaching your limit.
    """
    if rounds < 1 or rounds > 10:
        raise ValueError("rounds must be between 1 and 10")
    seller = opening >= walk_away
    if seller and not (opening >= target >= walk_away):
        raise ValueError("for a seller expect opening >= target >= walk_away")
    if not seller and not (opening <= target <= walk_away):
        raise ValueError("for a buyer expect opening <= target <= walk_away")

    total = target - opening  # signed distance to cover
    weights = [0.5**i for i in range(rounds)]
    wsum = sum(weights)
    schedule = []
    current = opening
    for i, w in enumerate(weights, start=1):
        step = total * w / wsum
        current += step
        schedule.append(
            {
                "round": i,
                "concede": round(abs(step), 2),
                "new_position": round(current, 2),
                "ask_in_return": "Trade, don't give: tie each move to something you get back.",
            }
        )
    return {
        "direction": "seller (moving down)" if seller else "buyer (moving up)",
        "schedule": schedule,
        "final_position": round(target, 2),
        "hard_stop": walk_away,
        "tip": "Each concession smaller than the last; pause before conceding; label it as a concession.",
    }


# --------------------------------------------------------------------------
# 3. Startup equity value
# --------------------------------------------------------------------------
def value_startup_equity(
    ownership_percent: float | None = None,
    shares: float | None = None,
    fully_diluted_shares: float | None = None,
    strike_price: float = 0.0,
    current_valuation: float | None = None,
    exit_valuation: float | None = None,
    future_dilution_percent: float = 30.0,
    vesting_years: float = 4.0,
    success_probability: float = 0.1,
) -> dict[str, Any]:
    """Rough expected-value model for a startup equity grant (not financial advice)."""
    if ownership_percent is None:
        if shares is None or not fully_diluted_shares:
            raise ValueError("give ownership_percent, or shares and fully_diluted_shares")
        ownership_percent = shares / fully_diluted_shares * 100
    if not 0 < ownership_percent <= 100:
        raise ValueError("ownership_percent must be in (0, 100]")
    if not 0 <= success_probability <= 1:
        raise ValueError("success_probability must be between 0 and 1")
    if not 0 <= future_dilution_percent < 100:
        raise ValueError("future_dilution_percent must be in [0, 100)")
    if vesting_years <= 0:
        raise ValueError("vesting_years must be > 0")

    own = ownership_percent / 100
    diluted_own = own * (1 - future_dilution_percent / 100)
    out: dict[str, Any] = {
        "ownership_percent_now": round(ownership_percent, 4),
        "ownership_percent_after_dilution": round(diluted_own * 100, 4),
    }
    exercise_cost = (shares or 0) * strike_price
    if current_valuation:
        out["paper_value_today"] = round(own * current_valuation - exercise_cost, 2)
        out["paper_value_per_year"] = round(out["paper_value_today"] / vesting_years, 2)
    if exit_valuation:
        gross = diluted_own * exit_valuation - exercise_cost
        out["value_at_exit_if_success"] = round(gross, 2)
        out["expected_value"] = round(gross * success_probability, 2)
        out["expected_value_per_year"] = round(gross * success_probability / vesting_years, 2)
    out["caveats"] = [
        "Ask for: fully diluted share count, latest 409A / strike, preference stack, "
        "post-termination exercise window, acceleration on change of control.",
        "Expected value is highly sensitive to success_probability; most startups return 0 to common.",
    ]
    return out


# --------------------------------------------------------------------------
# 4. Compare offers / packages
# --------------------------------------------------------------------------
def compare_offers(offers: list[dict[str, Any]], years: int = 4) -> dict[str, Any]:
    """Compare compensation packages (or deal packages) on annualized and multi-year value.

    Each offer: {name, base, bonus?, signing_bonus?, equity_value_per_year?, benefits_value?,
                 other_annual?, one_time_costs?}
    """
    if not offers:
        raise ValueError("provide at least one offer")
    if years < 1:
        raise ValueError("years must be >= 1")
    rows = []
    for o in offers:
        if "name" not in o:
            raise ValueError("each offer needs a 'name'")
        annual = (
            float(o.get("base", 0))
            + float(o.get("bonus", 0))
            + float(o.get("equity_value_per_year", 0))
            + float(o.get("benefits_value", 0))
            + float(o.get("other_annual", 0))
        )
        one_time = float(o.get("signing_bonus", 0)) - float(o.get("one_time_costs", 0))
        rows.append(
            {
                "name": o["name"],
                "annual_value": round(annual, 2),
                "first_year_value": round(annual + one_time, 2),
                f"total_{years}y": round(annual * years + one_time, 2),
            }
        )
    rows.sort(key=lambda r: r[f"total_{years}y"], reverse=True)
    best = rows[0]
    for r in rows:
        r[f"gap_to_best_{years}y"] = round(best[f"total_{years}y"] - r[f"total_{years}y"], 2)
    return {"ranked": rows, "best": best["name"], "horizon_years": years}


# --------------------------------------------------------------------------
# 5. Raise / counter-offer math
# --------------------------------------------------------------------------
def salary_counter(
    current_offer: float,
    market_p50: float | None = None,
    market_p75: float | None = None,
    market_p90: float | None = None,
    competing_offer: float | None = None,
) -> dict[str, Any]:
    """Suggest a salary counter given market percentiles and leverage."""
    if current_offer <= 0:
        raise ValueError("current_offer must be > 0")
    candidates = [current_offer * 1.10]  # baseline ask: +10%
    rationale = ["Baseline: ~10% above the offer is a common, defensible counter."]
    if market_p75:
        candidates.append(market_p75)
        rationale.append("Market p75 supports asking at the upper quartile.")
    if market_p90 and competing_offer:
        candidates.append(market_p90)
        rationale.append("A competing offer justifies reaching toward p90.")
    if competing_offer and competing_offer > current_offer:
        candidates.append(competing_offer * 1.03)
        rationale.append("Slightly above the competing offer, so matching it isn't enough.")
    counter = max(candidates)
    # sanity cap: >35% above offer rarely lands without a competing offer
    cap = current_offer * (1.5 if competing_offer else 1.35)
    if counter > cap:
        counter = cap
        rationale.append("Capped to keep the ask credible.")
    counter = math.ceil(counter / 500) * 500 - 250  # precise-looking number
    pos = None
    if market_p50:
        pos = round((current_offer / market_p50 - 1) * 100, 1)
    return {
        "recommended_counter": counter,
        "increase_percent": round((counter / current_offer - 1) * 100, 1),
        "offer_vs_market_p50_percent": pos,
        "rationale": rationale,
        "if_base_is_capped": [
            "signing bonus",
            "guaranteed first-year bonus",
            "more equity / refresh",
            "6-month compensation review with written criteria",
            "title / level",
            "remote days, PTO, learning budget",
        ],
    }


# --------------------------------------------------------------------------
# 6. Save brief
# --------------------------------------------------------------------------
def save_negotiation_brief(title: str, markdown: str) -> dict[str, Any]:
    """Write the negotiation brief / script to ./briefs/<date>-<slug>.md"""
    if not markdown.strip():
        raise ValueError("markdown is empty")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "brief"
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEFS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.md"
    path.write_text(f"# {title}\n\n{markdown.strip()}\n", encoding="utf-8")
    return {"saved_to": str(path)}


# --------------------------------------------------------------------------
# Registry + tool definitions for the agent
# --------------------------------------------------------------------------
_NUM = {"type": "number"}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "custom",
        "name": "analyze_bargaining_range",
        "description": (
            "Compute the ZOPA (zone of possible agreement) and a recommended opening anchor. "
            "Use once the user's target and walk-away are known. role='seller' when the user "
            "wants a HIGHER number (selling, salary, equity, rate); role='buyer' when they want "
            "a LOWER number (buying, paying)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "enum": ["buyer", "seller"]},
                "target": {**_NUM, "description": "User's realistic goal number"},
                "walk_away": {**_NUM, "description": "User's reservation point (BATNA-based)"},
                "counterpart_estimated_limit": {**_NUM, "description": "Best guess of the other side's limit"},
                "counterpart_opening": {**_NUM, "description": "Their opening number, if any"},
                "anchor_aggressiveness": {**_NUM, "description": "0 (soft) to 1 (aggressive); default 0.5"},
            },
            "required": ["role", "target", "walk_away"],
        },
    },
    {
        "type": "custom",
        "name": "plan_concessions",
        "description": "Build a shrinking concession schedule from the opening position toward the target, never crossing walk-away.",
        "input_schema": {
            "type": "object",
            "properties": {
                "opening": _NUM,
                "target": _NUM,
                "walk_away": _NUM,
                "rounds": {"type": "integer", "description": "1-10, default 4"},
            },
            "required": ["opening", "target", "walk_away"],
        },
    },
    {
        "type": "custom",
        "name": "value_startup_equity",
        "description": "Estimate paper and risk-adjusted expected value of a startup equity grant (with dilution and vesting).",
        "input_schema": {
            "type": "object",
            "properties": {
                "ownership_percent": _NUM,
                "shares": _NUM,
                "fully_diluted_shares": _NUM,
                "strike_price": _NUM,
                "current_valuation": _NUM,
                "exit_valuation": _NUM,
                "future_dilution_percent": {**_NUM, "description": "Default 30"},
                "vesting_years": {**_NUM, "description": "Default 4"},
                "success_probability": {**_NUM, "description": "0-1, default 0.1"},
            },
        },
    },
    {
        "type": "custom",
        "name": "compare_offers",
        "description": "Rank compensation or deal packages by annual, first-year and multi-year value.",
        "input_schema": {
            "type": "object",
            "properties": {
                "offers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "base": _NUM,
                            "bonus": _NUM,
                            "signing_bonus": _NUM,
                            "equity_value_per_year": _NUM,
                            "benefits_value": _NUM,
                            "other_annual": _NUM,
                            "one_time_costs": _NUM,
                        },
                        "required": ["name"],
                    },
                },
                "years": {"type": "integer", "description": "Horizon, default 4"},
            },
            "required": ["offers"],
        },
    },
    {
        "type": "custom",
        "name": "salary_counter",
        "description": "Suggest a salary counter-offer from the current offer, market percentiles and any competing offer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "current_offer": _NUM,
                "market_p50": _NUM,
                "market_p75": _NUM,
                "market_p90": _NUM,
                "competing_offer": _NUM,
            },
            "required": ["current_offer"],
        },
    },
    {
        "type": "custom",
        "name": "save_negotiation_brief",
        "description": "Save the final negotiation brief/script as a Markdown file on the user's machine. Call when the plan is ready.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "markdown": {"type": "string"}},
            "required": ["title", "markdown"],
        },
    },
]

TOOL_FUNCTIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "analyze_bargaining_range": analyze_bargaining_range,
    "plan_concessions": plan_concessions,
    "value_startup_equity": value_startup_equity,
    "compare_offers": compare_offers,
    "salary_counter": salary_counter,
    "save_negotiation_brief": save_negotiation_brief,
}


def run_tool(name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
    """Execute a custom tool. Returns (json_text, is_error)."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool {name}"}), True
    if not isinstance(tool_input, dict):
        return json.dumps({"error": "tool input must be an object"}), True
    try:
        return json.dumps(fn(**tool_input)), False
    except (TypeError, ValueError) as e:
        return json.dumps({"error": str(e)}), True
