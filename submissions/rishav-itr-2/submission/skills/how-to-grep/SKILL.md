---
name: how-to-grep
description: Use when locating relevant source files or symbols with grep in a repository.
---

# Targeted code search with grep

Use grep to find likely implementation files with a small number of focused searches.

1. Read the task and extract up to three distinctive identifiers or behavior terms. Include plausible naming variants when useful.
2. Infer the likely package or subsystem from the repository layout. Search there first; search the repository root only when the location is unclear.
3. Combine related alternatives in one recursive search:

   ```bash
   grep -RInE -I 'term_one|term_two|name_variant' likely/source/area/
   ```

   Replace the placeholders with terms from the current task. Keep them specific; generic alternatives create noisy results. Single quotes protect the pattern from shell expansion. Escape regular-expression characters when searching for them literally.
4. Review the matching paths and lines. When a likely implementation appears, read that file near the reported line before searching again. If results are broad, narrow the directory or add one distinctive term.
5. If there are no matches, check the pattern and path, then broaden once to a related directory or a meaningful naming variant.
6. After reading the implementation, search for callers or tests only when needed to understand the behavior.

## Examples

For a parser that may have been renamed:

```bash
grep -RInE -I 'parse_record|decode_record|RecordInput' src/package/
```

For configuration validation handled across multiple layers:

```bash
grep -RInE -I 'ConfigError|validate_config|config.*raise' src/package/
```

## Result handling

- Exit status 0 means grep found a match.
- Exit status 1 means grep ran but found no matches. Reconsider the terms or path; do not treat it as a broken command.
- Other nonzero statuses usually indicate a command, path, or pattern error. Fix the error before broadening the search.

## Stop rules

- Do not repeat near-identical searches after a relevant candidate appears.
- Do not reread the same file without a specific question or line range.
- Treat matches as candidates and confirm that the code implements the task's behavior.
- Generate all search terms from the current task; do not reuse memorized task-specific queries.
