"""Checks for the multi-GPU Kaggle trainer that do not need a GPU."""

import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "gemma4_e4b_lora_parallel.py"
spec = importlib.util.spec_from_file_location("lora_parallel", MODULE)
parallel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parallel)


class FakeTokenizer:
    def apply_chat_template(self, messages, tools=None, tokenize=False, add_generation_prompt=False,
                            enable_thinking=True):
        text = "\n".join(f"{message['role']}:{message.get('content', '')}" for message in messages)
        if add_generation_prompt:
            text += "\nassistant:"
        return text

    def __call__(self, text, add_special_tokens=False, return_offsets_mapping=False):
        encoded = {"input_ids": [ord(char) for char in text]}
        if return_offsets_mapping:
            encoded["offset_mapping"] = [(index, index + 1) for index in range(len(text))]
        return encoded


def record(experiment, start, end, pid=None):
    return {"gpus": list(experiment["gpus"]), "lora_r": experiment["rank"],
            "pid": pid or 100 + experiment["rank"], "device_names": ["NVIDIA L4"] * len(experiment["gpus"]),
            "started_at": start, "finished_at": end}


class ParallelCheckTests(unittest.TestCase):
    def test_one_rank_64_trajectory_run_with_double_alpha_on_all_gpus(self):
        self.assertEqual([e["rank"] for e in parallel.EXPERIMENTS], [64])
        self.assertEqual([e["alpha"] for e in parallel.EXPERIMENTS], [128])
        self.assertEqual(parallel.EXPERIMENTS[0]["gpus"], (0, 1, 2, 3))
        self.assertTrue(all(e["mode"] == "trajectory" and e["alpha"] == 2 * e["rank"]
                            for e in parallel.EXPERIMENTS))

    def test_every_run_has_its_own_gpu_group_within_a_wave(self):
        for wave in parallel.waves():
            flat = [gpu for experiment in wave for gpu in experiment["gpus"]]
            self.assertEqual(len(flat), len(set(flat)))
            self.assertTrue(set(flat) <= set(range(parallel.TOTAL_GPUS)))
        self.assertEqual(sum(len(wave) for wave in parallel.waves()), len(parallel.RANKS))
        self.assertTrue(all(len(e["gpus"]) == parallel.GPUS_PER_RUN for e in parallel.EXPERIMENTS))

    def test_a_wave_that_ran_together_passes(self):
        wave = parallel.waves()[0]
        parallel.assert_parallel([record(e, 10, 30) for e in wave], wave)

    def test_runs_that_did_not_overlap_fail(self):
        wave = parallel.waves()[0]
        if len(wave) < 2:
            self.skipTest("one run per wave")
        records = [record(e, i * 10, i * 10 + 5) for i, e in enumerate(wave)]
        with self.assertRaisesRegex(RuntimeError, "overlap"):
            parallel.assert_parallel(records, wave)

    def test_wrong_gpu_group_fails(self):
        wave = parallel.waves()[0]
        records = [record(e, 10, 30) for e in wave]
        records[0]["gpus"] = [7]
        with self.assertRaisesRegex(RuntimeError, "gpus"):
            parallel.assert_parallel(records, wave)

    def test_missing_run_fails(self):
        wave = parallel.waves()[0]
        if len(wave) < 2:
            self.skipTest("one run per wave")
        with self.assertRaisesRegex(RuntimeError, "expected"):
            parallel.assert_parallel([record(wave[0], 10, 30)], wave)

    def test_non_l4_fails(self):
        wave = parallel.waves()[0]
        records = [record(e, 10, 30) for e in wave]
        records[0]["device_names"] = ["Tesla T4"] * len(records[0]["gpus"])
        with self.assertRaisesRegex(RuntimeError, "L4"):
            parallel.assert_parallel(records, wave)

    def test_chat_template_must_be_the_canonical_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sft").mkdir()
            (root / "chat_template.jinja").write_text("{# old template #}")
            with self.assertRaisesRegex(RuntimeError, "canonical"):
                parallel.load_chat_template(str(root / "sft"))
            (root / "chat_template.jinja").write_text("{# " + parallel.TEMPLATE_MARKER + " #}")
            self.assertIn(parallel.TEMPLATE_MARKER, parallel.load_chat_template(str(root / "sft")))

    def test_prefix_loss_is_only_the_completion(self):
        row = {
            "mode": "prefix", "enable_thinking": True, "tools": [],
            "messages": [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "fix it"},
                {"role": "assistant", "content": "looking", "train": False},
            ],
            "completion": {"role": "assistant", "content": "done", "train": True},
        }
        encoded = parallel.render_row(FakeTokenizer(), row)
        supervised = "".join(chr(token) for token, label in zip(encoded["input_ids"], encoded["labels"]) if label != -100)
        self.assertIn("done", supervised)
        self.assertNotIn("fix it", supervised)
        self.assertNotIn("looking", supervised)

    def test_trajectory_skips_context_only_turns(self):
        row = {
            "mode": "trajectory", "enable_thinking": True, "tools": [],
            "messages": [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "fix it"},
                {"role": "assistant", "content": "old", "train": False},
                {"role": "tool", "content": "observation"},
                {"role": "assistant", "content": "new", "train": True},
            ],
        }
        encoded = parallel.render_row(FakeTokenizer(), row)
        supervised = "".join(chr(token) for token, label in zip(encoded["input_ids"], encoded["labels"]) if label != -100)
        self.assertIn("new", supervised)
        self.assertNotIn("old", supervised)
        self.assertNotIn("observation", supervised)


