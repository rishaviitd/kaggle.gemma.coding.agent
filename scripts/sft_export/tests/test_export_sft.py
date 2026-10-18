"""Behavior tests for raw-trace conversion; optional real-tokenizer integration tests."""

import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    exporter = importlib.import_module("export_sft")
except ModuleNotFoundError:
    exporter = None


def exchanges():
    tool = {"name": "run_command", "description": "Execute code", "input_schema": {
        "type": "object", "properties": {"command": {"type": "string"}},
        "required": ["command"]}}
    user = {"role": "user", "content": [{"type": "text", "text": "Fix the bug 🐛"}]}
    first = [{"type": "thinking", "thinking": "Inspect first", "signature": "not-training-data"},
             {"type": "text", "text": "I will inspect it."},
             {"type": "tool_use", "id": "c1", "name": "run_command", "input": {"command": "ls"}}]
    result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1",
                                           "content": '{"stdout":"OBSERVATION_ONLY"}'}]}
    base = {"system": [{"type": "text", "text": "SYSTEM_ONLY"}], "tools": [tool]}
    return [
        {"request": {**base, "messages": [user]}, "response_status": 200,
         "response": {"role": "assistant", "content": first, "stop_reason": "tool_use"}},
        {"request": {**base, "messages": [user, {"role": "assistant", "content": first}, result]},
         "response_status": 200,
         "response": {"role": "assistant", "content": [{"type": "text", "text": "Fixed."}],
                      "stop_reason": "end_turn"}},
    ]


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(exporter, "The SFT exporter has not been implemented")

    def test_reconstructs_one_prefix_and_preserves_pretool_commentary(self):
        row = exporter.convert_exchanges(exchanges())
        self.assertEqual([m["role"] for m in row["messages"]],
                         ["system", "user", "assistant", "assistant", "tool", "assistant"])
        self.assertEqual(row["messages"][0]["content"], "SYSTEM_ONLY")
        self.assertEqual(row["messages"][1]["content"], "Fix the bug 🐛")
        self.assertEqual(row["messages"][2]["content"], "I will inspect it.")
        self.assertEqual(row["messages"][3]["tool_calls"][0]["function"]["arguments"], {"command": "ls"})
        self.assertEqual(row["messages"][4]["tool_call_id"], "c1")
        self.assertEqual(len(row["tools"]), 1)
        self.assertEqual(row["tools"][0]["function"]["parameters"]["required"], ["command"])
        self.assertNotIn("Inspect first", json.dumps(row))
        self.assertNotIn("not-training-data", json.dumps(row))

    def test_opt_in_thinking_drops_provider_signature(self):
        row = exporter.convert_exchanges(exchanges(), retain_reasoning=True)
        self.assertEqual(row["messages"][2]["reasoning"], "Inspect first")
        self.assertNotIn("signature", json.dumps(row))

    def test_rejects_changed_system_tools_or_history(self):
        for field in ["system", "tools", "messages"]:
            data = copy.deepcopy(exchanges())
            data[1]["request"][field] = []
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "context|history"):
                exporter.convert_exchanges(data)

    def test_rejects_truncation_unknown_tool_or_missing_result(self):
        data = exchanges()
        data[1]["response"]["stop_reason"] = "max_tokens"
        with self.assertRaisesRegex(ValueError, "stop_reason"):
            exporter.convert_exchanges(data)
        data = exchanges()
        data[0]["response"]["content"][-1]["name"] = "disabled_tool"
        with self.assertRaisesRegex(ValueError, "enabled"):
            exporter.convert_exchanges(data)
        data = exchanges()[:1]
        with self.assertRaisesRegex(ValueError, "tool result|incomplete"):
            exporter.convert_exchanges(data)

    def test_rejects_orphan_result_and_nontext_blocks(self):
        data = exchanges()
        data[1]["request"]["messages"][-1]["content"][0]["tool_use_id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "tool|history"):
            exporter.convert_exchanges(data)
        data = exchanges()
        data[0]["request"]["messages"][0]["content"] = [{"type": "image", "source": {}}]
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            exporter.convert_exchanges(data)

    def test_compaction_splits_into_segments_with_context_only_carryover(self):
        data = exchanges()
        summary = {"request": {"messages": [{"role": "user", "content": [{"type": "text", "text":
                   exporter.SUMMARY_PROMPT + " Please summarize..."}]}]}, "response_status": 200,
                   "response": {"role": "assistant", "content": [{"type": "text", "text": "SUMMARY"}],
                                "stop_reason": "end_turn"}}
        carried = copy.deepcopy(data[1]["request"]["messages"][1:])
        after = {"request": {**copy.deepcopy(data[1]["request"]), "messages": [
                     {"role": "user", "content": [{"type": "text", "text": "For context: SUMMARY"}]}] + carried},
                 "response_status": 200,
                 "response": {"role": "assistant", "content": [{"type": "text", "text": "Resumed."}],
                              "stop_reason": "end_turn"}}
        rows = exporter.convert_task(data + [summary, after])
        self.assertEqual(len(rows), 2)
        self.assertNotIn("Please summarize", json.dumps(rows))
        second = rows[1]["messages"]
        self.assertEqual(second[1]["content"], "For context: SUMMARY")
        trained = [m for m in second if m["role"] == "assistant" and m["_train"]]
        self.assertEqual([m["content"] for m in trained], ["Resumed."])
        self.assertEqual(trained[0]["_turn"], 4)
        with self.assertRaisesRegex(ValueError, "unobserved assistant"):
            exporter.convert_exchanges([after])

    def test_parallel_calls_match_by_id_even_with_reordered_results(self):
        data = exchanges()
        second = {"type": "tool_use", "id": "c2", "name": "run_command", "input": {"command": "pwd"}}
        data[0]["response"]["content"].append(second)
        data[1]["request"]["messages"][1]["content"] = copy.deepcopy(data[0]["response"]["content"])
        data[1]["request"]["messages"][-1]["content"].insert(
            0, {"type": "tool_result", "tool_use_id": "c2", "content": "second"})
        row = exporter.convert_exchanges(data)
        results = [m for m in row["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in results], ["c1", "c2"])
        self.assertEqual([m["name"] for m in results], ["run_command", "run_command"])
        self.assertIn("OBSERVATION_ONLY", results[0]["content"])
        self.assertEqual(results[1]["content"], "second")

    def test_preserves_tool_failure_status_in_the_observation(self):
        data = exchanges()
        data[1]["request"]["messages"][-1]["content"][0]["is_error"] = True
        row = exporter.convert_exchanges(data)
        result = next(m for m in row["messages"] if m["role"] == "tool")
        observation = json.loads(result["content"])
        self.assertIn("is_error", observation)
        self.assertTrue(observation["is_error"])
        self.assertIn("OBSERVATION_ONLY", observation["content"])

    def test_conversations_only_cli_writes_unsloth_json_and_rejection_audit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            valid = root / "input" / "valid" / "anthropic_exchanges.json"
            invalid = root / "input" / "invalid" / "anthropic_exchanges.json"
            valid.parent.mkdir(parents=True)
            invalid.parent.mkdir(parents=True)
            valid.write_text(json.dumps(exchanges()))
            truncated = exchanges()
            truncated[-1]["response"]["stop_reason"] = "max_tokens"
            invalid.write_text(json.dumps(truncated))

            output = root / "out"
            proc = subprocess.run([
                sys.executable, str(Path(exporter.__file__)), str(valid.parent), str(invalid.parent),
                "--output", str(output), "--conversations-only", "--skip-invalid",
                "--retain-reasoning", "--enable-thinking", "--source-commit", "deadbeef",
            ], capture_output=True, text=True)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual({p.name for p in output.iterdir()}, {"train.jsonl", "rejected.jsonl"})
            row = json.loads((output / "train.jsonl").read_text())
            self.assertEqual(row["task_id"], "valid")
            self.assertIn("messages", row)
            self.assertIn("tools", row)
            self.assertNotIn("input_ids", row)
            self.assertNotIn("text", row)
            self.assertEqual(row["source_commit"], "deadbeef")
            self.assertTrue(row["template_kwargs"]["enable_thinking"])
            self.assertTrue(row["template_kwargs"]["preserve_thinking"])
            self.assertEqual(row["messages"][2]["reasoning"], "Inspect first")
            self.assertTrue(row["messages"][2]["_train"])
            rejected = json.loads((output / "rejected.jsonl").read_text())
            self.assertEqual(rejected["task_id"], "invalid")
            self.assertEqual(rejected["source_commit"], "deadbeef")
            self.assertIn("max_tokens", rejected["error"])

    def test_minimal_resolved_only_export_contains_only_passed_conversations(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inputs = []
            for name, resolved in (("passed", True), ("failed", False)):
                task = root / "input" / name
                task.mkdir(parents=True)
                (task / "anthropic_exchanges.json").write_text(json.dumps(exchanges()))
                results = task / "results"
                results.mkdir()
                (results / f"{name}.json").write_text(json.dumps({"resolved": resolved}))
                inputs.append(task)

            output = root / "out"
            proc = subprocess.run([
                sys.executable, str(Path(exporter.__file__)), *map(str, inputs),
                "--output", str(output), "--conversations-only", "--minimal",
                "--resolved-only", "--retain-reasoning", "--enable-thinking",
            ], capture_output=True, text=True)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual({p.name for p in output.iterdir()}, {"train.jsonl"})
            rows = [json.loads(line) for line in (output / "train.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(set(rows[0]), {"messages", "tools"})
            self.assertEqual(rows[0]["messages"][2]["reasoning"], "Inspect first")
            self.assertIn("filtered unresolved: 1", proc.stdout)


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(exporter, "The SFT exporter has not been implemented")
        self.row = exporter.convert_exchanges(exchanges())

    def test_prefix_is_one_row_per_model_response_without_teacher_thoughts(self):
        rows = exporter.build_examples(self.row, mode="prefix", task_id="task", segment=1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["messages"][-1]["role"], "user")
        first = rows[0]["completion"]
        self.assertIsInstance(first, list)
        self.assertEqual([step["role"] for step in first], ["assistant", "assistant"])
        self.assertEqual(first[0]["content"], "I will inspect it.")
        self.assertEqual(first[1]["tool_calls"][0]["function"]["name"], "run_command")
        self.assertIn("OBSERVATION_ONLY", rows[1]["messages"][-1]["content"])
        self.assertEqual(rows[1]["completion"]["content"], "Fixed.")
        steps = first + [rows[1]["completion"]]
        self.assertTrue(all(row["enable_thinking"] for row in rows))
        self.assertTrue(all(step["train"] for step in steps))
        self.assertTrue(all(message.get("train") is False
                            for row in rows for message in row["messages"] if message["role"] == "assistant"))
        self.assertNotIn("Inspect first", json.dumps(rows))
        self.assertNotIn("_train", json.dumps(rows))

    def test_commentary_and_call_are_never_split_into_two_rows(self):
        rows = exporter.build_examples(self.row, mode="prefix", task_id="task", segment=1)
        text_only = [row for row in rows if not isinstance(row["completion"], list)
                     and not row["completion"].get("tool_calls")]
        self.assertEqual([row["completion"]["content"] for row in text_only], ["Fixed."])

    def test_failed_and_repeated_steps_stay_in_context_but_are_not_supervised(self):
        def call(turn, call_id, name, args):
            return {"role": "assistant", "content": "", "_train": True, "_turn": turn, "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}]}

        def result(call_id, body):
            return {"role": "tool", "tool_call_id": call_id, "name": "x", "content": json.dumps(body)}
        messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "T"},
                    call(1, "a", "run_command", {"command": "pytest"}), result("a", {"status": "ok"}),
                    call(2, "b", "run_command", {"command": "pytest"}), result("b", {"status": "ok"}),
                    call(3, "c", "edit_file", {"path": "f"}), result("c", {"status": "error"}),
                    call(4, "d", "edit_file", {"path": "g"}), result("d", {"status": "ok"}),
                    call(5, "e", "run_command", {"command": "pytest"}), result("e", {"status": "ok"}),
                    {"role": "assistant", "content": "Done.", "_train": True, "_turn": 6}]
        marked = exporter.mask_unsound_steps(messages)
        flags = {m["_turn"]: m["_train"] for m in marked if m["role"] == "assistant"}
        self.assertEqual(flags, {1: True, 2: False, 3: False, 4: True, 5: True, 6: True})
        rows = exporter.build_examples({"messages": messages, "tools": []}, mode="prefix", task_id="t", segment=1)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(messages), 13)
        trajectory = exporter.build_examples({"messages": messages, "tools": []}, mode="trajectory",
                                             task_id="t", segment=1)[0]["messages"]
        self.assertEqual(sum(1 for m in trajectory if m["role"] == "assistant" and not m["train"]), 2)

    def test_shortened_tool_result_stays_valid_json_and_keeps_both_ends(self):
        body = {"status": "error", "error_message": "HEAD" + "x" * 9000 + "TAIL",
                "details": {"stderr": "A" * 5000}, "exit_code": 1}
        short = exporter.shorten_tool_result(json.dumps(body), 3000)
        parsed = json.loads(short)
        self.assertLessEqual(len(short), 3100)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["exit_code"], 1)
        self.assertTrue(parsed["error_message"].startswith("HEAD") and parsed["error_message"].endswith("TAIL"))
        self.assertIn("chars omitted", short)
        small = json.dumps({"status": "ok"})
        self.assertEqual(exporter.shorten_tool_result(small, 3000), small)

    def test_trajectory_is_one_row_and_exclude_turn_drops_only_prefix_targets(self):
        rows = exporter.build_examples(self.row, mode="trajectory", task_id="task", segment=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["messages"][-1]["content"], "Fixed.")
        self.assertNotIn("completion", rows[0])
        excluded = exporter.convert_exchanges(exchanges(), exclude_turns={1})
        prefix = exporter.build_examples(excluded, mode="prefix", task_id="task", segment=1)
        self.assertEqual([row["completion"]["content"] for row in prefix], ["Fixed."])
        trajectory = exporter.build_examples(excluded, mode="trajectory", task_id="task", segment=1)
        assistants = [message for message in trajectory[0]["messages"] if message["role"] == "assistant"]
        self.assertEqual([message["train"] for message in assistants], [False, False, True])
        self.assertEqual(assistants[-1]["content"], "Fixed.")

    def test_compaction_carryover_is_not_trained_again(self):
        data = exchanges()
        summary = {"request": {"messages": [{"role": "user", "content": [{
            "type": "text", "text": exporter.SUMMARY_PROMPT + " Please summarize..."}]}]},
            "response_status": 200,
            "response": {"role": "assistant", "content": [{"type": "text", "text": "SUMMARY"}],
                         "stop_reason": "end_turn"}}
        carried = copy.deepcopy(data[1]["request"]["messages"][1:])
        after = {"request": {**copy.deepcopy(data[1]["request"]), "messages": [
            {"role": "user", "content": [{"type": "text", "text": "For context: SUMMARY"}]}] + carried},
            "response_status": 200,
            "response": {"role": "assistant", "content": [{"type": "text", "text": "Resumed."}],
                         "stop_reason": "end_turn"}}
        segment = exporter.convert_task(data + [summary, after])[1]
        trajectory = exporter.build_examples(segment, mode="trajectory", task_id="task", segment=2)
        trained = [message["content"] for message in trajectory[0]["messages"]
                   if message["role"] == "assistant" and message["train"]]
        self.assertEqual(trained, ["Resumed."])
        prefix = exporter.build_examples(segment, mode="prefix", task_id="task", segment=2)
        self.assertEqual([row["completion"]["content"] for row in prefix], ["Resumed."])

    def test_retained_reasoning_stays_on_the_assistant_message(self):
        row = exporter.convert_exchanges(exchanges(), retain_reasoning=True)
        prefix = exporter.build_examples(row, mode="prefix", task_id="task", segment=1, enable_thinking=False)
        self.assertEqual(prefix[0]["completion"][0]["reasoning"], "Inspect first")
        self.assertFalse(prefix[0]["enable_thinking"])

    def test_truncation_keeps_the_opening_user_and_does_not_split_tool_results(self):
        messages = [{"role": "system", "content": "SYSTEM"},
                    {"role": "user", "content": "TASK " + ("x" * 50)}]
        for index in range(6):
            messages.append({"role": "assistant", "content": f"step-{index}", "_train": True, "_turn": index + 1})
            messages.append({"role": "tool", "tool_call_id": f"id-{index}", "name": "run_command", "content": "obs" * 40})
        messages[-2] = {"role": "assistant", "tool_calls": [{"id": "id-last", "type": "function", "function": {
            "name": "run_command", "arguments": {"command": "y" * 80}}}], "content": "", "_train": True, "_turn": 7}
        clipped = exporter.truncate_messages(messages, max_messages=5, max_chars=20)
        self.assertEqual(clipped[0]["content"], "SYSTEM")
        self.assertEqual(clipped[1]["content"], "TASK " + ("x" * 50))
        self.assertLessEqual(len(clipped), 5)
        self.assertIn("[truncated]", clipped[-1]["content"])
        self.assertEqual(clipped[-2]["tool_calls"][0]["function"]["arguments"]["command"], "y" * 80)
        self.assertNotIn("_train", json.dumps(clipped))
        self.assertNotIn("_truncated", json.dumps(clipped))

    def test_failed_batch_does_not_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, data in [("a", exchanges()), ("b", exchanges()[:1])]:
                task = root / "input" / name
                task.mkdir(parents=True)
                (task / "anthropic_exchanges.json").write_text(json.dumps(data))
            proc = subprocess.run([sys.executable, str(Path(exporter.__file__)), str(root / "input"),
                "--mode", "trajectory", "--output", str(root / "out")], capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("missing final tool result", proc.stderr)
            self.assertFalse((root / "out").exists())
            self.assertFalse(list(root.glob(".sft-export-*")))

    def test_cli_writes_both_modes_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            task = root / "input" / "task"
            task.mkdir(parents=True)
            (task / "anthropic_exchanges.json").write_text(json.dumps(exchanges()))
            for mode, count in [("prefix", 2), ("trajectory", 1)]:
                output = root / mode
                cmd = [sys.executable, str(Path(exporter.__file__)), str(root / "input"),
                       "--mode", mode, "--output", str(output)]
                proc = subprocess.run(cmd, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                rows = [json.loads(line) for line in (output / "train.jsonl").read_text().splitlines()]
                self.assertEqual(len(rows), count)
                self.assertTrue(all(row["mode"] == mode and row["enable_thinking"] for row in rows))
                manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(manifest["schema_version"], "gemma4-sft-v4")
                self.assertEqual(manifest["examples"], count)
                self.assertFalse(manifest["retain_reasoning"])
                self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)

class CompactObservationTests(unittest.TestCase):
    def hindsight(self, messages):
        return exporter.compact_observations(messages, policy="hindsight")

    def messages(self, read_text, command_out, old_string):
        def call(cid, name, args):
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}]}
        def result(cid, data):
            return {"role": "tool", "tool_call_id": cid, "content": json.dumps(data)}
        return [
            call("r", "read_file", {"filepath": "a.py"}),
            result("r", {"status": "ok", "content": read_text, "start_line": 1, "end_line": 200,
                         "total_lines": 200, "is_truncated": False}),
            call("c", "run_command", {"command": "pytest"}),
            result("c", {"status": "ok", "stdout": command_out, "stderr": "", "exit_code": 1}),
            call("e", "edit_file", {"filepath": "a.py", "old_string": old_string, "new_string": "x"}),
            result("e", {"status": "ok", "occurrences": 1, "diff": "d\n" * 600, "is_truncated": False}),
        ]

    def test_read_keeps_lines_a_later_edit_copies(self):
        text = "\n".join(f"line_number_{i} = {i}" for i in range(200))
        out = self.hindsight(self.messages(text, "ok", "line_number_150 = 150\nline_number_151 = 151"))
        body = json.loads(out[1]["content"])
        self.assertIn("line_number_150 = 150", body["content"])
        self.assertIn("line_number_151 = 151", body["content"])
        self.assertLess(len(out[1]["content"]), 2400)
        self.assertTrue(body["is_truncated"])
        self.assertIn("lines omitted", body["content"])

    def test_repeated_boilerplate_does_not_scatter_the_window(self):
        func = lambda n: f"def func_{n}(x):\n    origin = get_origin(x)\n    if origin is Union:\n        pass\n"
        text = "\n".join(func(n) for n in range(30))
        old = "    origin = get_origin(x)\n    if origin is Union:\n        pass\n\ndef func_20(x):"
        out = self.hindsight(self.messages(text, "ok", old))
        content = json.loads(out[1]["content"])["content"]
        self.assertIn("def func_20(x):", content)
        self.assertEqual(content.count("lines omitted"), 2)
        self.assertEqual(content.count("origin = get_origin(x)"), 2)

    def test_read_without_edit_is_cut_to_budget_from_the_top(self):
        text = "\n".join(f"line_number_{i} = {i}" for i in range(200))
        out = self.hindsight(self.messages(text, "ok", "never_in_file_text"))
        body = json.loads(out[1]["content"])
        self.assertTrue(body["content"].startswith("line_number_0 = 0"))
        self.assertLess(len(body["content"]), exporter.READ_UNUSED_BUDGET + 80)
        self.assertEqual(body["start_line"], 1)

    def test_command_output_keeps_the_tail_and_exit_code(self):
        out_text = "\n".join(f"noise line {i}" for i in range(300)) + "\n3 failed, 10 passed in 1.2s"
        out = self.hindsight(self.messages("x", out_text, "nothing"))
        body = json.loads(out[3]["content"])
        self.assertTrue(body["stdout"].endswith("3 failed, 10 passed in 1.2s"))
        self.assertEqual(body["exit_code"], 1)
        self.assertLess(len(body["stdout"]), exporter.RUN_BUDGET + 80)

    def test_edit_diff_is_cut_but_status_stays(self):
        out = self.hindsight(self.messages("x", "ok", "nothing"))
        body = json.loads(out[5]["content"])
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["occurrences"], 1)
        self.assertLess(len(body["diff"]), exporter.DIFF_BUDGET + 80)

    def test_short_results_are_returned_unchanged(self):
        messages = self.messages("a = 1", "ok", "a = 1")
        out = self.hindsight(messages)
        self.assertEqual(out[1]["content"], messages[1]["content"])
        self.assertEqual(out[3]["content"], messages[3]["content"])


