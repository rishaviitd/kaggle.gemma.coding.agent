"""Execution and status inspection tools for SWE-gemma."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from swegemma.tools.base import budget_gated, error_response, ok_response

logger = logging.getLogger(__name__)


@budget_gated
def run_command(ctx: Any, command: str) -> str:
    """Execute a shell command inside the repository sandbox.

    You can run any shell command (Python scripts, tests, git commands, etc.).
    The command runs in the repository workspace. No network access.

    Args:
        ctx: Context instance.
        command: Shell command to execute (e.g., "pytest tests/", "python3 script.py").
    """
    # Determine effective command timeout
    cmd_timeout = ctx.harness.command_timeout_seconds
    if ctx.budget.time_minutes is not None:
        remaining = ctx.remaining_time_seconds
        if remaining is not None:
            # Provide a minimum floor of 5s on remaining time so near-deadline commands don't fail immediately on 1s
            effective_remaining = max(5, int(remaining))
            if cmd_timeout is not None:
                effective_timeout = min(cmd_timeout, effective_remaining)
            else:
                effective_timeout = effective_remaining
        else:
            effective_timeout = cmd_timeout
    else:
        effective_timeout = cmd_timeout

    result = ctx.docker.exec(ctx.container_id, command, timeout=effective_timeout)
    if result.timed_out:
        return error_response(
            error_type='TimeoutExceeded',
            error_message=f'Command timed out after {effective_timeout} seconds',
        )

    max_chars = ctx.harness.max_stdout_chars
    stdout_str = result.stdout[:max_chars] if max_chars is not None else result.stdout
    stderr_str = result.stderr[:max_chars] if max_chars is not None else result.stderr

    if result.exit_code != 0:
        return error_response(
            error_type='CommandError',
            error_message=stderr_str if stderr_str else stdout_str,
            details={
                'stdout': stdout_str,
                'stderr': stderr_str,
                'exit_code': result.exit_code,
            },
        )

    return ok_response(
        stdout=stdout_str,
        stderr=stderr_str,
        exit_code=0,
    )


@budget_gated(count_tool_call=False)
def submit_patch(ctx: Any) -> str:
    """Capture the current working tree modifications as the agent's submission.

    This generates a unified git diff against the baseline commit. The agent
    can submit multiple times; only the final submission is evaluated.

    Args:
        ctx: Context instance.
    """
    ctx.docker.exec(ctx.container_id, 'cd /workspace && git add -N .')
    res = ctx.docker.exec(
        ctx.container_id,
        'cd /workspace && (git diff --binary _swegemma_baseline 2>/dev/null || git diff --binary HEAD)',
    )
    if res.exit_code != 0:
        return error_response(
            error_type='GitDiffError',
            error_message=f'git diff failed with exit code {res.exit_code}: {res.stderr}',
        )

    diff = res.stdout
    ctx.submitted_patch = diff
    ctx.patch_submitted = True
    return ok_response(
        patch_size=len(diff),
        files_changed=diff.count('diff --git'),
    )


@budget_gated(count_tool_call=False)
def get_status(ctx: Any) -> str:
    """Check active budget details (time, tool calls) and active patch details.

    Args:
        ctx: Context instance.
    """
    status_data: dict[str, Any] = {
        'status': 'ok',
        'tool_calls_used': ctx.tool_calls_used,
        'patch_submitted': ctx.submitted_patch is not None,
        'patch_size': len(ctx.submitted_patch) if ctx.submitted_patch else 0,
    }
    if ctx.budget.tool_calls is not None:
        status_data['tool_calls_remaining'] = max(
            0, ctx.budget.tool_calls - ctx.tool_calls_used
        )
        status_data['max_tool_calls'] = ctx.budget.tool_calls
    if ctx.budget.time_minutes is not None:
        status_data['time_seconds_remaining'] = ctx.remaining_time_seconds
        status_data['max_time_minutes'] = ctx.budget.time_minutes
        status_data['agent_elapsed_seconds'] = ctx.agent_elapsed_seconds
    if ctx.budget.turns is not None:
        status_data['max_turns'] = ctx.budget.turns
    if ctx.harness.command_timeout_seconds is not None:
        status_data['command_timeout_seconds'] = ctx.harness.command_timeout_seconds
    return json.dumps(status_data)


def make_run_command(ctx: Any) -> Callable:
    def run_command_tool(command: str) -> str:
        """Execute a shell command inside the repository sandbox.

        You can run any shell command (Python scripts, tests, git commands, etc.).
        The command runs in the repository workspace. No network access.

        Args:
            command: Shell command to execute (e.g., "pytest tests/", "python3 script.py").
        """
        return run_command(ctx, command)

    run_command_tool.__name__ = 'run_command'
    return run_command_tool


def make_submit_patch(ctx: Any) -> Callable:
    def submit_patch_tool() -> str:
        """Capture the current working tree modifications as the agent's submission.

        This generates a unified git diff against the baseline commit. The agent
        can submit multiple times; only the final submission is evaluated.
        """
        return submit_patch(ctx)

    submit_patch_tool.__name__ = 'submit_patch'
    return submit_patch_tool


def make_get_status(ctx: Any) -> Callable:
    def get_status_tool() -> str:
        """Check active budget details (time, tool calls) and active patch details."""
        return get_status(ctx)

    get_status_tool.__name__ = 'get_status'
    return get_status_tool


# Backward compatibility aliases
get_session_status = get_status
make_get_session_status = make_get_status
