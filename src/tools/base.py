"""Base response helpers and decorators for SWE-gemma tools."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any


def ok_response(**kwargs: Any) -> str:
    """Format a successful tool JSON response."""
    data = {'status': 'ok'}
    data.update(kwargs)
    return json.dumps(data)


def error_response(
    error_type: str,
    error_message: str,
    details: dict[str, Any] | None = None,
) -> str:
    """Format an error tool JSON response."""
    data: dict[str, Any] = {
        'status': 'error',
        'error_type': error_type,
        'error_message': error_message,
    }
    if details is not None:
        data['details'] = details
    return json.dumps(data)


def _attach_low_budget_warning(ctx: Any, raw_response: str) -> str:
    """Attach a low-budget warning to a JSON tool response when <= 10 tool calls remain."""
    budget = getattr(ctx, 'budget', None)
    max_calls = getattr(budget, 'tool_calls', None) if budget is not None else None
    used_calls = getattr(ctx, 'tool_calls_used', None)
    if max_calls is None or used_calls is None or max_calls < 20:
        return raw_response

    remaining = max(0, max_calls - used_calls)
    if remaining > 10:
        return raw_response

    try:
        payload = json.loads(raw_response)
        if isinstance(payload, dict) and 'budget_warning' not in payload:
            payload['budget_warning'] = (
                f'Only {remaining} tool call(s) remaining ({used_calls}/{max_calls} used). '
                'Finalize your edits and call submit_patch soon.'
            )
            return json.dumps(payload)
    except Exception:
        pass
    return raw_response


def budget_gated(
    func: Callable | None = None,
    *,
    count_tool_call: bool = True,
) -> Callable:
    """Decorator ensuring that tool invocations check and adhere to the context budget."""

    def decorator(target: Callable) -> Callable:
        @functools.wraps(target)
        def wrapper(ctx: Any, *args: Any, **kwargs: Any) -> str:
            tool_lock = getattr(ctx, 'tool_lock', None)
            if tool_lock is not None:
                with tool_lock:
                    try:
                        budget_error = ctx.check_budget(
                            count_tool_call=count_tool_call
                        )
                    except TypeError:
                        budget_error = ctx.check_budget()
                    if budget_error:
                        return budget_error
                    res = target(ctx, *args, **kwargs)
            else:
                try:
                    budget_error = ctx.check_budget(
                        count_tool_call=count_tool_call
                    )
                except TypeError:
                    budget_error = ctx.check_budget()
                if budget_error:
                    return budget_error
                res = target(ctx, *args, **kwargs)

            if count_tool_call and isinstance(res, str):
                return _attach_low_budget_warning(ctx, res)
            return res

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
