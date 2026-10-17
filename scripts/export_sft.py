#!/usr/bin/env python3
"""Export raw Anthropic task folders to masked, tokenized Gemma 4 SFT JSONL.

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
import uuid

MODEL = "google/gemma-4-E4B-it"
REVISION = "ee0ef6023621cff504d758262d4e04895a5af4a2"
TEMPLATE_SHA256 = "0a2c8073c878ab1da004bee933a998606537bbb62016310352c7285c3f01c5b5"
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


def annotated_template(source):
    """[1,4] Instrument known output sites; leave environment observations unlabelled.

    Markers give exact character spans even inside Jinja macros/captured blocks.
    Removing them MUST reproduce upstream output byte-for-byte (checked per row).
    """
    if hashlib.sha256(source.encode()).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("Unsupported chat template revision; use the documented pinned E4B tokenizer")
    train = "message.get('_train', true)"

    def replace(old, new, count=1):
        nonlocal source
        if source.count(old) != count:
            raise ValueError("Upstream chat template anchor changed")
        source = source.replace(old, new)

    def emit(expr, condition):
        return "{{- sft_emit(" + expr + ", " + condition + ") -}}"

    replace("format_tool_response_block(tool_name, response)",
            "format_tool_response_block(tool_name, response, supervise=false)")
    marker = "{{- '<|tool_response>' -}}"
    # Only the first tool-response opener is a model-generated stop token. [2]
    source = source.replace(marker, emit("'<|tool_response>'", "supervise"), 1)
    replace(marker, emit("'<|tool_response>'", train))
    calls = [
        "tool_response['name'] | default('unknown', true), tool_response['response']",
        "ns_tname.name, tool_body", "ns_tname.name, ns_txt.s",
    ]
    for args in calls:
        old = "format_tool_response_block(" + args + ")"
        replace(old, "format_tool_response_block(" + args + ", supervise=" + train + " and not ns_tr_out.flag)",
                count=2 if args == "ns_tname.name, tool_body" else 1)
    expression = "'<|channel>thought\\n' + thinking_text + '\\n<channel|>'"
    replace("{{- " + expression + " -}}", emit(expression, train))
    start = "{{- '<|tool_call>call:' + function['name'] + '{' -}}"
    end = "{{- '}<tool_call|>' -}}"
    replace(start, "{%- if " + train + " -%}{{- sft_start -}}{%- endif -%}" + start)
    replace(end, end + "{%- if " + train + " -%}{{- sft_end -}}{%- endif -%}")
    replace("{{- captured_content -}}", emit("captured_content", "role == 'model' and " + train))
    # The earlier identical end-turn expression belongs to the system prompt.
    marker = "{{- '<turn|>\\n' -}}"
    before, sep, after = source.rpartition(marker)
    if not sep:
        raise ValueError("Missing model end-turn marker")
    source = before + emit("'<turn|>\\n'", "role == 'model' and " + train) + after
    macro = ("{%- macro sft_emit(value, supervise) -%}"
             "{%- if supervise and value -%}{{- sft_start -}}{{- value -}}{{- sft_end -}}"
             "{%- else -%}{{- value -}}{%- endif -%}{%- endmacro -%}")
    return macro + source


def remove_markers(marked, start, end):
    parts, spans, position, active = [], [], 0, None
    while marked:
        a, b = marked.find(start), marked.find(end)
        if a == b == -1:
            parts.append(marked)
            break
        is_start = a != -1 and (b == -1 or a < b)
        index, marker = (a, start) if is_start else (b, end)
        parts.append(marked[:index])
        position += index
        if is_start:
            if active is not None:
                raise ValueError("Nested supervision markers")
            active = position
        else:
            if active is None:
                raise ValueError("Unbalanced supervision markers")
            spans.append((active, position))
            active = None
        marked = marked[index + len(marker):]
    if active is not None:
        raise ValueError("Unclosed supervision marker")
    return "".join(parts), spans


def encode_example(example, tokenizer, *, enable_thinking=False, max_length=32768):
    """[3,4] Tokenize once, mask non-generated tokens, never silently truncate."""
    if not tokenizer.is_fast:
        raise ValueError("A fast tokenizer with character offsets is required")
    # [1,2] The inference template strips non-tool reasoning before the last
    # actual user turn. Fail explicitly rather than silently lose SFT targets.
    last_user = max((i for i, m in enumerate(example["messages"]) if m["role"] == "user"), default=-1)
    if any(m.get("reasoning") and not m.get("tool_calls")
           for m in example["messages"][:max(last_user, 0)]):
        raise ValueError("Retained thinking before a later user turn would be stripped; split this trajectory")
    source = tokenizer.get_chat_template()
    annotated = annotated_template(source)
    common = dict(tools=example["tools"], tokenize=False, add_generation_prompt=False,
                  enable_thinking=enable_thinking,
                  preserve_thinking=any(m.get("reasoning") for m in example["messages"]))
    original = tokenizer.apply_chat_template(example["messages"], **common)
    salt = uuid.uuid4().hex
    start, end = f"\ue000SFT_START_{salt}\ue001", f"\ue000SFT_END_{salt}\ue001"
    marked = tokenizer.apply_chat_template(example["messages"], chat_template=annotated,
                                           sft_start=start, sft_end=end, **common)
    text, spans = remove_markers(marked, start, end)
    if text != original:
        raise ValueError("Annotated rendering differs from the official template")
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, truncation=False)
    ids, offsets = encoded["input_ids"], encoded["offset_mapping"]
    if len(ids) > max_length:
        raise ValueError(f"Sequence has {len(ids)} tokens, exceeds max_length={max_length}; no truncation applied")
    # A token crossing a masked boundary stays masked. Merge adjacent spans first.
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    labels, index = [], 0
    for token, (a, b) in zip(ids, offsets):
        while index < len(merged) and merged[index][1] <= a:
            index += 1
        keep = index < len(merged) and b > a and merged[index][0] <= a and b <= merged[index][1]
        labels.append(token if keep else -100)
    if not any(label != -100 for label in labels):
        raise ValueError("No supervised tokens remain")
    return {"input_ids": ids, "attention_mask": encoded["attention_mask"], "labels": labels, "text": text}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="Task folder, ancestor folder, or anthropic_exchanges.json")
    parser.add_argument("--output", required=True, type=Path, help="New output directory (never overwritten)")
    parser.add_argument("--tokenizer", default=MODEL, help="Official tokenizer ID or local tokenizer directory")
    parser.add_argument("--revision", default=REVISION, help="Pinned Hugging Face revision")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--retain-reasoning", action=argparse.BooleanOptionalAction, default=None,
                        help="Keep available teacher reasoning (default: drop); independent of thinking mode")
    parser.add_argument("--enable-thinking", action=argparse.BooleanOptionalAction, default=None,
                        help="Insert Gemma's thinking-mode system token (default: off); match serving settings")
    parser.add_argument("--thinking", action="store_true",
                        help="Deprecated shorthand for --retain-reasoning --enable-thinking")
    parser.add_argument("--exclude-turn", type=int, action="append", default=[],
                        help="1-based model call to mask, retaining its context; repeatable, applies to every task")
    parser.add_argument("--max-length", type=int, default=32768)
    args = parser.parse_args(argv)
    if args.thinking and (args.retain_reasoning is not None or args.enable_thinking is not None):
        parser.error("cannot combine legacy --thinking with --[no-]retain-reasoning or --[no-]enable-thinking")
    retain_reasoning = args.thinking if args.retain_reasoning is None else args.retain_reasoning
    enable_thinking = args.thinking if args.enable_thinking is None else args.enable_thinking
    if args.thinking:
        print("warning: --thinking is deprecated; use --retain-reasoning --enable-thinking", file=sys.stderr)
    if args.output.exists():
        parser.error(f"Output already exists: {args.output}")
    if args.max_length <= 0:
        parser.error("--max-length must be positive")
    paths = ([args.input] if args.input.is_file() and args.input.name == "anthropic_exchanges.json"
             else sorted(args.input.rglob("anthropic_exchanges.json")) if args.input.is_dir() else [])
    if not paths:
        parser.error("No anthropic_exchanges.json files found; other provider formats are not supported")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, revision=args.revision,
                                              local_files_only=args.local_files_only)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".sft-export-", dir=args.output.parent))
    audit = []
    try:
        with (staging / "train.jsonl").open("w") as train, (staging / "conversations.jsonl").open("w") as conv:
            (staging / "text").mkdir()
            number = 0
            for path in paths:
                try:
                    raw = path.read_bytes()
                    rows = convert_task(json.loads(raw), retain_reasoning=retain_reasoning, exclude_turns=args.exclude_turn)
                    encoded_rows = [encode_example(row, tokenizer, enable_thinking=enable_thinking, max_length=args.max_length)
                                    for row in rows]
                except (ValueError, KeyError, TypeError) as error:
                    raise ValueError(f"{path}: {error}") from error
                for segment, (row, encoded) in enumerate(zip(rows, encoded_rows), 1):
                    number += 1
                    text = encoded.pop("text")
                    text_path = f"text/{number:06d}_{path.parent.name}_seg{segment}.txt"
                    (staging / text_path).write_text(text)
                    train.write(json.dumps(encoded, ensure_ascii=False) + "\n")
                    # Metadata remains in the review file; train.jsonl is directly collatable.
                    conv.write(json.dumps({"task_id": path.parent.name, "segment": segment, **row},
                                          ensure_ascii=False) + "\n")
                    audit.append({"row": number, "task_id": path.parent.name, "segment": segment,
                                  "segments": len(rows), "source": str(path.resolve()),
                                  "source_sha256": hashlib.sha256(raw).hexdigest(), "text": text_path,
                                  "tokens": len(encoded["input_ids"]),
                                  "supervised_tokens": sum(x != -100 for x in encoded["labels"])})
        manifest = {"schema_version": "gemma4-sft-v2", "examples": len(audit),
                    "tokenizer": args.tokenizer, "requested_revision": args.revision,
                    "chat_template_sha256": TEMPLATE_SHA256,
                    "tokenizer_backend_sha256": hashlib.sha256(tokenizer.backend_tokenizer.to_str().encode()).hexdigest(),
                    "retain_reasoning": retain_reasoning, "enable_thinking": enable_thinking,
                    "excluded_turns": args.exclude_turn,
                    "max_length": args.max_length, "references": REFERENCES, "rows": audit}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        staging.rename(args.output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(f"Exported {len(audit)} example(s) to {args.output}; token counts: {[r['tokens'] for r in audit]}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
