"""
Project Syndicate — Role Definitions

Defines each agent role with its action space, default temperature,
cycle interval, description, and output schema.
"""

__version__ = "1.0.0"

from dataclasses import dataclass, field


# ──────────────────────────────────────────────
# Universal Survival Actions (Phase 8B)
# ──────────────────────────────────────────────

SURVIVAL_ACTIONS = {
    "propose_sip": {
        "description": "Propose a System Improvement Proposal. SIPs can change evaluation criteria, cycle frequencies, budget allocation, or any system parameter. Posted to the Agora for debate. Costs 2x thinking tax. Max 1 per evaluation period.",
        "params": {
            "title": "str — concise proposal title",
            "category": "str — evaluation | economics | pipeline | lifecycle | other",
            "proposal": "str — detailed description of the proposed change",
            "rationale": "str — why this benefits the ecosystem (not just you)",
            "metrics_affected": "list[str] — which evaluation metrics this would change",
        },
    },
    "offer_intel": {
        "description": "Share intelligence with the ecosystem. Increases reputation if useful, decreases if wrong. Your intel quality is tracked.",
        "params": {
            "content": "str — the intelligence",
            "market": "str — relevant market or 'general'",
            "confidence": "int — 1-10",
            "target_role": "str — scout/strategist/critic/operator/all",
        },
    },
    "request_alliance": {
        "description": "Propose a working relationship with another agent. Alliances boost trust and intel relevance. Public — all agents see who's allied.",
        "params": {
            "target_agent": "str — agent name",
            "offer": "str — what you bring",
            "request": "str — what you want in return",
            "duration": "str — e.g., 'until next evaluation'",
        },
    },
    "accept_alliance": {
        "description": "Accept a pending alliance proposal.",
        "params": {
            "alliance_id": "int — the alliance proposal to accept",
        },
    },
    "dissolve_alliance": {
        "description": "End an active alliance.",
        "params": {
            "alliance_id": "int — the alliance to dissolve",
            "reason": "str — why you're ending this",
        },
    },
    "execute_analysis": {
        "description": "Write and execute a Python script to analyze data. Available: get_price_history(symbol,timeframe,limit), get_current_price(symbol), get_my_trades(limit), get_my_positions(), get_agora_messages(channel,limit), get_market_regime(). Call output(data) to return results. Max 5000 chars.",
        "params": {
            "script": "str — Python code",
            "purpose": "str — what you're analyzing",
            "save_as_tool": "bool — save as reusable named tool",
            "tool_name": "str|null — name (required if save_as_tool=true)",
        },
    },
    "run_tool": {
        "description": "Execute a previously saved analysis tool. Max 3 per cycle.",
        "params": {
            "tool_name": "str — name of saved tool",
        },
    },
    "modify_genome": {
        "description": "Update a parameter in your strategy genome. Max 2 per evaluation period. Only modify with concrete evidence.",
        "params": {
            "parameter_path": "str — dot notation, e.g. 'risk_management.stop_loss_pct'",
            "new_value": "any — the new value",
            "evidence": "str — what experience justifies this change",
            "confidence": "int — 1-10",
        },
    },
    "strategic_hibernate": {
        "description": "Voluntarily pause all activity. Survival clock FREEZES. Budget stops draining. Wake on regime change, set duration, or manual trigger.",
        "params": {
            "reason": "str — why hibernation is strategic",
            "wake_condition": "str — regime_change | duration | manual",
            "duration_hours": "int|null — hours (if duration-based)",
        },
    },
}


# ──────────────────────────────────────────────
# Action Definitions
# ──────────────────────────────────────────────

SCOUT_ACTIONS = {
    "broadcast_opportunity": {
        "description": "Share a discovered opportunity with the ecosystem",
        "params": {
            "market": "str — trading pair (e.g., SOL/USDT)",
            "signal": "str — type of signal (volume_breakout, trend_reversal, support_bounce, etc.)",
            "urgency": "str — low/medium/high",
            "details": "str — what you see and why it matters",
        },
    },
    "request_deeper_analysis": {
        "description": "Ask the ecosystem for more information on something you've spotted",
        "params": {
            "topic": "str — what you need analyzed",
            "target_role": "str — who should respond (strategist/scout/any)",
            "context": "str — what you already know",
        },
    },
    "update_watchlist": {
        "description": "Change which markets you're actively monitoring",
        "params": {
            "add_markets": "list[str] — markets to start watching",
            "remove_markets": "list[str] — markets to stop watching",
            "reason": "str — why this change",
        },
    },
    "poison_intel": {
        "description": "Challenge circulating intelligence as unreliable. If right, your reputation increases and the source's drops. If wrong, YOUR reputation takes the hit.",
        "params": {
            "target_message_id": "int — Agora message being challenged",
            "challenge_reason": "str — why this intel is wrong",
            "counter_evidence": "str — what the data actually shows",
        },
    },
    "go_idle": {
        "description": "Nothing worth doing right now. Save your budget.",
        "params": {
            "reason": "str — why idle is the right call",
        },
    },
}

