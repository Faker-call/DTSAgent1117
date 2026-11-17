"""生成合成工单语料（≥60 条），用于评测种子集与语料检索测试。

用法：python scripts/gen_tickets.py [out_path]
"""

from __future__ import annotations

import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

OUT_PATH = Path("data/tickets/synthetic_tickets.json")

FILES = [
    ("src/mbgl/renderer/painter_fill.cpp", "mbgl::Painter::drawFill"),
    ("src/mbgl/renderer/tile_pyramid.cpp", "mbgl::TilePyramid::update"),
    ("src/mbgl/geometry/line_atlas.cpp", "mbgl::LineAtlas::getDashPattern"),
    ("src/mbgl/style/style_impl.cpp", "mbgl::style::Style::Impl::loadJSON"),
    ("src/mbgl/layout/symbol_layout.cpp", "mbgl::SymbolLayout::prepare"),
    ("src/mbgl/renderer/layers/render_symbol_layer.cpp", "mbgl::RenderSymbolLayer::render"),
    ("src/mbgl/renderer/layers/render_fill_extrusion_layer.cpp",
     "mbgl::RenderFillExtrusionLayer::render"),
    ("src/mbgl/sprite/sprite_atlas.cpp", "mbgl::SpriteAtlas::upload"),
    ("src/mbgl/text/glyph_manager.cpp", "mbgl::GlyphManager::getGlyphs"),
    ("src/mbgl/gl/context.cpp", "mbgl::gl::Context::createTexture"),
]

CATEGORIES = {
    "usage": [
        ("调用 {api} 后地图没有按预期刷新", "缺少对样式重新加载的调用"),
        ("自定义样式 JSON 部分字段被忽略", "样式字段名称拼写与文档不一致"),
        ("初始化顺序不同导致地图空白", "地图对象在样式加载前被复用"),
    ],
    "visual": [
        ("高缩放级别下路网出现断裂缝隙", "三角化时瓦片边界顶点未闭合"),
        ("标注在旋转后错位", "符号布局未考虑相机旋转矩阵"),
        ("填充色在拼接处出现色带", "颜色插值精度不足"),
    ],
    "crash": [
        ("加载大瓦片时进程崩溃", "空指针未判空直接解引用"),
        ("快速缩放导致崩溃", "异步任务回调访问已释放对象"),
        ("多线程下偶发崩溃", "共享纹理资源缺少同步"),
    ],
    "perf": [
        ("帧率下降到 20fps", "绘制调用未合批"),
        ("内存持续增长", "纹理缓存未按上限淘汰"),
        ("首屏加载变慢", "瓦片预取策略过于激进"),
    ],
}

APIS = ["setStyle", "addLayer", "setCamera", "loadStyleURL", "setCustomMapStyle"]

VERSIONS = ["6.0.1", "6.0.0", "5.5.2"]


def build_tickets(count: int = 64, seed: int = 20261117) -> list[dict]:
    rng = random.Random(seed)
    plan = [("usage", 26), ("visual", 13), ("crash", 13), ("perf", 12)]
    tickets: list[dict] = []
    index = 0
    for category, amount in plan:
        templates = CATEGORIES[category]
        for _ in range(amount):
            index += 1
            symptom, root_cause = rng.choice(templates)
            path, symbol = rng.choice(FILES)
            tickets.append(
                {
                    "id": f"SYN-{index:03d}",
                    "category": category,
                    "symptom": symptom.format(api=rng.choice(APIS)),
                    "root_cause": root_cause,
                    "root_cause_file": path,
                    "root_cause_symbol": symbol,
                    "fix_commit": f"{rng.getrandbits(32):08x}",
                    "affected_versions": rng.sample(VERSIONS, k=rng.choice([1, 1, 2])),
                    "sdk_version": rng.choice(VERSIONS),
                }
            )
    return tickets


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_PATH
    tickets = build_tickets()
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "synthetic",
        "generated_at": datetime.now(UTC).isoformat(),
        "count": len(tickets),
        "tickets": tickets,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {}
    for ticket in tickets:
        summary[ticket["category"]] = summary.get(ticket["category"], 0) + 1
    print(json.dumps({"path": str(out), "count": len(tickets), "by_category": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
