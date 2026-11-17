"""补丁链路评测：diff 校验通过率与静态检查覆盖情况。

用法：python scripts/eval_patch.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pmagent.agents.patch import PatchContext, self_review, static_checks, validate_diff

GOOD = """--- a/src/a.cc
+++ b/src/a.cc
@@ -10,3 +10,3 @@
 int BuildStrip(int n) {
-    return n;
+    return n + 1;
 }
"""

MINIMAL = """--- a/src/b.cc
+++ b/src/b.cc
@@ -1,1 +1,1 @@
-int A() { return 0; }
+int A() { return 1; }
"""

THREAD = """--- a/src/c.cc
+++ b/src/c.cc
@@ -5,2 +5,2 @@
-    counter += 1;
+    counter.fetch_add(1, std::memory_order_relaxed);
"""

MULTI = GOOD + """--- a/src/d.cc
+++ b/src/d.cc
@@ -1,1 +1,1 @@
-x
+y
""" + """--- a/src/e.cc
+++ b/src/e.cc
@@ -1,1 +1,1 @@
-x
+y
""" + """--- a/src/f.cc
+++ b/src/f.cc
@@ -1,1 +1,1 @@
-x
+y
"""

HEADER = """--- a/include/mln/api.hpp
+++ b/include/mln/api.hpp
@@ -1,1 +1,1 @@
-x
+y
"""

CASES = [
    ("good", GOOD, True),
    ("minimal", MINIMAL, True),
    ("thread", THREAD, True),
    ("multi-file", MULTI, False),
    ("header", HEADER, False),
]


def main() -> int:
    context = PatchContext(file="src/a.cc", symbol="pm::BuildStrip", related_tests=["test/a.cc"])
    validation_pass = 0
    compile_statuses: Counter[str] = Counter()
    reviews = []
    for name, diff, expected_valid in CASES:
        allowed = [
            Path(line[6:]).as_posix()
            for line in diff.splitlines()
            if line.startswith("+++ b/")
        ] + ["a.cc"]
        errors = validate_diff(diff, allowed_files=allowed)
        valid = not errors
        if valid == expected_valid:
            validation_pass += 1
        checks = static_checks(diff)
        compile_statuses[checks.compile.status] += 1
        review = self_review(diff, context)
        reviews.append(
            {
                "case": name,
                "valid": valid,
                "score": review["score"],
                "errors": errors[:2],
            }
        )

    report = {
        "cases": len(CASES),
        "validation_expectation_match": round(validation_pass / len(CASES), 3),
        "validation_pass_rate": round(
            sum(1 for item in reviews if item["valid"]) / len(CASES), 3
        ),
        "compile_check_status": dict(compile_statuses),
        "compile_pass_rate": None,
        "human_acceptance_rate": None,
        "note": "本机无 C++ 编译器，编译通过率与人工接受率无法测量；字段保留为 null",
        "reviews": reviews,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