STRATEGIST_ACTIONS = {
    "propose_plan": {
        "description": "Submit a trading plan for Critic review",
        "params": {
            "plan_name": "str — descriptive name",
            "market": "str — trading pair",
            "direction": "str — long/short",
            "entry_conditions": "str — when to enter",
            "exit_conditions": "str — take profit and stop loss",
            "position_size_pct": "float — % of allocated capital",
            "timeframe": "str — expected duration",
            "thesis": "str — the core reasoning behind this plan",
            "source_opportunity_id": "int|null — the Scout opportunity that inspired this",
        },
    },
    "revise_plan": {
        "description": "Update an existing plan based on Critic feedback or new data",
        "params": {
            "plan_id": "int — which plan to revise",
            "revisions": "str — what changed and why",
            "updated_fields": "dict — the specific parameter changes",
        },
    },
    "request_scout_intel": {
        "description": "Ask Scouts for specific market intelligence",
        "params": {
            "market": "str — which market",
            "question": "str — what you need to know",
            "urgency": "str — low/medium/high",
        },
    },
    "go_idle": {
        "description": "No actionable plan right now. Save your budget.",
        "params": {
            "reason": "str — why idle is the right call",
        },
    },
}

CRITIC_ACTIONS = {
    "approve_plan": {
        "description": "This plan passes review. Green light for execution.",
        "params": {
            "plan_id": "int — which plan",
            "assessment": "str — what makes this plan sound",
            "risk_notes": "str — risks the Operator should be aware of",
            "confidence": "int — 1-10, how confident in approval",
        },
    },
    "reject_plan": {
        "description": "This plan has fatal flaws. Do not execute.",
        "params": {
            "plan_id": "int — which plan",
            "reasons": "str — specific reasons for rejection",
            "fatal_flaws": "list[str] — the dealbreakers",
        },
    },
    "request_revision": {
        "description": "Plan has potential but needs changes before approval.",
        "params": {
            "plan_id": "int — which plan",
            "issues": "str — what needs to change",
            "suggestions": "str — how to fix it",
        },
    },
    "flag_risk": {
        "description": "Raise a risk concern visible to the entire ecosystem.",
        "params": {
            "risk_type": "str — market/position/systemic/agent",
            "description": "str — what the risk is",
            "severity": "str — low/medium/high/critical",
            "affected_agents": "list[str] — who is exposed",
        },
    },
    "challenge_evaluation_criteria": {
        "description": "A specialized SIP arguing that evaluation criteria are flawed or biased. Costs 2x thinking tax. Same rate limit as regular SIPs.",
        "params": {
            "target_metric": "str — which metric is problematic",
            "argument": "str — why it's unfair or miscalibrated",
            "proposed_change": "str — how it should be adjusted",
            "evidence": "str — data supporting your argument",
        },
    },
    "go_idle": {
        "description": "No plans to review. Nothing to flag.",
        "params": {
            "reason": "str — why idle",
        },
    },
}

OPERATOR_ACTIONS = {
    "execute_trade": {
        "description": "Enter a position based on an approved plan.",
        "params": {
            "plan_id": "int — which approved plan",
            "market": "str — trading pair",
            "direction": "str — long/short",
            "order_type": "str — market/limit",
            "limit_price": "float|null — for limit orders",
            "position_size_usd": "float — dollar amount",
            "stop_loss": "float — stop loss price",
            "take_profit": "float — take profit price",
        },
    },
    "adjust_position": {
        "description": "Modify an existing position's parameters.",
        "params": {
            "position_id": "int — which position",
            "new_stop_loss": "float|null",
            "new_take_profit": "float|null",
            "add_size_usd": "float|null — increase position (goes through Warden)",
            "reduce_size_pct": "float|null — reduce position by this %",
        },
    },
    "close_position": {
        "description": "Exit a position entirely.",
        "params": {
            "position_id": "int — which position",
            "order_type": "str — market/limit",
            "limit_price": "float|null",
            "reason": "str — why closing",
        },
    },
    "hedge": {
        "description": "Open a hedge against an existing position.",
        "params": {
            "position_id": "int — position being hedged",
            "hedge_market": "str — what to trade as hedge",
            "hedge_direction": "str — long/short",
            "hedge_size_usd": "float",
            "thesis": "str — why this hedge helps",
        },
    },
    "refuse_plan": {
        "description": "Decline to execute an approved plan. Costs reputation but preserves capital. Plan returns to pool.",
        "params": {
            "plan_id": "int — which plan",
            "reason": "str — why you won't execute",
            "risk_assessment": "str — what you think will go wrong",
        },
    },
    "go_idle": {
        "description": "No trades to make or manage right now.",
        "params": {
            "reason": "str — why idle",
        },
    },
}


# ──────────────────────────────────────────────
# Role Definitions
# ──────────────────────────────────────────────

