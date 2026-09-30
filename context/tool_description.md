# Tool descriptions sent to vLLM

Extracted verbatim from the first captured request in `logs/remote/model_requests/fastapi_11194.jsonl`. These are the `function.description` strings in the top-level `tools` array. Parameter schemas are included as sent.

## run_command

```text
Execute a shell command inside the repository sandbox.

        You can run any shell command (Python scripts, tests, git commands, etc.).
        The command runs in the repository workspace. No network access.

        Args:
            command: Shell command to execute (e.g., "pytest tests/", "python3 script.py").

```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "command": {
      "type": "string"
    }
  },
  "required": ["command"]
}
```

## read_file

```text
Read the contents of a file from the repository workspace.

        Supports reading specific line ranges using start_line and end_line (1-indexed).
        To avoid overwhelming the context window, large files are automatically truncated
        to a maximum number of lines (default: 150 lines).

        Args:
            filepath: Relative path within the workspace directory (e.g., "fastapi/main.py", "pyproject.toml").
            start_line: Optional starting line number (1-indexed, inclusive).
            end_line: Optional ending line number (1-indexed, inclusive).

        Returns:
            JSON string containing file content, line range, and truncation status.

```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "filepath": {
      "type": "string"
    },
    "start_line": {
      "any_of": [
        {
          "type": "INTEGER"
        },
        {
          "type": "NULL"
        }
      ],
      "nullable": true
    },
    "end_line": {
      "any_of": [
        {
          "type": "INTEGER"
        },
        {
          "type": "NULL"
        }
      ],
      "nullable": true
    }
  },
  "required": ["filepath"]
}
```

## edit_file

```text
Replace a contiguous block of text in an existing file.

        More token-efficient than write_file for targeted changes. Provide 2-4 lines of unique
        surrounding context in old_string to ensure an unambiguous match.

        Args:
            filepath: Relative path to the target file.
            old_string: Text block to replace.
            new_string: Replacement text block.
            allow_multiple: Replace all occurrences of old_string if True.

        Returns:
            JSON string containing file status, occurrences count, match strategy, and unified diff.

```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "filepath": {
      "type": "string"
    },
    "old_string": {
      "type": "string"
    },
    "new_string": {
      "type": "string"
    },
    "allow_multiple": {
      "default": false,
      "type": "boolean"
    }
  },
  "required": ["filepath", "old_string", "new_string"]
}
```

## write_file

```text
Create or overwrite a file in the workspace.

        Args:
            filepath: Relative path to the file to create or overwrite.
            content: Full content of the file.

```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "filepath": {
      "type": "string"
    },
    "content": {
      "type": "string"
    }
  },
  "required": ["filepath", "content"]
}
```

## get_status

```text
Check active budget details (time, tool calls) and active patch details.
```

Parameters sent:

```json
{
  "type": "object",
  "properties": {}
}
```

## submit_patch

```text
Capture the current working tree modifications as the agent's submission.

        This generates a unified git diff against the baseline commit. The agent
        can submit multiple times; only the final submission is evaluated.

```

Parameters sent:

```json
{
  "type": "object",
  "properties": {}
}
```

## get_code_neighbors

```text
Discover structural code graph neighbors (callers, callees, definitions) for a given code node.

        Args:
            node: Name of the function, method, or class node (e.g. 'parse_request' or 'app.routes.get_user').
            edge_type: Optional edge relation filter (e.g. 'CALLS', 'DEFINED_IN', 'IMPORTS').
            max_neighbors: Maximum number of neighbor nodes to return (default: 50).

        Returns:
            JSON string with list of neighbor node names and count.

```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "node": {
      "type": "string"
    },
    "edge_type": {
      "any_of": [
        {
          "type": "STRING"
        },
        {
          "type": "NULL"
        }
      ],
      "nullable": true
    },
    "max_neighbors": {
      "default": 50,
      "type": "integer"
    }
  },
  "required": ["node"]
}
```

## search_similar_code

```text
Find semantically similar code functions and classes using graph vector embeddings.

        Args:
            query: Function name or code snippet to search for similar implementations.
            k: Number of most similar code nodes to return (default: 10).

        Returns:
            JSON string containing matching node names, code snippets, and similarity scores.

```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string"
    },
    "k": {
      "default": 10,
      "type": "integer"
    }
  },
  "required": ["query"]
}
```

## get_code_subgraph

```text
Extract the relationship graph (nodes and connecting edges) for a focal set of code elements.

        Args:
            nodes: List of function/class node names to include in the induced subgraph.

        Returns:
            JSON string detailing the subgraph nodes and connecting directed edges.

```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "nodes": {
      "items": {
        "type": "string"
      },
      "type": "array"
    }
  },
  "required": ["nodes"]
}
```

## code_analyzer_agent

```text
Analyzes repository source files and symbol graphs to locate root causes.
```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "request": {
      "type": "string"
    }
  },
  "required": ["request"]
}
```

## list_skills

```text
Lists all available skills with their names and descriptions.
```

Parameters sent:

```json
{
  "type": "object",
  "properties": {}
}
```

## load_skill

```text
Loads the SKILL.md instructions for a given skill.
```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "skill_name": {
      "type": "string",
      "description": "The name of the skill to load."
    }
  },
  "required": ["skill_name"]
}
```

## load_skill_resource

```text
Loads a resource file (from references/, assets/, or scripts/) from within a skill.
```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "skill_name": {
      "type": "string",
      "description": "The name of the skill."
    },
    "file_path": {
      "type": "string",
      "description": "The relative path to the resource (e.g., 'references/my_doc.md', 'assets/template.txt', or 'scripts/setup.sh')."
    }
  },
  "required": ["skill_name", "file_path"]
}
```

## run_skill_script

```text
Executes a script from a skill's scripts/ directory.
```

Parameters sent:

```json
{
  "type": "object",
  "properties": {
    "skill_name": {
      "type": "string",
      "description": "The name of the skill."
    },
    "file_path": {
      "type": "string",
      "description": "The relative path to the script (e.g., 'scripts/setup.py')."
    },
    "args": {
      "anyOf": [
        {
          "type": "object"
        },
        {
          "type": "array",
          "items": {
            "type": "string"
          }
        }
      ],
      "description": "Optional arguments to pass to the script as key-value pairs (long options) or as a list of strings. If specified as a list, it is treated as the complete list of arguments, and 'short_options' and 'positional_args' must not be provided."
    },
    "short_options": {
      "type": "object",
      "description": "Optional short options (single hyphen) to pass to the script as key-value pairs. Must not be provided if 'args' is a list."
    },
    "positional_args": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "Optional positional arguments to pass to the script. Must not be provided if 'args' is a list."
    }
  },
  "required": ["skill_name", "file_path"]
}
```

regex maching for the search similar query

Matching precedence: 1. Exact match (e.g. 'fastapi.applications.FastAPI') 2. Suffix match on delimiter boundary (e.g. '.FastAPI' or ';FastAPI') 3. Case-insensitive exact / boundary match 4. Substring containment match
"""
