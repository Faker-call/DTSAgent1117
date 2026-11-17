"""把代码查询包装成工具调用：模型/代码提案 → 策略裁决 → 执行器 → 审计。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pmagent.authz import PolicyEngine
from pmagent.contracts import ToolProposal, User
from pmagent.knowledge.index import CodeIndex
from pmagent.safety.executor import Executor, ToolSpec
from pmagent.safety.guardrails import Guardrails


class ToolBroker:
    """诊断阶段的只读工具入口；不经执行器的查询在代码层不可达。"""

    def __init__(
        self,
        code_index: CodeIndex,
        *,
        user: User,
        repo_root: str | Path = ".",
        audit: Callable[..., None] | None = None,
        stage: str = "slice",
        agent_role: str = "diagnosis",
    ) -> None:
        self.code_index = code_index
        self.stage = stage
        self.agent_role = agent_role
        self.executor = Executor(
            policy=PolicyEngine(),
            guardrails=Guardrails(repo_root),
            tools={
                "code.get_slice": ToolSpec(fn=self._slice),
                "code.get_callers": ToolSpec(fn=self._callers),
                "code.get_callees": ToolSpec(fn=self._callees),
            },
            user=user,
            audit=audit,
            limits={"code.get_slice": 20, "code.get_callers": 20, "code.get_callees": 20},
        )

    def slice(self, symbol: str) -> str:
        return self._call("code.get_slice", {"symbol": symbol})

    def callers(self, symbol: str) -> list[str]:
        return self._call("code.get_callers", {"symbol": symbol})

    def callees(self, symbol: str) -> list[str]:
        return self._call("code.get_callees", {"symbol": symbol})

    # -- 工具实现 ---------------------------------------------------------
    def _call(self, tool: str, args: dict) -> object:
        proposal = ToolProposal(tool=tool, args=args, stage=self.stage)
        return self.executor.execute(
            proposal, stage=self.stage, agent_role=self.agent_role
        )

    def _slice(self, symbol: str) -> str:
        row = self.code_index.conn.execute(
            "SELECT text FROM chunks WHERE symbol_qualified = ? LIMIT 1", (symbol,)
        ).fetchone()
        return row["text"] if row is not None else ""

    def _callers(self, symbol: str) -> list[str]:
        leaf = symbol.split("::")[-1]
        return [
            row["from_symbol"]
            for row in self.code_index.conn.execute(
                "SELECT DISTINCT from_symbol FROM refs WHERE to_name = ? LIMIT 10", (leaf,)
            ).fetchall()
        ]

    def _callees(self, symbol: str) -> list[str]:
        return [
            row["to_name"]
            for row in self.code_index.conn.execute(
                "SELECT DISTINCT to_name FROM refs WHERE from_symbol = ? LIMIT 10", (symbol,)
            ).fetchall()
        ]


__all__ = ["ToolBroker"]
