"""视觉分析：规则检测（缝隙/纯色块/色带/错位）+ 可选多模态描述与冲突标注。"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image

from pmagent.contracts import VisualEvidence

SOLID_STD = 3.0
SEAM_DIFF = 30
BAND_DIFF = 30
UNIFORM_STD = 8.0
MIN_REGION_AREA = 0.02


@dataclass
class Region:
    x: int
    y: int
    w: int
    h: int
    kind: str
    confidence: float
    source: str = "rule"

    def as_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "kind": self.kind,
            "confidence": round(self.confidence, 2),
            "source": self.source,
        }


@dataclass
class VisualAnalysis:
    evidence: VisualEvidence
    regions: list[Region] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)


class Describer(Protocol):
    def describe(self, path: str | Path) -> dict: ...


class ModelDescriber:
    """多模态描述：把图片编码后交给 deepseek-flash 图像输入。"""

    def __init__(self, gateway, model: str = "deepseek-flash") -> None:
        self.gateway = gateway
        self.model = model

    def describe(self, path: str | Path) -> dict:  # pragma: no cover - 需要在线模型
        from pmagent.gateway import complete_json
        from pmagent.gateway.base import Message

        data = base64.b64encode(Path(path).read_bytes()).decode()
        messages = [
            {
                "role": "system",
                "content": (
                    "你是地图渲染缺陷助手。描述截图中的渲染异常，输出 JSON："
                    "{description, kinds:[seam|solid|band|misalign], regions:[{x,y,w,h,kind}]}"
                ),
            },
            Message(
                role="user",
                content=[
                    {"type": "text", "text": "请分析这张渲染截图"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
                ],
            ),
        ]

        class _Result:
            description: str
            kinds: list[str]
            regions: list[dict]

        from pydantic import BaseModel

        class _Payload(BaseModel):
            description: str = ""
            kinds: list[str] = []
            regions: list[dict] = []

        result = complete_json(self.gateway, messages, _Payload, role="vision")
        return result.value.model_dump()


class VisualAnalyzer:
    def __init__(self, describer: Describer | None = None) -> None:
        self.describer = describer

    def analyze(self, path: str | Path) -> VisualAnalysis:
        path = Path(path)
        image = Image.open(path).convert("RGB")
        regions = self._detect(image)
        features = sorted({region.kind for region in regions})
        conflicts: list[dict] = []

        if self.describer is not None:
            try:
                description = self.describer.describe(path)
            except Exception:  # noqa: BLE001 - 模型不可用时只保留规则结果
                description = {}
            model_kinds = [str(kind) for kind in description.get("kinds", [])]
            model_regions: list[Region] = []
            for item in description.get("regions", []):
                if not {"x", "y", "w", "h", "kind"} <= set(item):
                    continue
                model_regions.append(
                    Region(
                        x=int(item["x"]),
                        y=int(item["y"]),
                        w=int(item["w"]),
                        h=int(item["h"]),
                        kind=str(item["kind"]),
                        confidence=float(item.get("confidence", 0.6)),
                        source="model",
                    )
                )
            for model_region in model_regions:
                for rule_region in regions:
                    if (
                        self._overlaps(rule_region, model_region)
                        and rule_region.kind != model_region.kind
                    ):
                        conflicts.append(
                            {
                                "rule_kind": rule_region.kind,
                                "model_kind": model_region.kind,
                                "region": rule_region.as_dict(),
                            }
                        )
            features = sorted(set(features) | set(model_kinds))
            features += [
                f"conflict:rule={item['rule_kind']},model={item['model_kind']}"
                for item in conflicts
            ]
            regions = regions + model_regions

        evidence = VisualEvidence(
            source_path=str(path),
            kind="image",
            features=features,
            regions=[region.as_dict() for region in regions],
        )
        return VisualAnalysis(evidence=evidence, regions=regions, conflicts=conflicts)

    # -- 规则检测 ---------------------------------------------------------
    def _detect(self, image: Image.Image) -> list[Region]:
        width, height = image.size
        pixels = image.load()
        regions: list[Region] = []
        regions.extend(self._solid_blocks(image, pixels, width, height))
        regions.extend(self._seams(pixels, width, height, axis="v"))
        regions.extend(self._seams(pixels, width, height, axis="h"))
        regions.extend(self._bands(pixels, width, height))
        regions.extend(self._misalignment(regions))
        return regions

    @staticmethod
    def _solid_blocks(image, pixels, width: int, height: int) -> list[Region]:
        cell = max(8, min(width, height) // 8)
        columns = width // cell
        rows = height // cell
        uniform: set[tuple[int, int]] = set()
        for row in range(rows):
            for col in range(columns):
                xs = range(col * cell, (col + 1) * cell)
                ys = range(row * cell, (row + 1) * cell)
                values = [sum(pixels[x, y]) / 3 for y in ys for x in xs]
                mean = sum(values) / len(values)
                variance = sum((value - mean) ** 2 for value in values) / len(values)
                if variance ** 0.5 <= SOLID_STD and mean > 5:
                    uniform.add((col, row))

        total = columns * rows
        regions: list[Region] = []
        visited: set[tuple[int, int]] = set()
        for cell_id in list(uniform):
            if cell_id in visited:
                continue
            component: list[tuple[int, int]] = []
            stack = [cell_id]
            visited.add(cell_id)
            while stack:
                current = stack.pop()
                component.append(current)
                for neighbor in (
                    (current[0] + 1, current[1]),
                    (current[0] - 1, current[1]),
                    (current[0], current[1] + 1),
                    (current[0], current[1] - 1),
                ):
                    if neighbor in uniform and neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)
            cols = [item[0] for item in component]
            row_ids = [item[1] for item in component]
            area = (max(cols) - min(cols) + 1) * (max(row_ids) - min(row_ids) + 1)
            if not (MIN_REGION_AREA * total <= area <= 0.4 * total):
                continue
            regions.append(
                Region(
                    x=min(cols) * cell,
                    y=min(row_ids) * cell,
                    w=(max(cols) - min(cols) + 1) * cell,
                    h=(max(row_ids) - min(row_ids) + 1) * cell,
                    kind="solid",
                    confidence=0.8,
                )
            )
        return regions[:3]

    @staticmethod
    def _column_profile(pixels, width: int, height: int, axis: str):
        if axis == "v":
            return [
                tuple(
                    sum(pixels[x, y][channel] for y in range(height)) / height
                    for channel in range(3)
                )
                for x in range(width)
            ]
        return [
            tuple(
                sum(pixels[x, y][channel] for x in range(width)) / width
                for channel in range(3)
            )
            for y in range(height)
        ]

    def _seams(self, pixels, width: int, height: int, axis: str) -> list[Region]:
        profile = self._column_profile(pixels, width, height, axis)
        stds = self._profile_stds(pixels, width, height, axis)
        regions: list[Region] = []
        for index in range(1, len(profile)):
            if stds[index - 1] > UNIFORM_STD or stds[index] > UNIFORM_STD:
                continue
            diff = sum(abs(a - b) for a, b in zip(profile[index - 1], profile[index], strict=True))
            if diff >= SEAM_DIFF:
                if axis == "v":
                    regions.append(
                        Region(x=index, y=0, w=1, h=height, kind="seam", confidence=0.7)
                    )
                else:
                    regions.append(
                        Region(x=0, y=index, w=width, h=1, kind="seam", confidence=0.7)
                    )
        return regions

    @staticmethod
    def _profile_stds(pixels, width: int, height: int, axis: str) -> list[float]:
        stds: list[float] = []
        if axis == "v":
            for x in range(width):
                values = [sum(pixels[x, y]) / 3 for y in range(height)]
                mean = sum(values) / len(values)
                variance = sum((value - mean) ** 2 for value in values) / len(values)
                stds.append(variance ** 0.5)
        else:
            for y in range(height):
                values = [sum(pixels[x, y]) / 3 for x in range(width)]
                mean = sum(values) / len(values)
                variance = sum((value - mean) ** 2 for value in values) / len(values)
                stds.append(variance ** 0.5)
        return stds

    def _bands(self, pixels, width: int, height: int) -> list[Region]:
        profile = self._column_profile(pixels, width, height, "h")
        stds = self._profile_stds(pixels, width, height, "h")
        boundaries = [
            index
            for index in range(1, len(profile))
            if stds[index - 1] <= UNIFORM_STD
            and stds[index] <= UNIFORM_STD
            and sum(abs(a - b) for a, b in zip(profile[index - 1], profile[index], strict=True))
            >= BAND_DIFF
        ]
        if len(boundaries) < 3:
            return []
        return [
            Region(
                x=0,
                y=boundaries[0],
                w=width,
                h=max(1, boundaries[-1] - boundaries[0]),
                kind="band",
                confidence=0.6,
            )
        ]

    @staticmethod
    def _misalignment(regions: list[Region]) -> list[Region]:
        vertical = [region for region in regions if region.kind == "seam" and region.h > region.w]
        vertical.sort(key=lambda region: region.x)
        results: list[Region] = []
        for left, right in zip(vertical, vertical[1:], strict=False):
            if 0 < right.x - left.x <= 6:
                results.append(
                    Region(
                        x=left.x,
                        y=0,
                        w=max(1, right.x - left.x),
                        h=left.h,
                        kind="misalign",
                        confidence=0.5,
                    )
                )
        return results

    @staticmethod
    def _overlaps(left: Region, right: Region) -> bool:
        return not (
            left.x + left.w <= right.x
            or right.x + right.w <= left.x
            or left.y + left.h <= right.y
            or right.y + right.h <= left.y
        )


__all__ = ["Describer", "ModelDescriber", "Region", "VisualAnalysis", "VisualAnalyzer"]
