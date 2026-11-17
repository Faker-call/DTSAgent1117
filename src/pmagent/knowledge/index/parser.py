"""tree-sitter-cpp 符号抽取：命名空间、类、函数、宏、include 与调用点。"""

from __future__ import annotations

from typing import Any

from pmagent.knowledge.index.models import IncludeEdge, ParsedFile, RefEdge, Symbol

_PARSER: Any | None = None

NAME_TYPES = {
    "identifier",
    "field_identifier",
    "qualified_identifier",
    "type_identifier",
    "destructor_name",
    "operator_name",
}
CONTAINER_TYPES = {
    "translation_unit",
    "declaration_list",
    "field_declaration_list",
    "linkage_specification",
    "template_declaration",
}


def get_parser() -> Any:
    global _PARSER
    if _PARSER is None:
        import tree_sitter_cpp
        from tree_sitter import Language, Parser

        _PARSER = Parser(Language(tree_sitter_cpp.language()))
    return _PARSER


def _text(node: Any) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _find_name(node: Any) -> str:
    if node is None:
        return ""
    if node.type in NAME_TYPES:
        return _text(node).strip()
    for child in node.children:
        found = _find_name(child)
        if found:
            return found
    return ""


def _qualify(namespace: list[str], classes: list[str], name: str) -> str:
    parts = [*namespace, *classes]
    if "::" in name:
        return "::".join([*parts, name]) if parts else name
    return "::".join([*parts, name]) if parts else name


def parse_source(path: str, source: str | bytes) -> ParsedFile:
    data = source.encode("utf-8") if isinstance(source, str) else source
    parser = get_parser()
    tree = parser.parse(data)
    parsed = ParsedFile(path=path)
    if tree.root_node.has_error:
        parsed.parse_error = "syntax error"

    _walk(tree.root_node, parsed, [], [])
    return parsed


def _walk(node: Any, parsed: ParsedFile, namespace: list[str], classes: list[str]) -> None:
    for child in node.children:
        node_type = child.type
        if node_type == "namespace_definition":
            namespace_name = _text(child.child_by_field_name("name")).strip()
            new_ns = [*namespace, namespace_name] if namespace_name else list(namespace)
            if namespace_name:
                parsed.symbols.append(
                    Symbol(
                        name=namespace_name,
                        qualified_name="::".join(new_ns),
                        kind="namespace",
                        signature=f"namespace {namespace_name}",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                    )
                )
            body = child.child_by_field_name("body")
            if body is not None:
                _walk(body, parsed, new_ns, [])
        elif node_type in {"class_specifier", "struct_specifier"}:
            class_name = _text(child.child_by_field_name("name")).strip()
            qualified = _qualify(namespace, classes, class_name) if class_name else ""
            parent = "::".join([*namespace, *classes]) or None
            if class_name:
                parsed.symbols.append(
                    Symbol(
                        name=class_name,
                        qualified_name=qualified,
                        kind="class",
                        signature=f"{node_type.split('_')[0]} {class_name}",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        parent=parent,
                        namespace="::".join(namespace),
                        class_name=class_name,
                    )
                )
            body = child.child_by_field_name("body")
            if body is not None:
                _walk(body, parsed, namespace, [*classes, class_name] if class_name else classes)
        elif node_type == "function_definition":
            declarator = child.child_by_field_name("declarator")
            name = _find_name(declarator)
            if not name:
                continue
            qualified = _qualify(namespace, classes, name)
            parsed.symbols.append(
                Symbol(
                    name=name.split("::")[-1],
                    qualified_name=qualified,
                    kind="function",
                    signature=_text(declarator).strip(),
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    parent="::".join([*namespace, *classes]) or None,
                    namespace="::".join(namespace),
                    class_name=classes[-1] if classes else "",
                )
            )
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_calls(body, parsed, qualified)
        elif node_type in {"field_declaration", "declaration"}:
            declarator = child.child_by_field_name("declarator")
            if declarator is not None and declarator.type == "function_declarator":
                name = _find_name(declarator)
                if name:
                    parsed.symbols.append(
                        Symbol(
                            name=name.split("::")[-1],
                            qualified_name=_qualify(namespace, classes, name),
                            kind="declaration",
                            signature=_text(declarator).strip(),
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            parent="::".join([*namespace, *classes]) or None,
                            namespace="::".join(namespace),
                            class_name=classes[-1] if classes else "",
                        )
                    )
            nested = child.child_by_field_name("body")
            if nested is not None:
                _walk(nested, parsed, namespace, classes)
        elif node_type in {"preproc_def", "preproc_function_def"}:
            name = _text(child.child_by_field_name("name")).strip() or _find_name(child)
            if name:
                parsed.symbols.append(
                    Symbol(
                        name=name,
                        qualified_name=name,
                        kind="macro",
                        signature=f"#define {name}",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        namespace="::".join(namespace),
                    )
                )
        elif node_type == "preproc_include":
            target = ""
            for sub in child.children:
                if sub.type in {"system_lib_string", "string_literal"}:
                    target = _text(sub).strip('<>"')
            parsed.includes.append(IncludeEdge(source_path=parsed.path, target=target))
        elif node_type in CONTAINER_TYPES:
            _walk(child, parsed, namespace, classes)


def _collect_calls(body: Any, parsed: ParsedFile, from_symbol: str) -> None:
    stack = [body]
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            target = _text(function).strip()
            if target:
                parsed.refs.append(
                    RefEdge(
                        from_symbol=from_symbol,
                        to_name=target.split("::")[-1],
                        kind="call",
                        line=node.start_point[0] + 1,
                    )
                )
        stack.extend(node.children)
