"""Calculate tool for performing arithmetic operations."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable

from agents import function_tool

_MAX_EXPRESSION_CHARS = 200
_BinaryOperator = Callable[[float | int, float | int], float | int]
_UnaryOperator = Callable[[float | int], float | int]

_ALLOWED_BINARY_OPS: dict[type[ast.operator], _BinaryOperator] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY_OPS: dict[type[ast.unaryop], _UnaryOperator] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _ensure_number(value: object) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Expression contains non-numeric values")
    return value


def _evaluate_expression(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _evaluate_expression(node.body)

    if isinstance(node, ast.Constant):
        return _ensure_number(node.value)

    if isinstance(node, ast.UnaryOp):
        op_fn = _ALLOWED_UNARY_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError("Expression uses an unsupported unary operator")
        operand = _evaluate_expression(node.operand)
        return op_fn(operand)

    if isinstance(node, ast.BinOp):
        op_fn = _ALLOWED_BINARY_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError("Expression uses an unsupported operator")
        left = _evaluate_expression(node.left)
        right = _evaluate_expression(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ValueError("Exponent is too large")
        try:
            return op_fn(left, right)
        except ZeroDivisionError as exc:
            raise ValueError("Division by zero is not allowed") from exc

    raise ValueError("Expression contains unsupported syntax")


@function_tool(name_override="mcp__utilities__calculate")
def calculate(expression: str) -> str:
    """Evaluate a simple arithmetic expression and return the result.

    Args:
        expression: Python arithmetic expression.
    """
    expr = (expression or "").strip()
    if not expr:
        raise ValueError("Expression cannot be empty")
    if len(expr) > _MAX_EXPRESSION_CHARS:
        raise ValueError("Expression exceeds maximum length")

    try:
        parsed = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid expression syntax") from exc

    result = _evaluate_expression(parsed)
    if isinstance(result, float) and not math.isfinite(result):
        raise ValueError("Expression result must be finite")
    return f"Result: {result}"
