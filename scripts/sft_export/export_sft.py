#!/usr/bin/env python3
"""Export raw Anthropic task folders to prefix or trajectory SFT JSONL.

References are attached to the behavior they inform, and copied to manifest.json:
[1] Google canonical E4B template (pinned; never invent the control-token format):
    https://huggingface.co/google/gemma-4-E4B-it/blob/ee0ef6023621cff504d758262d4e04895a5af4a2/chat_template.jinja
[2] Gemma reasoning/tool lifecycle:
    https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4
[3] No extra special tokens after rendering a chat template:
    https://huggingface.co/docs/transformers/main/en/chat_templating
[4] SFT labels use -100 for ignored tokens; assistant-only loss needs annotations:
    https://huggingface.co/docs/trl/sft_trainer
[5] Thinking-mode prompt control:
    https://ai.google.dev/gemma/docs/capabilities/thinking
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
MODEL = "google/gemma-4-E4B-it"
REVISION = "ee0ef6023621cff504d758262d4e04895a5af4a2"
REFERENCES = [
    f"https://huggingface.co/{MODEL}/blob/{REVISION}/chat_template.jinja",
    "https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4",
    "https://huggingface.co/docs/transformers/main/en/chat_templating",
    "https://huggingface.co/docs/trl/sft_trainer",
    "https://ai.google.dev/gemma/docs/capabilities/thinking",
]


def text_blocks(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise ValueError("Expected text or text content blocks")
    if any(not isinstance(b, dict) or b.get("type") != "text" for b in value):
        raise ValueError("Unsupported non-text content block")
    return "".join(b["text"] for b in value)


def normalize_tools(tools):
    """[1,4] Captured tools are the actually enabled set, including unused tools."""
    result = []
    for tool in tools:
        if tool.get("type", "custom") != "custom" or "input_schema" not in tool:
            raise ValueError("Unsupported Anthropic tool definition")
        result.append({"type": "function", "function": {
            "name": tool["name"], "description": tool.get("description", ""),
            "parameters": copy.deepcopy(tool["input_schema"])}})
    names = [t["function"]["name"] for t in result]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate enabled tool names")
    return result


def assistant_messages(content, retain_reasoning):
    """[1] Split commentary from calls: the template renders calls before content."""
    if not isinstance(content, list):
        raise ValueError("Expected Anthropic assistant content blocks")
    text, thoughts, calls = [], [], []
    for block in content:
        kind = block.get("type")
        if kind in {"text", "thinking", "redacted_thinking"} and calls:
            raise ValueError("Unsupported assistant text/thinking after tool_use")
        if kind == "text":
            text.append(block["text"])
        elif kind == "thinking":
            if retain_reasoning:
                thoughts.append(block["thinking"])
        elif kind == "redacted_thinking" and not retain_reasoning:
            continue
        elif kind == "tool_use":
            if not isinstance(block.get("input"), dict):
                raise ValueError("Tool arguments must be an object")
            calls.append({"id": block["id"], "type": "function", "function": {
                "name": block["name"], "arguments": copy.deepcopy(block["input"])}})
        else:
            raise ValueError(f"Unsupported assistant block: {kind}")
    messages = []
    if text or thoughts:
        message = {"role": "assistant", "content": "".join(text)}
        if thoughts:
            message["reasoning"] = "".join(thoughts)
        messages.append(message)
    if calls:
        messages.append({"role": "assistant", "content": "", "tool_calls": calls})
    if not messages:
        raise ValueError("Empty assistant response after normalization")
    return messages


def normalize_history(history, retain_reasoning):
    messages, names = [], {}
    for message in history:
        role = message["role"]
        if role == "assistant":
            normalized = assistant_messages(message["content"], retain_reasoning)
            messages.extend(normalized)
            for part in normalized:
                for call in part.get("tool_calls", []):
                    if call["id"] in names:
                        raise ValueError("Duplicate tool call ID")
                    names[call["id"]] = call["function"]["name"]
        elif role == "user":
            content = message["content"]
            if isinstance(content, str):
                messages.append({"role": "user", "content": content})
                continue
            texts = []
            for block in content:
                if block["type"] == "text":
                    texts.append(block["text"])
                elif block["type"] == "tool_result":
                    if texts:
                        raise ValueError("Unsupported user text before tool results")
                    call_id = block["tool_use_id"]
                    if call_id not in names:
                        raise ValueError(f"Orphan tool result: {call_id}")
                    body = text_blocks(block.get("content", ""))
                    if block.get("is_error", False):
                        body = json.dumps({"is_error": True, "content": body}, ensure_ascii=False)
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "name": names[call_id], "content": body})
                else:
                    raise ValueError(f"Unsupported user block: {block['type']}")
            if texts:
                messages.append({"role": "user", "content": "".join(texts)})
        else:
            raise ValueError(f"Unsupported message role: {role}")
    return messages


def public_messages(messages):
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


def validate_conversation(messages, enabled):
    pending, seen = set(), set()
    for message in messages:
        if message["role"] == "assistant":
            if pending:
                raise ValueError("Missing tool result before the next assistant response")
            for call in message.get("tool_calls", []):
                if call["function"]["name"] not in enabled:
                    raise ValueError(f"Called tool is not enabled: {call['function']['name']}")
                if call["id"] in seen:
                    raise ValueError("Duplicate tool call ID")
                seen.add(call["id"])
                pending.add(call["id"])
        elif message["role"] == "tool":
            if message["tool_call_id"] not in pending:
                raise ValueError("Duplicate or orphan tool result")
            pending.remove(message["tool_call_id"])
        elif pending:
            raise ValueError("Missing tool result before user message")
    if pending:
        raise ValueError("Incomplete trajectory: missing final tool result")


SUMMARY_PROMPT = "The following is a conversation history between a user and an AI agent."


def is_summary_call(exchange):
    """ADK context compaction: a tool-less call that summarizes the history so far."""
    request = exchange["request"]
    messages = request.get("messages") or []
    if request.get("tools") or len(messages) != 1 or messages[0].get("role") != "user":
        return False
    content = messages[0]["content"]
    first = content if isinstance(content, str) else (content[0].get("text", "") if content else "")
    return first.startswith(SUMMARY_PROMPT)


def convert_task(exchanges, *, retain_reasoning=False, exclude_turns=None):
    """Split a task at each context compaction; return one example per segment.

    After compaction the agent continues from a replaced history ("For context:"
    summary plus recent turns). Each segment is trained on exactly the prompt the
    model saw. Assistant turns carried into that prompt are context-only (they
    were already supervised in the previous segment); summarizer calls are dropped.
    Turn numbers stay global (1-based over all model calls, as in the trace).
    """
    if not isinstance(exchanges, list) or not exchanges:
        raise ValueError("No exchanges found")
    excluded = set(exclude_turns or [])
    if not excluded <= set(range(1, len(exchanges) + 1)):
        raise ValueError("Excluded turn does not exist in this trajectory")
    segments, current = [], []
    for turn, exchange in enumerate(exchanges, 1):
        if is_summary_call(exchange):
            if current:
                segments.append(current)
            current = []
            continue
        current.append((turn, exchange))
    if current:
        segments.append(current)
    if not segments:
        raise ValueError("No agent exchanges found")

    def tool_results(exchange):
        # Tool results in the post-compaction request, keyed by call id.
        results = []
        for message in exchange["request"].get("messages") or []:
            if message.get("role") == "user" and isinstance(message.get("content"), list):
                for block in message["content"]:
                    if block.get("type") == "tool_result":
                        results.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                                        "content": text_blocks(block.get("content", ""))})
        return results

    rows = []
    for index, seg in enumerate(segments):
        trailing = tool_results(segments[index + 1][0][1]) if index + 1 < len(segments) else None
        if trailing:
            names = {c["id"]: c["function"]["name"] for _, e in seg
                     for m in assistant_messages(e["response"]["content"], retain_reasoning)
                     for c in m.get("tool_calls", [])}
            trailing = [{**r, "name": names[r["tool_call_id"]]} for r in trailing if r["tool_call_id"] in names]
        rows.append(convert_exchanges([e for _, e in seg], retain_reasoning=retain_reasoning, exclude_turns=excluded,
                                      turns=[t for t, _ in seg], context_prefix=index > 0,
                                      trailing_results=trailing))
    return rows


def convert_exchanges(exchanges, *, retain_reasoning=False, exclude_turns=None, turns=None, context_prefix=False,
                      trailing_results=None):
    """Validate each observed prefix rather than concatenate overlapping requests."""
    if not isinstance(exchanges, list) or not exchanges:
        raise ValueError("No exchanges found")
    excluded = set(exclude_turns or [])
    if turns is None:
        turns = list(range(1, len(exchanges) + 1))
        if not excluded <= set(turns):
            raise ValueError("Excluded turn does not exist in this trajectory")
    if len(turns) != len(exchanges):
        raise ValueError("Turn numbers do not match exchanges")
    first = exchanges[0]["request"]
    system = text_blocks(first.get("system", ""))
    tools = normalize_tools(first.get("tools", []))
    messages = [{"role": "system", "content": system}]
    for index, (turn, exchange) in enumerate(zip(turns, exchanges)):
        request, response = exchange["request"], exchange["response"]
        if exchange.get("response_status", 200) != 200:
            raise ValueError(f"Turn {turn}: unsuccessful response")
        if text_blocks(request.get("system", "")) != system or normalize_tools(request.get("tools", [])) != tools:
            raise ValueError(f"Turn {turn}: system/tool context changed; split the trajectory")
        observed = [{"role": "system", "content": system}] + normalize_history(request["messages"], retain_reasoning)
        previous = public_messages(messages)
        if observed[:len(previous)] != previous:
            raise ValueError(f"Turn {turn}: request history changed or was compacted; split the trajectory")
        delta = observed[len(previous):]
        if any(m["role"] == "assistant" for m in delta):
            if not (context_prefix and index == 0):
                raise ValueError(f"Turn {turn}: unobserved assistant response in request history")
            # Carried over from before compaction: context only, never supervised.
            for m in delta:
                if m["role"] == "assistant":
                    m.update(_turn=0, _train=False)
        messages.extend(delta)
        stop = response.get("stop_reason")
        if stop not in {"end_turn", "tool_use"}:
            raise ValueError(f"Turn {turn}: unsupported/incomplete stop_reason {stop!r}")
        parts = assistant_messages(response["content"], retain_reasoning)
        has_calls = any(m.get("tool_calls") for m in parts)
        if has_calls != (stop == "tool_use"):
            raise ValueError(f"Turn {turn}: stop_reason does not match tool calls")
        for part in parts:
            part.update(_turn=turn, _train=turn not in excluded)
        messages.extend(parts)
    if trailing_results:
        # Compaction cut the segment after a tool call; its observation is only in
        # the next (compacted) request. Append it as context so the call is closed.
        pending = {c["id"] for m in messages for c in m.get("tool_calls", [])}
        pending -= {m["tool_call_id"] for m in messages if m["role"] == "tool"}
        messages.extend(r for r in trailing_results if r["tool_call_id"] in pending)
    validate_conversation(messages, {t["function"]["name"] for t in tools})
    # [1] Native serialization has names but no IDs. Order parallel results by
    # their calls, especially when several calls use the same function name.
    for index, message in enumerate(messages):
        calls = message.get("tool_calls", [])
        if calls:
            results = messages[index + 1:index + 1 + len(calls)]
            by_id = {result["tool_call_id"]: result for result in results}
            messages[index + 1:index + 1 + len(calls)] = [by_id[call["id"]] for call in calls]
    return {"messages": messages, "tools": tools}


def clip_text(value, max_chars):
    if not isinstance(value, str) or max_chars <= 0 or len(value) <= max_chars:
        return value
    marker = "…[truncated]"
    return value[:max(0, max_chars - len(marker))] + marker


def head_tail(value, keep):
    """Keep the start and end of a long string and say how much was left out."""
    if len(value) <= keep:
        return value
    head = int(keep * 0.6)
    tail = max(1, keep - head)
    return f"{value[:head]}\n…[{len(value) - head - tail} chars omitted]…\n{value[-tail:]}"


def shorten_tool_result(content, max_chars):
    """Shorten one tool observation and keep it valid JSON when it was JSON.

    Only the longest string fields (file text, stdout, a diff) are cut, from the
    middle, so status fields and the start and end of the output survive. The
    observation is masked in the loss, so this changes cost, not supervision.
    """
    if not isinstance(content, str) or max_chars <= 0 or len(content) <= max_chars:
        return content
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return head_tail(content, max_chars) if max_chars >= 200 else clip_text(content, max_chars)
    def string_slots(node):
        items = node.items() if isinstance(node, dict) else enumerate(node)
        for key, value in items:
            if isinstance(value, str):
                yield node, key
            elif isinstance(value, (dict, list)):
                yield from string_slots(value)

    for _ in range(24):
        text = json.dumps(data, ensure_ascii=False)
        if len(text) <= max_chars:
            return text
        slots = list(string_slots(data))
        if not slots:
            break
        node, key = max(slots, key=lambda slot: len(slot[0][slot[1]]))
        current = node[key]
        if len(current) <= 120:
            break
        excess = len(text) - max_chars
        node[key] = head_tail(current, max(100, len(current) - excess - 80))
    return head_tail(json.dumps(data, ensure_ascii=False), max_chars)


# Observation budgets in characters. Tool results are masked in the loss, so they only need to
# show the model what happened. The block of text that a later edit_file copies as old_string is
# always kept (with a few lines around it), because the model has to see what it must reproduce.
# A read that no later edit uses keeps only a short head.
READ_UNUSED_BUDGET, READ_PIN_CAP = 400, 2500
RUN_BUDGET, RUN_PIN_CAP, STDERR_BUDGET = 600, 1600, 300
DIFF_BUDGET = 300
LINE_CAP = 240
PIN_CONTEXT = 3


def normalize_path(path):
    path = str(path or "")
    return path if path.startswith("/") else "/workspace/" + path


def edit_blocks(messages, start):
    """(path, stripped old_string lines) for every edit_file call after ``start``."""
    blocks = []
    for message in messages[start:]:
        for call in (message.get("tool_calls") or []) if message.get("role") == "assistant" else []:
            function = call.get("function", {})
            if function.get("name") != "edit_file":
                continue
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    continue
            arguments = arguments or {}
            old = [line.strip() for line in str(arguments.get("old_string", "")).split("\n")]
            while old and not old[0]:
                old.pop(0)
            while old and not old[-1]:
                old.pop()
            if old and sum(len(line) for line in old) >= 6:
                blocks.append((normalize_path(arguments.get("filepath")), old, str(arguments.get("old_string", ""))))
    return blocks


def find_block(lines, block, raw=""):
    """(first line, line count) of the block; None when it is absent.

    Tries the exact old_string first (it may start or end in the middle of a line), then the
    stripped lines, then a single long line anywhere inside a line.
    """
    text = "\n".join(lines)
    at = text.find(raw) if raw else -1
    if at >= 0:
        first = text.count("\n", 0, at)
        return first, raw.count("\n") + 1
    stripped = [line.strip() for line in lines]
    width = len(block)
    for i in range(len(stripped) - width + 1):
        if stripped[i:i + width] == block:
            return i, width
    if width == 1 and len(block[0]) >= 12:
        for i, line in enumerate(stripped):
            if block[0] in line:
                return i, 1
    return None


def compact_lines(text, budget, pin_cap, blocks, tail_share, fill_when_pinned=True):
    """Keep each edited block (plus context), then fill what is left of the budget.

    The fill goes to the end of the text for command output (the test summary is there) and to
    the start for file text. Gaps are marked with how many lines were left out.
    """
    lines = text.split("\n")
    pinned = set()
    for block, raw in blocks:
        found = find_block(lines, block, raw)
        if found is None:
            continue
        i, width = found
        window = set(range(max(0, i - PIN_CONTEXT), min(len(lines), i + width + PIN_CONTEXT)))
        if sum(len(lines[j]) + 1 for j in pinned | window) <= pin_cap:
            pinned |= window
    keep = set(pinned)
    room = budget - sum(len(lines[i]) + 1 for i in keep)
    if pinned and not fill_when_pinned:
        room = 0
    if room > 0:
        tail_room = int(room * tail_share)
        for i in range(len(lines) - 1, -1, -1):
            if i in keep:
                continue
            if tail_room - (len(lines[i]) + 1) < 0:
                break
            keep.add(i)
            tail_room -= len(lines[i]) + 1
        head_room = room - int(room * tail_share) + tail_room
        for i in range(len(lines)):
            if i in keep:
                continue
            if head_room - (len(lines[i]) + 1) < 0:
                break
            keep.add(i)
            head_room -= len(lines[i]) + 1
    if len(keep) == len(lines):
        return text, False
    out, gap = [], 0
    for i, line in enumerate(lines):
        if i in keep:
            if gap:
                out.append(f"…[{gap} lines omitted]…")
                gap = 0
            out.append(line if i in pinned or len(line) <= LINE_CAP else line[:LINE_CAP] + "…")
        else:
            gap += 1
    if gap:
        out.append(f"…[{gap} lines omitted]…")
    return "\n".join(out), True


def compact_observation(name, content, blocks):
    try:
        data = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return content
    if not isinstance(data, dict):
        return content
    changed = False
    if name == "read_file" and isinstance(data.get("content"), str):
        path = normalize_path(data.get("filepath")) if data.get("filepath") else None
        mine = [(block, raw) for block_path, block, raw in blocks if path is None or block_path == path]
        data["content"], changed = compact_lines(data["content"], READ_UNUSED_BUDGET, READ_PIN_CAP, mine, 0.0, fill_when_pinned=False)
        if changed:
            data["is_truncated"] = True
    elif name == "run_command":
        mine = [(block, raw) for _path, block, raw in blocks]
        if isinstance(data.get("stdout"), str):
            data["stdout"], changed = compact_lines(data["stdout"], RUN_BUDGET, RUN_PIN_CAP, mine, 0.7)
        if isinstance(data.get("stderr"), str):
            data["stderr"], err_changed = compact_lines(data["stderr"], STDERR_BUDGET, STDERR_BUDGET, [], 0.7)
            changed = changed or err_changed
    elif name == "edit_file" and isinstance(data.get("diff"), str):
        data["diff"], changed = compact_lines(data["diff"], DIFF_BUDGET, DIFF_BUDGET, [], 0.0)
    if not changed:
        return content
    return json.dumps(data, ensure_ascii=False)


def compact_observations_hindsight(messages):
    """Shrink results using later edits to decide what to keep (peeks at the future)."""
    names = {}
    for message in messages:
        for call in (message.get("tool_calls") or []) if message.get("role") == "assistant" else []:
            names[call.get("id")] = call.get("function", {}).get("name")
    out = []
    for index, message in enumerate(messages):
        message = dict(message)
        if message.get("role") == "tool" and isinstance(message.get("content"), str):
            name = names.get(message.get("tool_call_id"))
            if name in {"read_file", "run_command", "edit_file"}:
                message["content"] = compact_observation(name, message["content"], edit_blocks(messages, index + 1))
        out.append(message)
    return out


# Causal policy: the cut depends only on the observation itself, like the harnesses that produced
# public SWE-agent SFT data (mini-SWE-agent keeps the first and last 5000 of outputs over 10000,
# OpenHands the first and last half of 30000, SWE-agent cuts at 100000). Short results are kept
# verbatim; only long ones lose their middle, and the cut says how much was elided.
CAUSAL_LIMITS = {
    # tool: (field, keep_all_up_to, head_share)
    "read_file": ("content", 2500, 0.6),
    "run_command": ("stdout", 1500, 0.3),
    "edit_file": ("diff", 700, 0.7),
}
CAUSAL_STDERR = 600


def elide_middle(text, limit, head_share):
    """First and last lines that fit ``limit`` chars; says how many characters were elided."""
    if len(text) <= limit:
        return text, False
    lines = text.split("\n")
    head_room = int(limit * head_share)
    tail_room = limit - head_room
    head, used = [], 0
    for line in lines:
        if used + len(line) + 1 > head_room:
            break
        head.append(line)
        used += len(line) + 1
    tail, used = [], 0
    for line in reversed(lines[len(head):]):
        if used + len(line) + 1 > tail_room:
            break
        tail.append(line)
        used += len(line) + 1
    tail.reverse()
    kept = sum(len(line) + 1 for line in head + tail)
    if not head and not tail:
        return text[:head_room] + f"…[{len(text) - limit} characters elided]…" + text[-tail_room:], True
    return "\n".join(head + [f"…[{max(0, len(text) - kept)} characters elided]…"] + tail), True


def compact_observation_causal(name, content):
    limits = CAUSAL_LIMITS.get(name)
    if not limits:
        return content
    try:
        data = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return content
    if not isinstance(data, dict):
        return content
    field, limit, head_share = limits
    changed = False
    if isinstance(data.get(field), str):
        data[field], changed = elide_middle(data[field], limit, head_share)
    if name == "run_command" and isinstance(data.get("stderr"), str):
        data["stderr"], err = elide_middle(data["stderr"], CAUSAL_STDERR, 0.3)
        changed = changed or err
    if not changed:
        return content
    if "is_truncated" in data or name in {"read_file", "edit_file"}:
        data["is_truncated"] = True
    return json.dumps(data, ensure_ascii=False)


def compact_observations_causal(messages):
    names = {}
    for message in messages:
        for call in (message.get("tool_calls") or []) if message.get("role") == "assistant" else []:
            names[call.get("id")] = call.get("function", {}).get("name")
    out = []
    for message in messages:
        message = dict(message)
        if message.get("role") == "tool" and isinstance(message.get("content"), str):
            message["content"] = compact_observation_causal(names.get(message.get("tool_call_id")), message["content"])
        out.append(message)
    return out


def compact_observations(messages, policy="causal"):
    """Shrink read_file, run_command and edit_file results; other messages are untouched."""
    if policy == "none":
        return messages
    if policy == "causal":
        return compact_observations_causal(messages)
    if policy == "hindsight":
        return compact_observations_hindsight(messages)
    raise ValueError(f"Unknown observation policy: {policy}")


EDIT_TOOLS = {"edit_file", "write_file"}


def tool_failed(content):
    try:
        data = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and (data.get("status") == "error" or data.get("is_error") is True)


def mask_unsound_steps(messages):
    """Mark whole model responses as context-only when they should not be imitated.

    A response is masked when one of its tool calls came back as an error, or when
    it repeats an identical call although nothing was edited since the last time.
    The steps stay in the history, so later steps can still learn to recover.
    """
    messages = [dict(message) for message in messages]
    seen, bad_turns = set(), set()
    for index, message in enumerate(messages):
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not calls:
            continue
        results = messages[index + 1:index + 1 + len(calls)]
        signature = {json.dumps(call["function"], sort_keys=True) for call in calls}
        failed = any(result.get("role") == "tool" and tool_failed(result.get("content")) for result in results)
        repeated = bool(signature) and signature <= seen
        if message.get("_turn") is not None and (failed or repeated):
            bad_turns.add(message["_turn"])
        if any(call["function"]["name"] in EDIT_TOOLS for call in calls):
            seen = set()
        else:
            seen |= signature
    for message in messages:
        if message.get("role") == "assistant" and message.get("_turn") in bad_turns:
            message["_train"] = False
    return messages


def clip_message(message, max_chars, *, pin=False, train=None):
    """Copy a public message. Only tool observations are clipped.

    The system prompt, the task, and assistant actions stay intact. Assistant
    messages record ``train`` so a trajectory row can mask context-only turns.
    """
    source_train = message.get("_train", True)
    message = {key: copy.deepcopy(value) for key, value in message.items() if not str(key).startswith("_")}
    if message.get("role") == "assistant":
        message["train"] = source_train if train is None else train
    if not pin and message.get("role") == "tool" and isinstance(message.get("content"), str):
        message["content"] = shorten_tool_result(message["content"], max_chars)
    return message


def message_groups(messages):
    """Keep each tool call with the results that close it."""
    groups, index = [], 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            count = len(message["tool_calls"])
            groups.append(messages[index:index + 1 + count])
            index += 1 + count
        else:
            groups.append([message])
            index += 1
    return groups


def truncate_messages(messages, *, max_messages, max_chars, train=None):
    """Keep the system prompt, the first user turn, and the newest fitting groups.

    Tool-call groups are never split. Tool observations are clipped to max_chars.
    The system prompt and the first user turn are never clipped.
    """
    if max_messages <= 0 or max_chars <= 0:
        raise ValueError("max_messages and max_chars must be positive")
    groups = message_groups(messages)
    pinned_groups = []
    if groups and groups[0][0].get("role") == "system":
        pinned_groups.append(groups.pop(0))
    user_at = next((i for i, group in enumerate(groups) if any(m.get("role") == "user" for m in group)), None)
    if user_at is not None:
        pinned_groups.append(groups.pop(user_at))
    kept = list(groups)
    while pinned_groups and sum(len(group) for group in pinned_groups + kept) > max_messages and kept:
        kept.pop(0)
    while sum(len(group) for group in pinned_groups + kept) > max_messages and len(pinned_groups) > 1:
        pinned_groups.pop()
    pinned = {id(message) for group in pinned_groups for message in group}
    selected = [message for group in pinned_groups + kept for message in group]
    if not selected:
        raise ValueError("Truncation removed every message")
    return [clip_message(message, max_chars, pin=id(message) in pinned, train=train) for message in selected]


def trainable_assistant_indexes(messages):
    return [index for index, message in enumerate(messages)
            if message.get("role") == "assistant" and message.get("_train", True)]


def response_groups(messages, indexes):
    """Group supervised assistant indexes that belong to one model response.

    A response with commentary and tool calls is stored as two adjacent assistant
    messages with the same turn. Gemma renders them as one model turn, so they
    must be supervised together, or the commentary would end with a stop token.
    """
    groups = []
    for index in indexes:
        turn = messages[index].get("_turn")
        previous = groups[-1][-1] if groups else None
        if (previous is not None and previous == index - 1 and turn is not None
                and messages[previous].get("_turn") == turn):
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups


def build_examples(row, *, mode, task_id, segment, enable_thinking=True, max_messages=18, max_chars=4000,
                   mask_bad_steps=True, compact=True, observation_policy="causal"):
    """Cut one trajectory row, or one prefix row per supervised model response.

    Teacher reasoning is whatever convert_task already kept. Thinking mode is
    recorded for the trainer; this function does not render or tokenize.
    Failed and repeated steps stay in the history but are not supervised.
    """
    if mode not in {"prefix", "trajectory"}:
        raise ValueError(f"Unknown mode: {mode}")
    messages = mask_unsound_steps(row["messages"]) if mask_bad_steps else row["messages"]
    if compact:
        messages = compact_observations(messages, policy=observation_policy)
    tools = row["tools"]
    if mode == "trajectory":
        trained = [index for index in trainable_assistant_indexes(messages)]
        if not trained:
            return []
        body = truncate_messages(messages, max_messages=max_messages, max_chars=max_chars, train=None)
        if not any(message.get("role") == "assistant" for message in body):
            raise ValueError("Trajectory truncation dropped every assistant turn")
        return [{"task_id": task_id, "segment": segment, "mode": mode, "enable_thinking": enable_thinking,
                 "tools": tools, "messages": body}]
    examples = []
    for group in response_groups(messages, trainable_assistant_indexes(messages)):
        index = group[0]
        context = truncate_messages(messages[:index], max_messages=max_messages, max_chars=max_chars, train=False)
        steps = [clip_message(messages[i], max_chars, train=True) for i in group]
        completion = steps[0] if len(steps) == 1 else steps
        examples.append({"task_id": task_id, "segment": segment, "mode": mode, "turn": messages[index].get("_turn"),
                         "enable_thinking": enable_thinking, "tools": tools,
                         "messages": context, "completion": completion})
    return examples


def export_conversations(args, paths):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".sft-export-", dir=args.output.parent))
    written = rejected = filtered_unresolved = 0
    try:
        with (staging / "train.jsonl").open("w") as train:
            reject_file = (staging / "rejected.jsonl").open("w") if args.skip_invalid else None
            try:
                for path in paths:
                    raw = path.read_bytes()
                    try:
                        if args.resolved_only:
                            result_path = path.parent / "results" / f"{path.parent.name}.json"
                            result = json.loads(result_path.read_bytes())
                            if result.get("resolved") is False:
                                filtered_unresolved += 1
                                continue
                            if result.get("resolved") is not True:
                                raise ValueError(f"Missing boolean resolved status in {result_path}")
                        rows = convert_task(json.loads(raw), retain_reasoning=args.retain_reasoning,
                                            exclude_turns=args.exclude_turn)
                    except (ValueError, KeyError, TypeError, OSError) as error:
                        if not args.skip_invalid:
                            raise ValueError(f"{path}: {error}") from error
                        rejected += 1
                        reject_file.write(json.dumps({
                            "task_id": path.parent.name,
                            "source": str(path),
                            "source_commit": args.source_commit,
                            "source_sha256": hashlib.sha256(raw).hexdigest(),
                            "error": str(error),
                        }, ensure_ascii=False) + "\n")
                        continue
                    for segment, row in enumerate(rows, 1):
                        written += 1
                        exported = row if args.minimal else {
                            "task_id": path.parent.name,
                            "segment": segment,
                            "source": str(path),
                            "source_commit": args.source_commit,
                            "source_sha256": hashlib.sha256(raw).hexdigest(),
                            "template_model": MODEL,
                            "template_revision": REVISION,
                            "template_kwargs": {
                                "enable_thinking": args.enable_thinking,
                                "preserve_thinking": any(
                                    message.get("reasoning") for message in row["messages"]
                                ),
                            },
                            **row,
                        }
                        train.write(json.dumps(exported, ensure_ascii=False) + "\n")
            finally:
                if reject_file is not None:
                    reject_file.close()
        if written == 0:
            raise ValueError("No valid examples found")
        staging.rename(args.output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(f"Exported {written} conversation example(s) to {args.output}; "
          f"rejected: {rejected}; filtered unresolved: {filtered_unresolved}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="+", type=Path,
                        help="One or more task folders, ancestors, or anthropic_exchanges.json files")
    parser.add_argument("--output", required=True, type=Path, help="New output directory (never overwritten)")
    parser.add_argument("--mode", choices=["prefix", "trajectory"],
                        help="prefix: one row per assistant message; trajectory: one row per segment")
    parser.add_argument("--retain-reasoning", action=argparse.BooleanOptionalAction, default=False,
                        help="Keep teacher thinking in the messages (default: drop)")
    parser.add_argument("--enable-thinking", action=argparse.BooleanOptionalAction, default=True,
                        help="Record that training should render Gemma thinking mode (default: on)")
    parser.add_argument("--exclude-turn", type=int, action="append", default=[],
                        help="1-based model call to drop as a prefix target; repeatable")
    parser.add_argument("--max-messages", type=int, default=18,
                        help="Keep the system prompt, first user turn, and the newest messages that fit")
    parser.add_argument("--max-chars", type=int, default=4000,
                        help="Clip tool observations to this many characters; the task text is kept")
    parser.add_argument("--conversations-only", action="store_true",
                        help="Write normalized messages/tools JSONL, one row per segment, instead of --mode rows")
    parser.add_argument("--skip-invalid", action="store_true",
                        help="With --conversations-only, audit invalid trajectories instead of aborting")
    parser.add_argument("--source-commit",
                        help="Git commit containing the inputs (recorded in conversations-only rows)")
    parser.add_argument("--minimal", action="store_true",
                        help="With --conversations-only, write only messages and tools")
    parser.add_argument("--resolved-only", action="store_true",
                        help="With --conversations-only, keep only attempts whose result has resolved=true")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f"Output already exists: {args.output}")
    if args.max_messages <= 0 or args.max_chars <= 0:
        parser.error("--max-messages and --max-chars must be positive")
    if args.conversations_only == (args.mode is not None):
        parser.error("pass exactly one of --mode or --conversations-only")
    if args.skip_invalid and not args.conversations_only:
        parser.error("--skip-invalid requires --conversations-only")
    if args.source_commit and not args.conversations_only:
        parser.error("--source-commit requires --conversations-only")
    if (args.minimal or args.resolved_only) and not args.conversations_only:
        parser.error("--minimal and --resolved-only require --conversations-only")
    if args.minimal and args.source_commit:
        parser.error("--minimal cannot be combined with --source-commit")
    discovered = []
    for input_path in args.input:
        if input_path.is_file() and input_path.name == "anthropic_exchanges.json":
            discovered.append(input_path)
        elif input_path.is_dir():
            discovered.extend(input_path.rglob("anthropic_exchanges.json"))
    paths = sorted({path.resolve() for path in discovered})
    if not paths:
        parser.error("No anthropic_exchanges.json files found; other provider formats are not supported")
    if args.conversations_only:
        export_conversations(args, paths)
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".sft-export-", dir=args.output.parent))
    audit = []
    try:
        with (staging / "train.jsonl").open("w") as train:
            number = 0
            for path in paths:
                try:
                    raw = path.read_bytes()
                    rows = convert_task(json.loads(raw), retain_reasoning=args.retain_reasoning,
                                        exclude_turns=args.exclude_turn)
                    examples = []
                    for segment, row in enumerate(rows, 1):
                        examples.extend(build_examples(
                            row, mode=args.mode, task_id=path.parent.name, segment=segment,
                            enable_thinking=args.enable_thinking, max_messages=args.max_messages,
                            max_chars=args.max_chars))
                except (ValueError, KeyError, TypeError) as error:
                    raise ValueError(f"{path}: {error}") from error
                for example in examples:
                    number += 1
                    train.write(json.dumps(example, ensure_ascii=False) + "\n")
                    audit.append({"row": number, "task_id": example["task_id"], "segment": example["segment"],
                                  "mode": args.mode, "source": str(path.resolve()),
                                  "source_sha256": hashlib.sha256(raw).hexdigest(),
                                  "messages": len(example["messages"])})
        manifest = {"schema_version": "gemma4-sft-v4", "mode": args.mode, "examples": len(audit),
                    "retain_reasoning": args.retain_reasoning, "enable_thinking": args.enable_thinking,
                    "excluded_turns": args.exclude_turn, "max_messages": args.max_messages,
                    "max_chars": args.max_chars, "references": REFERENCES, "rows": audit}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        staging.rename(args.output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(f"Exported {len(audit)} {args.mode} example(s) to {args.output}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
