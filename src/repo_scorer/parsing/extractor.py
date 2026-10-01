"""Extract functions, methods and classes from a Python AST.

For each code unit we record size, cyclomatic complexity, docstring and
type-hint presence, and a *normalized hash* used later for clone detection.
"""

import ast
import copy
import hashlib
from dataclasses import dataclass

FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

# Each of these adds one independent path through a function.
_DECISION_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp,
    ast.ExceptHandler, ast.Assert, ast.comprehension, ast.match_case,
)


@dataclass
class UnitInfo:
    kind: str  # function | method | class
    name: str
    qualname: str
    lineno: int
    end_lineno: int
    loc: int
    complexity: int | None
    arg_count: int
    has_docstring: bool
    has_type_hints: bool
    normalized_hash: str | None


# ---------------------------------------------------------------- metrics


def count_loc(source: str) -> int:
    """Lines of code: non-blank lines that aren't pure comments."""
    return sum(
        1 for line in source.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def cyclomatic_complexity(func: ast.AST) -> int:
    """McCabe complexity: 1 + number of decision points.

    Nested functions, classes and lambdas are separate scopes, so they're skipped.
    """
    complexity = 1
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (*FUNC_NODES, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, _DECISION_NODES):
            complexity += 1
        if isinstance(node, ast.comprehension):
            complexity += len(node.ifs)  # each `if` filter is another branch
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1  # `a and b and c` = 2 branches
        stack.extend(ast.iter_child_nodes(node))
    return complexity


def _params(func: ast.AST, is_method: bool) -> list[ast.arg]:
    a = func.args
    params = [*a.posonlyargs, *a.args, *a.kwonlyargs]
    if a.vararg:
        params.append(a.vararg)
    if a.kwarg:
        params.append(a.kwarg)
    if is_method and params and params[0].arg in ("self", "cls"):
        params = params[1:]
    return params


def _fully_typed(func: ast.AST, params: list[ast.arg]) -> bool:
    """Every parameter annotated, plus a return annotation (optional for __init__)."""
    args_ok = all(p.annotation is not None for p in params)
    return_ok = func.returns is not None or func.name == "__init__"
    return args_ok and return_ok


# ---------------------------------------------------------------- clone hashing


class _Normalizer(ast.NodeTransformer):
    """Erase names and literal values so renamed copies look identical."""

    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node):
        node.arg = "_"
        node.annotation = None
        node.type_comment = None
        return node

    def visit_Attribute(self, node):
        self.generic_visit(node)
        node.attr = "_"
        return node

    def visit_Constant(self, node):
        # Keep the literal's *type* (str/int/...) but not its value.
        return ast.copy_location(ast.Constant(value=type(node.value).__name__), node)

    def _visit_func(self, node):
        node.name = "_"
        node.decorator_list = []
        node.returns = None
        self.generic_visit(node)
        return node

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func


def normalized_hash(func: ast.AST) -> str:
    """SHA-256 of a function's structure, ignoring names, literals and docstring.

    Two functions with the same hash are structural ("Type-2") clones.
    """
    node = copy.deepcopy(func)
    if ast.get_docstring(node) is not None:
        node.body = node.body[1:] or [ast.Pass()]
    node = _Normalizer().visit(node)
    dump = ast.dump(node, annotate_fields=False, include_attributes=False)
    return hashlib.sha256(dump.encode()).hexdigest()


# ---------------------------------------------------------------- extraction


def is_overload(func: ast.AST) -> bool:
    """True for @overload / @typing.overload stubs: type-checker hints that never run."""
    for dec in func.decorator_list:
        if (isinstance(dec, ast.Name) and dec.id == "overload") or (
            isinstance(dec, ast.Attribute) and dec.attr == "overload"
        ):
            return True
    return False


def extract_units(tree: ast.Module) -> list[UnitInfo]:
    units: list[UnitInfo] = []

    def visit(node: ast.AST, scope: list[str], in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qual = [*scope, child.name]
                end = child.end_lineno or child.lineno
                units.append(UnitInfo(
                    kind="class",
                    name=child.name,
                    qualname=".".join(qual),
                    lineno=child.lineno,
                    end_lineno=end,
                    loc=end - child.lineno + 1,
                    complexity=None,
                    arg_count=0,
                    has_docstring=ast.get_docstring(child) is not None,
                    has_type_hints=False,
                    normalized_hash=None,
                ))
                visit(child, qual, in_class=True)

            elif isinstance(child, FUNC_NODES):
                if is_overload(child):
                    continue  # only the real implementation counts
                qual = [*scope, child.name]
                end = child.end_lineno or child.lineno
                params = _params(child, is_method=in_class)
                units.append(UnitInfo(
                    kind="method" if in_class else "function",
                    name=child.name,
                    qualname=".".join(qual),
                    lineno=child.lineno,
                    end_lineno=end,
                    loc=end - child.lineno + 1,
                    complexity=cyclomatic_complexity(child),
                    arg_count=len(params),
                    has_docstring=ast.get_docstring(child) is not None,
                    has_type_hints=_fully_typed(child, params),
                    normalized_hash=normalized_hash(child),
                ))
                visit(child, qual, in_class=False)  # nested defs are functions

            else:
                visit(child, scope, in_class)  # e.g. defs inside `if` blocks

    visit(tree, [], in_class=False)
    return units