class CausalObservationTests(unittest.TestCase):
    def tool_message(self, name, data):
        return [{"role": "assistant", "content": "", "tool_calls": [
                    {"id": "x", "type": "function", "function": {"name": name, "arguments": {}}}]},
                {"role": "tool", "tool_call_id": "x", "content": json.dumps(data)}]

    def test_short_results_are_untouched(self):
        messages = self.tool_message("read_file", {"status": "ok", "content": "a\n" * 100, "is_truncated": False})
        self.assertEqual(exporter.compact_observations(messages)[1]["content"], messages[1]["content"])

    def test_long_command_output_keeps_head_and_tail_and_counts_elision(self):
        out = "\n".join(f"line {i}" for i in range(2000)) + "\n5 passed in 1s"
        messages = self.tool_message("run_command", {"status": "ok", "stdout": out, "stderr": "", "exit_code": 0})
        body = json.loads(exporter.compact_observations(messages)[1]["content"])
        self.assertTrue(body["stdout"].startswith("line 0"))
        self.assertTrue(body["stdout"].endswith("5 passed in 1s"))
        self.assertRegex(body["stdout"], r"\[\d+ characters elided\]")
        self.assertLessEqual(len(body["stdout"]), exporter.CAUSAL_LIMITS["run_command"][1] + 60)
        self.assertEqual(body["exit_code"], 0)

    def test_cut_never_depends_on_later_messages(self):
        text = "\n".join(f"code_{i} = {i}" for i in range(400))
        first = self.tool_message("read_file", {"status": "ok", "content": text, "is_truncated": False})
        later = [{"role": "assistant", "content": "", "tool_calls": [{"id": "e", "type": "function", "function": {
            "name": "edit_file", "arguments": {"filepath": "a", "old_string": "code_300 = 300", "new_string": "z"}}}]}]
        alone = exporter.compact_observations(first)[1]["content"]
        with_future = exporter.compact_observations(first + later)[1]["content"]
        self.assertEqual(alone, with_future)
        self.assertTrue(json.loads(alone)["is_truncated"])

    def test_none_policy_keeps_every_result_byte_for_byte(self):
        messages = self.tool_message("read_file", {"status": "ok", "content": "x\n" * 50000})
        self.assertEqual(exporter.compact_observations(messages, policy="none"), messages)

    def test_unknown_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            exporter.compact_observations([], policy="nope")


if __name__ == "__main__":
    unittest.main()
