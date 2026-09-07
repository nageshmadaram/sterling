"""Reproduce compounding arithmetic; no prices, market simulation, or trades."""

import json
import math


def scenario(n: int, risk: float, reward_r: float = 2.0) -> dict:
    win_multiplier = 1 + reward_r * risk
    loss_multiplier = 1 - risk
    wins = math.ceil(
        (math.log(20) - n * math.log(loss_multiplier))
        / (math.log(win_multiplier) - math.log(loss_multiplier))
    )
    return {
        "trades": n,
        "equity_risk_fraction": risk,
        "net_win_r": reward_r,
        "net_loss_r": -1,
        "all_wins_ending_inr": 100000 * win_multiplier**n,
        "target_possible_under_assumptions": wins <= n,
        "minimum_wins": wins if wins <= n else None,
        "required_win_fraction": wins / n if wins <= n else None,
        "ending_at_minimum_wins_inr": (
            100000 * win_multiplier**wins * loss_multiplier ** (n - wins)
            if wins <= n else None
        ),
    }


def convexity_scenario(multiple: float, n: int = 60, allocation: float = 0.01) -> dict:
    win_multiplier = 1 + allocation * (multiple - 1)
    loss_multiplier = 1 - allocation
    minimum_wins = math.ceil(
        (math.log(20) - n * math.log(loss_multiplier))
        / (math.log(win_multiplier) - math.log(loss_multiplier))
    )
    return {
        "gross_option_multiple_on_winner": multiple,
        "premium_allocation_fraction": allocation,
        "attempts": n,
        "win_account_multiplier": win_multiplier,
        "loss_account_multiplier": loss_multiplier,
        "minimum_wins_for_20x": minimum_wins,
        "ending_at_minimum_wins_inr": (
            100000 * win_multiplier**minimum_wins
            * loss_multiplier**(n-minimum_wins)
        ),
        "same_wins_with_0_15_percent_sale_stt_only_inr": (
            100000 * (1 - allocation + allocation * multiple * (1 - 0.0015))**minimum_wins
            * loss_multiplier**(n-minimum_wins)
        ),
        "arithmetic_breakeven_win_probability": 1 / multiple,
        "log_growth_breakeven_win_probability": (
            -math.log(loss_multiplier)
            / (math.log(win_multiplier) - math.log(loss_multiplier))
        ),
        "assumptions": "Full premium lost on every loser; exact stated multiple realized on every winner; costs ignored except in explicitly named sale-STT-only sensitivity; lot constraints, spreads, depth, latency and loss breakers ignored.",
    }


def double_premium_scenario(n: int = 60, account_target_multiple: float = 2.0) -> dict:
    allocation = 0.05
    premium_stop = 0.20
    win_multiplier = 1 + allocation
    loss_multiplier = 1 - allocation * premium_stop
    wins = math.ceil(
        (math.log(account_target_multiple) - n * math.log(loss_multiplier))
        / (math.log(win_multiplier) - math.log(loss_multiplier))
    )
    possible = wins <= n
    return {
        "classification": "GROSS_BINARY_ARITHMETIC_NOT_MARKET_RESULTS",
        "premium_target_multiple": 2,
        "premium_stop_loss_fraction": premium_stop,
        "account_premium_allocation_fraction": allocation,
        "planned_account_stop_loss_fraction_before_costs": allocation * premium_stop,
        "trades": n,
        "account_target_multiple": account_target_multiple,
        "win_account_multiplier": win_multiplier,
        "loss_account_multiplier": loss_multiplier,
        "target_possible_within_trade_count": possible,
        "minimum_wins": wins if possible else None,
        "all_wins_ending_inr": 100000 * win_multiplier**n,
        "ending_at_minimum_wins_inr": (
            100000 * win_multiplier**wins * loss_multiplier**(n-wins)
            if possible else None
        ),
        "arithmetic_breakeven_win_probability": premium_stop / (1 + premium_stop),
        "log_growth_breakeven_win_probability": (
            -math.log(loss_multiplier)
            / (math.log(win_multiplier) - math.log(loss_multiplier))
        ),
        "illustrative_60_trade_endings_inr": {
            str(w): 100000 * win_multiplier**w * loss_multiplier**(60-w)
            for w in (20, 22, 23, 24, 30)
        },
        "assumptions": [
            "Every winner exits at exactly 2x premium; every loser exits at exactly 0.8x.",
            "No time exits, signal exits, fees, spreads, slippage, gaps or partial fills.",
            "Fractional position sizing with immediate reinvestment; no lot/depth constraints.",
            "No daily or campaign loss breaker; feasible outcome sequences are not asserted.",
            "Neither a win rate nor the stated number of trades has been observed.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps({
        "classification": "HYPOTHETICAL_COMPOUNDING_ARITHMETIC_NOT_MARKET_RESULTS",
        "initial_inr": 100000,
        "target_inr": 2000000,
        "profit_percent_required": 1900,
        "assumptions": [
            "Every trade returns exactly the specified net R, including costs.",
            "Fractional equity sizing with no lot, liquidity, or cash constraints.",
            "Immediate reinvestment and no losses beyond the specified stop.",
            "No win probability or achievable trading frequency is asserted.",
        ],
        "daily_net_growth": {
            str(n): math.expm1(math.log(20) / n) for n in (20, 21, 22)
        },
        "per_trade_net_growth": {
            str(n): math.expm1(math.log(20) / n) for n in (60, 100, 200)
        },
        "scenarios": [scenario(n, 0.01) for n in (60, 100, 200)]
            + [scenario(60, 0.05)],
        "ten_consecutive_loss_drawdown": {
            str(risk): 1 - (1 - risk)**10 for risk in (0.01, 0.05, 0.10)
        },
        "rare_large_payoff_scenarios": [
            convexity_scenario(multiple) for multiple in (100, 200, 500)
        ],
        "two_x_option_premium_scenarios": [
            double_premium_scenario(account_target_multiple=target)
            for target in (2, 20)
        ],
        "measured_strategy_performance": None,
    }, indent=2))