TOKENIZER_DIR = Path(__file__).resolve().parents[2] / "models" / "llm" / "gemma-4-e4b-it-qat-tokenizer"
DATASET_DIR = Path(__file__).resolve().parents[2] / "data" / "transient" / "sft-haiku-solved-v6-thoughts"


@unittest.skipUnless((TOKENIZER_DIR / "tokenizer.json").exists(), "Gemma 4 tokenizer not downloaded")
class RealGemmaTemplateTests(unittest.TestCase):
    """The fake tokenizer once hid a template mismatch, so check the real one."""

    @classmethod
    def setUpClass(cls):
        from transformers import AutoTokenizer
        cls.tokenizer = AutoTokenizer.from_pretrained(str(TOKENIZER_DIR))
        cls.tokenizer.chat_template = parallel.load_chat_template(str(TOKENIZER_DIR / "sft"))

    def decoded_spans(self, encoded):
        spans, current = [], []
        for token, label in zip(encoded["input_ids"], encoded["labels"]):
            if label != -100:
                current.append(token)
            elif current:
                spans.append(self.tokenizer.decode(current))
                current = []
        if current:
            spans.append(self.tokenizer.decode(current))
        return spans

    def row(self, completion, mode="prefix"):
        tools = [{"type": "function", "function": {"name": "run_command", "description": "run",
                  "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                                 "required": ["command"]}}}]
        call = {"role": "assistant", "content": "", "train": False, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": {"command": "ls"}}}]}
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "fix it"},
                    call, {"role": "tool", "tool_call_id": "c1", "name": "run_command", "content": '{"status": "ok"}'}]
        return {"mode": mode, "enable_thinking": True, "tools": tools, "messages": messages,
                "completion": completion}

    def test_call_after_a_tool_result_is_supervised_up_to_the_stop_token(self):
        completion = {"role": "assistant", "content": "", "train": True, "tool_calls": [
            {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": {"command": "pwd"}}}]}
        spans = self.decoded_spans(parallel.render_row(self.tokenizer, self.row(completion)))
        self.assertEqual(len(spans), 1)
        self.assertTrue(spans[0].startswith("<|tool_call>call:run_command"))
        self.assertTrue(spans[0].endswith("<tool_call|><|tool_response>"))
        self.assertNotIn("thought", spans[0])

    def test_commentary_and_call_form_one_span_without_a_turn_end(self):
        completion = [
            {"role": "assistant", "content": "I will check the directory.", "train": True},
            {"role": "assistant", "content": "", "train": True, "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": {"command": "pwd"}}}]}]
        spans = self.decoded_spans(parallel.render_row(self.tokenizer, self.row(completion)))
        self.assertEqual(len(spans), 1)
        self.assertTrue(spans[0].startswith("I will check the directory."))
        self.assertNotIn("<turn|>", spans[0])
        self.assertTrue(spans[0].endswith("<|tool_response>"))

    def test_final_answer_ends_with_the_turn_token(self):
        completion = {"role": "assistant", "content": "Fixed.", "train": True}
        spans = self.decoded_spans(parallel.render_row(self.tokenizer, self.row(completion)))
        self.assertEqual(spans, ["Fixed.<turn|>\n"])

    def test_thought_after_a_tool_result_is_context_only(self):
        thought = "The listing may show where the config lives, so a plain ls first."
        completion = [
            {"role": "assistant", "content": "Checking the directory.", "train": True, "reasoning": thought},
            {"role": "assistant", "content": "", "train": True, "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": {"command": "pwd"}}}]}]
        encoded = parallel.render_row(self.tokenizer, self.row(completion))
        spans = self.decoded_spans(encoded)
        self.assertEqual(len(spans), 1)
        self.assertTrue(spans[0].startswith("<channel|>Checking the directory."))
        self.assertTrue(spans[0].endswith("<|tool_response>"))
        self.assertNotIn(thought, spans[0])
        full = self.tokenizer.decode(encoded["input_ids"])
        self.assertIn(thought, full)  # still in the context the model sees

    def test_first_turn_thought_masks_text_but_supervises_the_opener(self):
        thought = "Start by listing the repo root."
        tools = self.row({"role": "assistant", "content": ""})["tools"]
        messages = [
            {"role": "system", "content": "rules"}, {"role": "user", "content": "fix it"},
            {"role": "assistant", "content": "", "train": True, "reasoning": thought, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": {"command": "ls"}}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "run_command", "content": '{"status": "ok"}'}]
        row = {"mode": "trajectory", "enable_thinking": True, "tools": tools, "messages": messages}
        spans = self.decoded_spans(parallel.render_row(self.tokenizer, row))
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0], "<|channel>thought\n")
        self.assertTrue(spans[1].startswith("<channel|>"))
        self.assertIn("<|tool_call>call:run_command", spans[1])
        self.assertTrue(all(thought not in span for span in spans))

    @unittest.skipUnless((DATASET_DIR / "trajectory" / "train.jsonl").exists(), "dataset not built")
    def test_every_built_row_renders_within_the_cap_without_thought_loss(self):
        import json
        parallel.check_manifest(str(DATASET_DIR))
        lines = (DATASET_DIR / "trajectory" / "train.jsonl").read_text().splitlines()
        for line in lines[::7]:
            row = json.loads(line)
            encoded = parallel.render_row(self.tokenizer, row)
            self.assertLessEqual(len(encoded["input_ids"]), parallel.MAX_SEQ_LENGTH)
            self.assertTrue(any(label != -100 for label in encoded["labels"]))
            self.assertEqual(parallel.thought_leaks(self.tokenizer, row, encoded), 0)


class ManifestTests(unittest.TestCase):
    def write(self, root, schema, examples, rows, thoughts=True):
        import json
        (root / "trajectory").mkdir(parents=True)
        manifest = {"schema_version": schema, "examples": examples}
        if thoughts:
            manifest["thoughts"] = {"steps": 1}
        (root / "trajectory" / "manifest.json").write_text(json.dumps(manifest))
        (root / "trajectory" / "train.jsonl").write_text("{}\n" * rows)

    def test_v6_with_matching_rows_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.write(Path(tmp), parallel.DATA_SCHEMA, 2, 2)
            self.assertEqual(parallel.check_manifest(tmp)["examples"], 2)

    def test_older_dataset_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.write(Path(tmp), "gemma4-sft-v5", 2, 2, thoughts=False)
            with self.assertRaises(RuntimeError):
                parallel.check_manifest(tmp)

    def test_row_count_mismatch_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.write(Path(tmp), parallel.DATA_SCHEMA, 3, 2)
            with self.assertRaises(RuntimeError):
                parallel.check_manifest(tmp)


if __name__ == "__main__":
    unittest.main()
