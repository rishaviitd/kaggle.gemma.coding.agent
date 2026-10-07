---
name: file-outline
description: List every class and function in one Python file with its line range, so you can read only the part you need. Use before read_file on a Python file over 150 lines.
---

# File outline

A plain read_file returns only the first 150 lines of a file (usually imports).

1. Get the outline (one call):
   run_skill_script(skill_name="file-outline", file_path="scripts/outline.py", args=["fastapi/routing.py"])
   Output: one line per definition, for example `L930-976 def Console.is_terminal(self) -> bool`.
2. Read only that range:
   read_file(filepath="rich/console.py", start_line=930, end_line=976)

One definition only: args=["rich/console.py", "--symbol", "is_terminal"].
Paths are relative to /workspace. Python files only; for other files use read_file with start_line and end_line.
Put every argument in one `args` list. Do not use `positional_args` or `short_options`.
