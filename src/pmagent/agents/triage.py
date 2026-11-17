"""症状 → 渲染管线阶段映射。"""

from __future__ import annotations

from pmagent.contracts import Triage

STAGE_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("tile_loading", "瓦片调度与加载", ("瓦片", "tile", "加载", "空白", "白块")),
    ("geometry", "几何与三角化", ("断裂", "畸形", "几何", "三角", "接缝", "缝隙")),
    ("texture", "纹理与图集", ("纹理", "模糊", "色带", "图集", "atlas")),
    ("shader", "着色器", ("着色", "shader", "全黑", "编译失败", "glsl")),
    ("draw_order", "绘制顺序与深度", ("层级", "穿透", "遮挡", "深度", "z-order")),
    ("camera", "相机与投影", ("缩放", "漂移", "抖动", "倾斜", "视角")),
    ("style", "样式解析", ("样式", "颜色不对", "style", "配色")),
    ("label", "标注布局与碰撞", ("标注", "label", "重叠", "错位")),
    ("memory", "内存与生命周期", ("内存", "oom", "增长", "释放", "泄漏")),
    ("scheduling", "任务调度与绘制调用", ("卡顿", "掉帧", "fps", "主线程", "阻塞")),
]

DEFAULT_STAGE = ("unknown", "未归类")


def triage(text: str, symptoms: list[str] | None = None) -> Triage:
    """返回阶段与归类依据；命中多个时取最早命中的规则。"""
    content = " ".join([text, *(symptoms or [])])
    lowered = content.lower()
    matches: list[tuple[str, str, str]] = []
    for stage, label, keywords in STAGE_RULES:
        for keyword in keywords:
            if keyword.lower() in lowered:
                matches.append((stage, label, keyword))
                break
    if not matches:
        return Triage(stage=DEFAULT_STAGE[0], rationale="未命中任何管线阶段关键词")
    stage, label, keyword = matches[0]
    others = [item[1] for item in matches[1:]]
    rationale = f"命中关键词「{keyword}」，归入 {label}"
    if others:
        rationale += f"；同时命中 {', '.join(others)}，取首个"
    return Triage(stage=stage, rationale=rationale)


def stage_label(stage: str) -> str:
    for candidate, label, _keywords in STAGE_RULES:
        if candidate == stage:
            return label
    return "未归类"


VERIFICATION_BY_STAGE: dict[str, list[str]] = {
    "tile_loading": [
        "在瓦片请求入口加日志，确认请求是否返回与解码是否成功",
        "用 --repro 沙箱复现同一缩放级别，比对瓦片命中情况",
    ],
    "geometry": [
        "对候选函数加临时日志，打印顶点数与边界值",
        "为三角化函数补一个最小单测，覆盖瓦片边界坐标",
    ],
    "texture": [
        "检查纹理上传日志与图集分配记录",
        "在沙箱中固定相机位置复现并截图比对",
    ],
    "shader": [
        "打开着色器编译日志，确认编译状态",
        "切换到另一渲染后端验证是否复现",
    ],
    "draw_order": [
        "打印绘制调用顺序与深度状态",
        "二分定位引入该层级的提交",
    ],
    "camera": [
        "记录相机矩阵与缩放级别，比对异常前后数值",
        "用固定视角复现并对比预期矩阵",
    ],
    "style": [
        "打印样式解析结果与字段来源",
        "用官方样式 JSON 做对照实验",
    ],
    "label": [
        "记录标注布局前后的碰撞检测结果",
        "固定缩放级别复现并截图标注位置",
    ],
    "memory": [
        "采集内存快照并比对前后差异",
        "用复现工程跑长时间循环观察增长",
    ],
    "scheduling": [
        "记录每帧绘制调用数与主线程阻塞时间",
        "对候选任务二分提交定位退化点",
    ],
}


def verification_steps(stage: str) -> list[str]:
    return list(VERIFICATION_BY_STAGE.get(stage, ["补充日志点复现，或对可疑提交做二分"]))


__all__ = [
    "DEFAULT_STAGE",
    "STAGE_RULES",
    "VERIFICATION_BY_STAGE",
    "stage_label",
    "triage",
    "verification_steps",
]
