---
name: file-outline
description: List every class and function in a Python file with its line range, so you can read only the part you need. Use before read_file on any file longer than 150 lines.
---

# File outline

Use this when you need code from a Python file longer than 150 lines and do not know the exact lines yet.
A plain read_file only returns the first 150 lines, which are usually imports.

1. Get the outline (one call):
   run_skill_script(skill_name="file-outline", file_path="scripts/outline.py", args=["fastapi/routing.py"])
   Output: one line per definition, for example `L930-976 def Console.is_terminal(self) -> bool`.
2. Read only the range you need:
   read_file(filepath="rich/console.py", start_line=930, end_line=976)

One definition only: args=["rich/console.py", "--symbol", "is_terminal"].
Paths are relative to /workspace. For non-Python files, use read_file with start_line and end_line.
