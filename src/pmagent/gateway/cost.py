"""成本核算：按模型单价累计 token 费用，超预算即终止。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pmagent.errors import BudgetExceeded


@dataclass(frozen=True)
class Price:
    """单价：元 / 百万 token。"""

    input_per_m: float
    output_per_m: float
    cached_per_m: float


# 按 DeepSeek 官方定价（高峰时段），空闲时段约为一半
PRICING: dict[str, Price] = {
    "deepseek-flash": Price(input_per_m=2.0, output_per_m=8.0, cached_per_m=0.04),
    "deepseek-v4-pro": Price(input_per_m=9.0, output_per_m=27.0, cached_per_m=0.30),
}
DEFAULT_PRICE = Price(input_per_m=2.0, output_per_m=8.0, cached_per_m=0.04)


def price_for(model: str) -> Price:
    return PRICING.get(model, DEFAULT_PRICE)


def cost_of(
    model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0
) -> float:
    price = price_for(model)
    billable_input = max(0, prompt_tokens - cached_tokens)
    total = (
        billable_input * price.input_per_m
        + cached_tokens * price.cached_per_m
        + completion_tokens * price.output_per_m
    )
    return total / 1_000_000


@dataclass
class CostMeter:
    max_cost_cny: float = 5.0
    max_tokens: int = 400_000
    trace: Any | None = None
    total_cny: float = 0.0
    total_tokens: int = 0
    by_role: dict[str, float] = field(default_factory=dict)

    def record(
        self,
        *,
        role: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> float:
        amount = cost_of(model, prompt_tokens, completion_tokens, cached_tokens)
        self.total_cny += amount
        self.total_tokens += prompt_tokens + completion_tokens
        self.by_role[role] = round(self.by_role.get(role, 0.0) + amount, 6)
        if self.trace is not None and hasattr(self.trace, "record"):
            self.trace.record(
                "model_called",
                role=role,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                cost_cny=round(amount, 6),
                total_cny=round(self.total_cny, 6),
            )
        self.check()
        return amount

    def check(self) -> None:
        if self.total_cny > self.max_cost_cny:
            raise BudgetExceeded(
                f"会话费用超限（{self.total_cny:.4f} > {self.max_cost_cny:.2f} 元）",
                hint="提高 budget.max_cost_cny 或减少检索轮次",
            )
        if self.total_tokens > self.max_tokens:
            raise BudgetExceeded(
                f"会话 token 超限（{self.total_tokens} > {self.max_tokens}）",
                hint="提高 budget.max_tokens",
            )

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_cny": round(self.total_cny, 6),
            "total_tokens": self.total_tokens,
            "by_role": dict(self.by_role),
        }
