<p align="center">
  <img src="assets/header.png" alt="Gemma 4 Developer Agent Competition banner" width="100%">
</p>

<h1 align="center">Kaggle Gemma Developer Agent</h1>

<p align="center">
  <strong>🛠️ Work in progress · Local evaluation and remote model inference</strong>
</p>

<p align="center">
  A Gemma-powered coding agent that explores repositories, drafts bug fixes, and validates patches in Docker using the competition evaluation harness.
</p>

<p align="center">
  <a href="https://www.kaggle.com/competitions/gemma-4-developer-agent">Competition</a> ·
  <a href="#how-to-run">How to run</a> ·
  <a href="https://rishaviitd.github.io/kaggle.gemma.coding.agent/?v=workflow-2">Presentation</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/Gemma-4-4285F4?logo=google&logoColor=white" alt="Gemma 4">
  <img src="https://img.shields.io/badge/Google_ADK-Agents-34A853?logo=google&logoColor=white" alt="Google ADK">
  <img src="https://img.shields.io/badge/vLLM-Inference-orange" alt="vLLM inference">
  <img src="https://img.shields.io/badge/LiteLLM-Model_Routing-8A2BE2" alt="LiteLLM model routing">
  <img src="https://img.shields.io/badge/Docker-Sandbox-2496ED?logo=docker&logoColor=white" alt="Docker sandbox">
  <img src="https://img.shields.io/badge/Kaggle-Competition-20BEFF?logo=kaggle&logoColor=white" alt="Kaggle competition">
</p>

## Overview

