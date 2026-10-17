"""Behavior tests for raw-trace conversion; optional real-tokenizer integration tests."""

import copy
import importlib
import inspect
import json
import os
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


@unittest.skipUnless(os.environ.get("GEMMA_SFT_TOKENIZER"), "Set GEMMA_SFT_TOKENIZER for official-tokenizer checks")
class OfficialTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from transformers import AutoTokenizer
        cls.tokenizer = AutoTokenizer.from_pretrained(os.environ["GEMMA_SFT_TOKENIZER"], local_files_only=True)

    def setUp(self):
        self.assertIsNotNone(exporter, "The SFT exporter has not been implemented")

    def test_thinking_mode_and_teacher_reasoning_are_independent(self):
        self.assertIn("retain_reasoning", inspect.signature(exporter.convert_exchanges).parameters)
        self.assertIn("enable_thinking", inspect.signature(exporter.encode_example).parameters)
        for retain in [False, True]:
            for enable in [False, True]:
                with self.subTest(retain_reasoning=retain, enable_thinking=enable):
                    row = exporter.convert_exchanges(exchanges(), retain_reasoning=retain)
                    encoded = exporter.encode_example(row, self.tokenizer, enable_thinking=enable)
                    self.assertEqual("<|think|>" in encoded["text"], enable)
                    self.assertEqual("Inspect first" in encoded["text"], retain)
                    self.assertEqual("<|channel>thought" in encoded["text"], retain)
                    expected = self.tokenizer.apply_chat_template(
                        row["messages"], tools=row["tools"], tokenize=False,
                        add_generation_prompt=False, enable_thinking=enable, preserve_thinking=retain)
                    self.assertEqual(encoded["text"], expected)
                    supervised = self.tokenizer.decode([x for x in encoded["labels"] if x != -100])
                    self.assertNotIn("<|think|>", supervised)
                    self.assertEqual("Inspect first" in supervised, retain)
                    self.assertNotIn("OBSERVATION_ONLY", supervised)

    def test_cli_records_independent_settings_and_legacy_alias(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "anthropic_exchanges.json"
            source.write_text(json.dumps(exchanges()))
            cases = [(["--enable-thinking"], False, True),
                     (["--retain-reasoning"], True, False),
                     (["--thinking"], True, True)]
            for index, (flags, retain, enable) in enumerate(cases):
                with self.subTest(flags=flags):
                    output = root / f"out-{index}"
                    proc = subprocess.run([
                        sys.executable, str(Path(exporter.__file__)), str(source),
                        "--output", str(output), "--tokenizer", os.environ["GEMMA_SFT_TOKENIZER"],
                        "--local-files-only", *flags], capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    manifest = json.loads((output / "manifest.json").read_text())
                    self.assertEqual(manifest["retain_reasoning"], retain)
                    self.assertEqual(manifest["enable_thinking"], enable)
                    self.assertEqual(manifest["schema_version"], "gemma4-sft-v2")
                    self.assertNotIn("thinking", manifest)
                    text = next((output / "text").glob("*.txt")).read_text()
                    self.assertEqual("<|think|>" in text, enable)
                    self.assertEqual("Inspect first" in text, retain)
                    if flags == ["--thinking"]:
                        self.assertIn("deprecated", proc.stderr)

    def test_legacy_flag_cannot_silently_override_explicit_mode(self):
        proc = subprocess.run([
            sys.executable, str(Path(exporter.__file__)), "unused", "--output", "unused-output",
            "--thinking", "--no-enable-thinking"], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("cannot combine", proc.stderr)

    def test_matches_upstream_and_masks_observations_and_prompts(self):
        row = exporter.convert_exchanges(exchanges())
        encoded = exporter.encode_example(row, self.tokenizer)
        original = self.tokenizer.apply_chat_template(row["messages"], tools=row["tools"],
            tokenize=False, add_generation_prompt=False, enable_thinking=False)
        self.assertEqual(encoded["text"], original)
        self.assertLess(original.index("I will inspect it."), original.index("<|tool_call>"))
        self.assertLess(original.index("<|tool_call>"), original.index("OBSERVATION_ONLY"))
        self.assertEqual(original.count("SYSTEM_ONLY"), 1)
        self.assertEqual(original.count("declaration:run_command"), 1)
        self.assertEqual(original.count("Fix the bug"), 1)
        ids = encoded["input_ids"]
        labels = encoded["labels"]
        offsets = self.tokenizer(original, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
        for value in ["SYSTEM_ONLY", "Fix the bug", "OBSERVATION_ONLY", "<tool_response|>"]:
            start = original.index(value)
            matching = [i for i, (a, b) in enumerate(offsets) if a < start + len(value) and b > start]
            self.assertTrue(matching)
            self.assertTrue(all(labels[i] == -100 for i in matching), value)
        supervised = self.tokenizer.decode([t for t, label in zip(ids, labels) if label != -100])
        self.assertIn("I will inspect it.", supervised)
        self.assertIn("call:run_command", supervised)
        self.assertIn("<|tool_response>", supervised)
        self.assertIn("Fixed.", supervised)
        self.assertTrue(original.endswith("<turn|>\n"))
        self.assertIn(self.tokenizer.convert_tokens_to_ids("<turn|>"), labels)

    def test_excluded_turn_is_context_only_and_thinking_is_optional(self):
        row = exporter.convert_exchanges(exchanges(), retain_reasoning=True, exclude_turns={1})
        encoded = exporter.encode_example(row, self.tokenizer, enable_thinking=True)
        self.assertIn("Inspect first", encoded["text"])
        supervised = self.tokenizer.decode([x for x in encoded["labels"] if x != -100])
        self.assertNotIn("Inspect first", supervised)
        self.assertNotIn("run_command", supervised)
        self.assertIn("Fixed.", supervised)
        included = exporter.encode_example(exporter.convert_exchanges(exchanges(), retain_reasoning=True),
                                           self.tokenizer, enable_thinking=True)
        supervised = self.tokenizer.decode([x for x in included["labels"] if x != -100])
        self.assertIn("Inspect first", supervised)

    def test_rejects_length_overflow_without_silent_truncation(self):
        with self.assertRaisesRegex(ValueError, "max_length"):
            exporter.encode_example(exporter.convert_exchanges(exchanges()), self.tokenizer, max_length=10)

    def test_thinking_before_a_later_user_turn_is_not_silently_discarded(self):
        row = exporter.convert_exchanges(exchanges(), retain_reasoning=True)
        row["messages"].extend([
            {"role": "user", "content": "One more change"},
            {"role": "assistant", "content": "Done", "_turn": 3, "_train": True},
        ])
        for enable in [False, True]:
            with self.subTest(enable_thinking=enable), self.assertRaisesRegex(ValueError, "thinking.*user|reasoning.*user"):
                exporter.encode_example(row, self.tokenizer, enable_thinking=enable)

    def test_failed_batch_does_not_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, data in [("a", exchanges()), ("b", exchanges()[:1])]:
                task = root / "input" / name
                task.mkdir(parents=True)
                (task / "anthropic_exchanges.json").write_text(json.dumps(data))
            proc = subprocess.run([sys.executable, str(Path(exporter.__file__)), str(root / "input"),
                "--output", str(root / "out"), "--tokenizer", os.environ["GEMMA_SFT_TOKENIZER"],
                "--local-files-only"], capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("missing final tool result", proc.stderr)
            self.assertFalse((root / "out").exists())
            self.assertFalse(list(root.glob(".sft-export-*")))

    def test_cli_writes_loadable_labels_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            task = root / "input" / "task"
            task.mkdir(parents=True)
            (task / "anthropic_exchanges.json").write_text(json.dumps(exchanges()))
            cmd = [sys.executable, str(Path(exporter.__file__)), str(root / "input"),
                   "--output", str(root / "out"), "--tokenizer", os.environ["GEMMA_SFT_TOKENIZER"],
                   "--local-files-only"]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            row = json.loads((root / "out" / "train.jsonl").read_text())
            self.assertEqual(len(row["input_ids"]), len(row["labels"]))
            self.assertEqual(len(row["input_ids"]), len(row["attention_mask"]))
            manifest = json.loads((root / "out" / "manifest.json").read_text())
            self.assertEqual(manifest["examples"], 1)
            self.assertTrue(manifest["references"])
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
