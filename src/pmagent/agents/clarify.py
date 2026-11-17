"""澄清策略：按缺失项提出有限问题，且不重复询问已回答内容。"""

from __future__ import annotations

from pmagent.contracts import EvidenceRequest, SessionState

MAX_QUESTIONS = 3

QUESTION_BY_FIELD = {
    "SDK 版本": "使用的 SDK 版本是多少？",
    "平台与渲染后端": "运行在哪个平台、使用哪个渲染后端（OpenGL / Vulkan）？",
    "日志 / 堆栈 / 崩溃转储 / 复现工程 / 截图（任一项）": (
        "能提供日志、堆栈、崩溃转储或复现步骤中的任一项吗？"
    ),
    "带文件行号的堆栈或明确的代码位置": "有带文件行号的堆栈，或具体到文件与函数的代码位置吗？",
}


class ClarificationPolicy:
    def questions(self, state: SessionState, missing: list[EvidenceRequest]) -> list[str]:
        questions: list[str] = []
        for item in missing:
            if len(questions) >= MAX_QUESTIONS:
                break
            if item.needed == "SDK 版本" and state.slots.sdk_version:
                continue
            if item.needed == "平台与渲染后端" and state.slots.platform:
                continue
            question = QUESTION_BY_FIELD.get(item.needed, f"能补充{item.needed}吗？")
            if question not in questions:
                questions.append(question)
        return questions

    def apply_answer(self, state: SessionState, field: str, value: str) -> None:
        if field == "SDK 版本":
            state.slots.sdk_version = value
        elif field == "平台与渲染后端":
            state.slots.platform = value
        else:
            state.confirmed_facts.append(f"{field}={value}")


__all__ = ["ClarificationPolicy", "MAX_QUESTIONS", "QUESTION_BY_FIELD"]