The [Gemma 4 Developer Agent Competition](https://www.kaggle.com/competitions/gemma-4-developer-agent) aims to bring autonomous coding assistance to consumer hardware. Participants post-train Gemma 4 to understand unfamiliar repositories, diagnose software issues, and generate correct patches using fine-tuning, reinforcement learning, prompts, and tools. Code graphs and embeddings support repository navigation; scoring measures the percentage of issues resolved through validation tests.

All agents must use `gemma-4-31b-it-qat-w4a16-ct`. Submissions include an `agent.yaml` configuration, supporting prompts, tools, skills, and optional LoRA adapters in `submission.zip`.

## Data

The competition provides a public training/development set for agent training, prompt design, and local validation. During scoring, it is replaced by a hidden test set. Both sets use the same graph-generation methods and verification standards. See the [dataset description](context/data.md) for the full schema.

### Training data

The public set contains **129 Python bug-fixing and feature-request tasks** from `fastapi/fastapi`, `Textualize/rich`, `psf/requests`, and `encode/httpx`.

| Asset | What it contains |
| --- | --- |
| `tasks.jsonl` | Task ID, repository, base commit, problem statement, optional hints, creation timestamp, reference solution (`patch`), and verification tests (`test_patch`). |
| `snapshots/` | A Git repository archive for each task, frozen before the fix. Forward commit history is removed to prevent access to future solutions. |
| `graphs/` | Python AST call and dependency graphs containing code symbols, source definitions, and relationships for structural navigation. |
| `embeddings/` | 256-dimensional float32 vectors for code symbols, used for semantic code search. |

Reference fixes and verification tests are available for training and offline analysis. They must remain separate from the task input when measuring the agent's ability to solve an issue.

### Test data

The hidden test set has **about 120 tasks from private repositories**, split between public and private leaderboard evaluations. Reference fixes and grading tests are withheld.

## Harness

The competition harness compiles the YAML submission, provides repository tools to the agent, and verifies its patch in a fresh sandbox. The diagram below is reproduced from the [competition harness guide](context/harness.md).

```mermaid
flowchart TB
    subgraph Submission["Competitor Submission (submission.zip / agent_dir)"]
        YAML["agent.yaml + sub_agents/*.yaml"]
        EvalCfg["eval_config.yaml (Optional Budgets)"]
        Prompts["prompts/*.md, configs/*.yaml"]
        Adapters["adapters/* (Optional LoRA / Weights)"]
    end

    subgraph Host["Harness Process (swegemma + adk-submission + adk-eval-core)"]
        Validator["validate_directory & validate_single_declared_model"]
        Server["Local Inference Server (vLLM / Transformers :8000)"]
        Compiler["compile_submission() -> ADK Runner"]
        Tools["SwegemmaContext (9 Bound Tools + Budget Gate)"]
    end

    subgraph Sandboxes["Isolated Per-Task Sandboxes"]
        ContA["Container A: Agent Sandbox (/workspace)\nSnapshot + Editable Install + Baseline Commit"]
        ContB["Container B: Verification Sandbox (/workspace)\nFresh Snapshot + agent_patch + test_patch + pytest"]
    end

    Submission --> Validator
    EvalCfg --> Host
    Adapters --> Server
    Validator --> Compiler
    Server <-->|"OpenAI-compatible /v1 API"| Compiler
    Compiler <-->|"Tool Calls & JSON Responses"| Tools
    Tools <-->|"docker exec / subprocess"| ContA
    ContA -->|"git add -N . && git diff --binary"| ContB
ContB -->|"exit_code == 0 & JUnit XML valid"| Score["Resolution Rate [0.0, 1.0]"]
```

### Supported tools

| Tool | Purpose |
| --- | --- |
| `run_command` | Run shell commands in the task workspace. |
| `read_file` | Read workspace files. |
| `edit_file` / `write_file` | Modify or create workspace files. |
| `get_status` | Check remaining budget and patch status. |
| `submit_patch` | Submit the generated Git diff. |
| `get_code_neighbors` | Find related symbols and callers. |
| `search_similar_code` | Find semantically similar code. |
| `get_code_subgraph` | Inspect relationships among code symbols. |

![Competition tool usage diagram](assets/tool.usage.png)

## How to run

### Requirements

- Python 3 and [`uv`](https://docs.astral.sh/uv/getting-started/installation/) installed (`uvx` supplies the Kaggle CLI automatically).
- A Kaggle account with the competition rules accepted and access to the Gemma 4 model and wheelhouse dataset.
- A Kaggle API token available as `KAGGLE_API_TOKEN` in the root `.env` file or shell environment.
- In `notebooks/kernel-metadata.json`, set `id` to your Kaggle username and a notebook slug, such as `your-kaggle-name/gemma-agent-run`. Keep `machine_shape` set to `NvidiaL4`; the run uses Kaggle's L4 x4 competition accelerator with notebook internet disabled.

Create the ignored root `.env` file with your own token:

```dotenv
KAGGLE_API_TOKEN=your-kaggle-api-token
```

The launcher fetches Kaggle CLI 2.2.4 with `uvx`, so you do not need to install the CLI into the project virtual environment. Your local machine needs internet to contact Kaggle; the notebook run itself has internet disabled.

### Install the project environment

Python 3.12 is needed for local evaluation. Restore the environment from the lockfile:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock.txt
```

Start Docker Desktop and build the sandbox image:

```bash
docker build --platform linux/amd64 -t swebench-sandbox:latest -f data/docker/Dockerfile.sandbox data/docker
```

The amd64 image matches the supplied Linux wheels. On Apple Silicon, Docker uses emulation, so run times may differ. Task datasets and organizer wheels must be obtained separately; they are excluded from Git. Only the `fastapi_11194` snapshot, graph, and embeddings are currently available locally.

### Verify the reference fix

This runs without a model and confirms the local evaluation setup:

```bash
.venv/bin/python scripts/evaluate.py --reference-check
```

### Run the local evaluation pipeline

The local inference server must expose the competition model and support automatic tool calling. Start it, then set the endpoint and run:

```bash
LOCAL_INFERENCE_URL=http://localhost:8000/v1 .venv/bin/python scripts/evaluate.py
```

This runner uses `fastapi_11194`, allows 50 tool calls and 30 minutes, and writes to `results/baseline/`. Reference patches are used only with `--reference-check`.

### Run with a remote vLLM server

The remote vLLM server must enable Gemma tool and reasoning parsers. Store its key in the root `.env` file:

```dotenv
VLLM_API_KEY=your-key-here
```

Run the base-model pipeline (or pass `--api-base` for a different endpoint):

```bash
.venv/bin/python scripts/task_pipeline.py --task-id fastapi_11194 \
  --api-base https://your-model-server/v1 --model gemma4
```

It checks tool calling before starting, runs workspaces and verification locally in Docker, and saves patches, logs, traces, and results under `results/remote-baseline/`. Defaults allow 50 tool calls, 10 minutes, and 4096 output tokens. For other tasks, provide matching task, snapshot, graph, embedding, and wheel paths.

### Launch the Kaggle notebook and follow logs

From the repository root, push and monitor the offline competition notebook:

```bash
python3 scripts/run_kaggle_notebook.py
```

The launcher reports queued status every 10 seconds, then polls and prints new logs after the run starts. To monitor the latest run without launching another version:

```bash
python3 scripts/run_kaggle_notebook.py --follow-only
```

## Project structure

```text
├── src/                    Agent configuration, prompts, and tool reference code
├── scripts/
│   ├── task_pipeline.py    Remote base-model task runner
│   ├── evaluate.py         Evaluation and reference checks
│   └── run_kaggle_notebook.py  Push notebook and poll Kaggle status/logs
├── notebooks/              Kaggle notebook and account-specific metadata
├── context/                Competition and harness notes
├── requirements.lock.txt   Python dependencies
├── data/                   Local task assets (ignored)
├── wheelhouse/             Organizer wheels (ignored)
└── results/                Patches, logs, traces, and results (ignored)
```
