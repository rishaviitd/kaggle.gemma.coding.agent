"""Modular tools for SWE-gemma evaluation context."""

from collections.abc import Callable
from typing import Any

from swegemma.tools.base import budget_gated, error_response, ok_response
from swegemma.tools.execution import (
    get_status,
    make_get_status,
    make_run_command,
    make_submit_patch,
    run_command,
    submit_patch,
)
from swegemma.tools.graph import (
    get_code_neighbors,
    get_code_subgraph,
    make_get_code_neighbors,
    make_get_code_subgraph,
    make_search_similar_code,
    search_similar_code,
)
from swegemma.tools.workspace import (
    _resolve_workspace_path,
    edit_file,
    make_edit_file,
    make_read_file,
    make_write_file,
    read_file,
    write_file,
)


def create_tools(ctx: Any) -> dict[str, Callable]:
    """Create all standard evaluation tools bound to the provided context."""
    return {
        'run_command': make_run_command(ctx),
        'read_file': make_read_file(ctx),
        'write_file': make_write_file(ctx),
        'edit_file': make_edit_file(ctx),
        'submit_patch': make_submit_patch(ctx),
        'get_status': make_get_status(ctx),
        'get_code_neighbors': make_get_code_neighbors(ctx),
        'search_similar_code': make_search_similar_code(ctx),
        'get_code_subgraph': make_get_code_subgraph(ctx),
    }


__all__ = [
    '_resolve_workspace_path',
    'budget_gated',
    'create_tools',
    'edit_file',
    'error_response',
    'get_code_neighbors',
    'get_code_subgraph',
    'get_status',
    'ok_response',
    'read_file',
    'run_command',
    'search_similar_code',
    'submit_patch',
    'write_file',
]
