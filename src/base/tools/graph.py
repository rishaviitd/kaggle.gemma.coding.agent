"""Graph inspection and semantic code search tools for SWE-gemma."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast

from swegemma.tools.base import budget_gated, error_response, ok_response

logger = logging.getLogger(__name__)


@budget_gated
def get_code_neighbors(
    ctx: Any,
    node: str,
    edge_type: str | None = None,
    max_neighbors: int = 50,
) -> str:
    """Discover structural code graph neighbors (callers, callees, definitions) for a given code node.

    Args:
        ctx: Context instance.
        node: Name of the function, method, or class node (e.g. 'parse_request' or 'app.routes.get_user').
        edge_type: Optional edge relation filter (e.g. 'CALLS', 'DEFINED_IN', 'IMPORTS').
        max_neighbors: Maximum number of neighbor nodes to return (default: 50).

    Returns:
        JSON string with list of neighbor node names and count.
    """
    task = getattr(ctx, 'task', None)
    task_repo = (
        task.get('repo')
        if isinstance(task, dict)
        else getattr(task, 'repo', None)
        if task
        else None
    )
    repo_name = (
        getattr(ctx, 'repo', None)
        or task_repo
        or getattr(ctx, 'graph_repo_key', None)
    )
    if not repo_name:
        return error_response(
            error_type='MissingRepoContext',
            error_message='No repository configured for graph queries.',
        )

    try:
        from swegemma import graph as sg

        base_commit = getattr(task, 'base_commit', None) if task else None
        if not base_commit and isinstance(task, dict):
            base_commit = task.get('base_commit')
        if base_commit and str(repo_name).endswith(f'_{base_commit}'):
            base_commit = None

        neighbors = sg.get_neighbor(
            node=node,
            repo_name=repo_name,
            max_neighbors=max_neighbors,
            edge_type=edge_type,
            graph_dir=ctx.graph_dir,
            embeddings_dir=ctx.embeddings_dir,
            base_commit=base_commit,
        )
        neighbor_list = (
            neighbors if isinstance(neighbors, list) else neighbors.get(node, [])
        )
        return ok_response(
            node=node,
            neighbors=neighbor_list,
            count=len(neighbor_list),
        )
    except Exception as e:
        return error_response(
            error_type='GraphLookupError',
            error_message=str(e),
        )


@budget_gated
def search_similar_code(
    ctx: Any,
    query: str,
    k: int = 10,
) -> str:
    """Find semantically similar code functions and classes using graph vector embeddings.

    Args:
        ctx: Context instance.
        query: Function name or code snippet to search for similar implementations.
        k: Number of most similar code nodes to return (default: 10).

    Returns:
        JSON string containing matching node names, code snippets, and similarity scores.
    """
    task = getattr(ctx, 'task', None)
    task_repo = (
        task.get('repo')
        if isinstance(task, dict)
        else getattr(task, 'repo', None)
        if task
        else None
    )
    repo_name = (
        getattr(ctx, 'repo', None)
        or task_repo
        or getattr(ctx, 'graph_repo_key', None)
    )
    if not repo_name:
        return error_response(
            error_type='MissingRepoContext',
            error_message='No repository configured for graph queries.',
        )

    try:
        from swegemma import graph as sg

        base_commit = getattr(task, 'base_commit', None) if task else None
        if not base_commit and isinstance(task, dict):
            base_commit = task.get('base_commit')
        if base_commit and str(repo_name).endswith(f'_{base_commit}'):
            base_commit = None

        similar_nodes = sg.get_similar_nodes(
            node=query,
            repo_name=repo_name,
            k=k,
            graph_dir=ctx.graph_dir,
            embeddings_dir=ctx.embeddings_dir,
            base_commit=base_commit,
        )
        formatted = [
            {
                'node_name': item['node_name'],
                'code': item.get('code', ''),
                'similarity': round(float(item.get('similarity', 0.0)), 4),
            }
            for item in similar_nodes
        ]
        return ok_response(
            query=query,
            results=formatted,
            count=len(formatted),
        )
    except Exception as e:
        return error_response(
            error_type='SimilaritySearchError',
            error_message=str(e),
        )


@budget_gated
def get_code_subgraph(
    ctx: Any,
    nodes: list[str],
) -> str:
    """Extract the relationship graph (nodes and connecting edges) for a focal set of code elements.

    Args:
        ctx: Context instance.
        nodes: List of function/class node names to include in the induced subgraph.

    Returns:
        JSON string detailing the subgraph nodes and connecting directed edges.
    """
    task = getattr(ctx, 'task', None)
    task_repo = (
        task.get('repo')
        if isinstance(task, dict)
        else getattr(task, 'repo', None)
        if task
        else None
    )
    repo_name = (
        getattr(ctx, 'repo', None)
        or task_repo
        or getattr(ctx, 'graph_repo_key', None)
    )
    if not repo_name:
        return error_response(
            error_type='MissingRepoContext',
            error_message='No repository configured for graph queries.',
        )

    try:
        from swegemma import graph as sg

        base_commit = getattr(task, 'base_commit', None) if task else None
        if not base_commit and isinstance(task, dict):
            base_commit = task.get('base_commit')
        if base_commit and str(repo_name).endswith(f'_{base_commit}'):
            base_commit = None

        full_graph = sg.get_graph(
            repo_name=repo_name,
            graph_dir=ctx.graph_dir,
            embeddings_dir=ctx.embeddings_dir,
            base_commit=base_commit,
        )
        subgraph = sg.get_induced_subgraph(full_graph, cast(Any, nodes))

        edges = []
        for u, v, data in subgraph.edges(data=True):
            edges.append(
                {
                    'from': str(u),
                    'to': str(v),
                    'type': data.get('type') or data.get('relation', 'connected'),
                }
            )

        return ok_response(
            nodes=[str(n) for n in subgraph.nodes()],
            edges=edges,
            node_count=len(subgraph.nodes()),
            edge_count=len(edges),
        )
    except Exception as e:
        return error_response(
            error_type='SubgraphExtractionError',
            error_message=str(e),
        )


def make_get_code_neighbors(ctx: Any) -> Callable:
    def get_code_neighbors_tool(
        node: str,
        edge_type: str | None = None,
        max_neighbors: int = 50,
    ) -> str:
        """Discover structural code graph neighbors (callers, callees, definitions) for a given code node.

        Args:
            node: Name of the function, method, or class node (e.g. 'parse_request' or 'app.routes.get_user').
            edge_type: Optional edge relation filter (e.g. 'CALLS', 'DEFINED_IN', 'IMPORTS').
            max_neighbors: Maximum number of neighbor nodes to return (default: 50).

        Returns:
            JSON string with list of neighbor node names and count.
        """
        return get_code_neighbors(ctx, node, edge_type, max_neighbors)

    get_code_neighbors_tool.__name__ = 'get_code_neighbors'
    return get_code_neighbors_tool


def make_search_similar_code(ctx: Any) -> Callable:
    def search_similar_code_tool(
        query: str,
        k: int = 10,
    ) -> str:
        """Find semantically similar code functions and classes using graph vector embeddings.

        Args:
            query: Function name or code snippet to search for similar implementations.
            k: Number of most similar code nodes to return (default: 10).

        Returns:
            JSON string containing matching node names, code snippets, and similarity scores.
        """
        return search_similar_code(ctx, query, k)

    search_similar_code_tool.__name__ = 'search_similar_code'
    return search_similar_code_tool


def make_get_code_subgraph(ctx: Any) -> Callable:
    def get_code_subgraph_tool(nodes: list[str]) -> str:
        """Extract the relationship graph (nodes and connecting edges) for a focal set of code elements.

        Args:
            nodes: List of function/class node names to include in the induced subgraph.

        Returns:
            JSON string detailing the subgraph nodes and connecting directed edges.
        """
        return get_code_subgraph(ctx, nodes)

    get_code_subgraph_tool.__name__ = 'get_code_subgraph'
    return get_code_subgraph_tool