@dataclass
class RoleDefinition:
    """Configuration for a single agent role."""
    name: str
    description: str
    available_actions: dict
    default_temperature: float
    cycle_interval_seconds: int
    active_cycle_interval_seconds: int | None = None  # Operators: shorter interval with positions
    context_token_budget: int = 3000
    avg_cycle_cost: float = 0.005  # starting estimate


ROLE_DEFINITIONS: dict[str, RoleDefinition] = {
    "scout": RoleDefinition(
        name="scout",
        description=(
            "You are a Scout. Your job is to watch markets, detect patterns, and "
            "broadcast opportunities to the ecosystem. You don't trade — you find. "
            "A good Scout spots what others miss. A bad Scout wastes everyone's time "
            "with noise. Your reputation depends on the quality of your signals."
        ),
        available_actions={**SCOUT_ACTIONS, **SURVIVAL_ACTIONS},
        default_temperature=0.7,
        cycle_interval_seconds=300,  # 5 minutes
    ),
    "strategist": RoleDefinition(
        name="strategist",
        description=(
            "You are a Strategist. Your job is to take opportunities from Scouts "
            "and turn them into actionable trading plans. You analyze risk, define "
            "entry/exit conditions, and submit plans for Critic review. A good "
            "Strategist builds plans that survive scrutiny and make money. A bad "
            "Strategist wastes capital on untested ideas."
        ),
        available_actions={**STRATEGIST_ACTIONS, **SURVIVAL_ACTIONS},
        default_temperature=0.5,
        cycle_interval_seconds=900,  # 15 minutes
    ),
    "critic": RoleDefinition(
        name="critic",
        description=(
            "You are a Critic. Your job is to stress-test trading plans submitted "
            "by Strategists. Approve what's sound, reject what's flawed, request "
            "revisions when a plan has potential but needs work. You are the last "
            "line of defense before capital is risked. A good Critic catches fatal "
            "flaws. A bad Critic rubber-stamps everything or blocks everything."
        ),
        available_actions={**CRITIC_ACTIONS, **SURVIVAL_ACTIONS},
        default_temperature=0.2,
        cycle_interval_seconds=0,  # on-demand only
    ),
    "operator": RoleDefinition(
        name="operator",
        description=(
            "You are an Operator. Your job is to execute approved trading plans. "
            "You manage positions, set stop losses, adjust for market conditions, "
            "and close trades when the thesis plays out or invalidates. A good "
            "Operator executes with discipline. A bad Operator overrides the plan "
            "and lets emotions drive decisions."
        ),
        available_actions={**OPERATOR_ACTIONS, **SURVIVAL_ACTIONS},
        default_temperature=0.2,
        cycle_interval_seconds=900,  # 15 minutes when idle
        active_cycle_interval_seconds=60,  # 1 minute during active trades
    ),
}


def get_role(role_name: str) -> RoleDefinition:
    """Get a role definition by name. Falls back to scout if unknown."""
    return ROLE_DEFINITIONS.get(role_name, ROLE_DEFINITIONS["scout"])


def get_action_names(role_name: str) -> set[str]:
    """Get the set of valid action type names for a role."""
    role = get_role(role_name)
    return set(role.available_actions.keys())


def format_actions_for_prompt(role_name: str) -> str:
    """Format the available actions for inclusion in the system prompt."""
    role = get_role(role_name)
    lines = []
    for action_name, action_def in role.available_actions.items():
        lines.append(f"- {action_name}: {action_def['description']}")
        for param_name, param_desc in action_def["params"].items():
            lines.append(f"    {param_name}: {param_desc}")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# Output Schemas (JSON Schema for validation)
# ──────────────────────────────────────────────

NORMAL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["situation", "confidence", "recent_pattern", "action", "reasoning", "self_note"],
    "properties": {
        "situation": {"type": "string"},
        "confidence": {
            "type": "object",
            "required": ["score", "reasoning"],
            "properties": {
                "score": {"type": "integer", "minimum": 1, "maximum": 10},
                "reasoning": {"type": "string"},
            },
        },
        "recent_pattern": {"type": "string"},
        "action": {
            "type": "object",
            "required": ["type", "params"],
            "properties": {
                "type": {"type": "string"},
                "params": {"type": "object"},
            },
        },
        "reasoning": {"type": "string"},
        "self_note": {"type": "string"},
    },
}

REFLECTION_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["what_worked", "what_failed", "pattern_detected", "lesson", "confidence_trend"],
    "properties": {
        "what_worked": {"type": "string"},
        "what_failed": {"type": "string"},
        "pattern_detected": {"type": "string"},
        "lesson": {"type": "string"},
        "confidence_trend": {"type": "string", "enum": ["improving", "stable", "declining"]},
        "confidence_reason": {"type": "string"},
        "strategy_note": {"type": "string"},
        "memory_promotion": {
            "type": "array",
            "items": {"type": "string"},
        },
        "memory_demotion": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}
