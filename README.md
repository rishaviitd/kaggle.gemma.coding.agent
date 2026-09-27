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
  <a href="SETUP.md">Setup Guide</a>
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

## What it does

- Navigates code with file tools, repository graphs, and semantic search.
- Uses a main coding agent and a code analyzer configured through YAML.
- Runs remote model inference while keeping workspaces and tests local.
- Saves patches, execution logs, traces, and test results for each task.
- Supports reference-patch checks to verify the evaluation setup.

## Quick start

Follow [SETUP.md](SETUP.md) to restore Python dependencies, obtain the task assets, and build the Docker sandbox. Organizer wheels and datasets are required and are excluded from Git.

Create a root `.env` file:

```dotenv
VLLM_API_KEY=your-key-here
LOCAL_INFERENCE_URL=https://your-model-server/v1
```

The remote server must support automatic tool calling. Run the default task:

```bash
.venv/bin/python scripts/task_pipeline.py --task-id fastapi_11194 --model gemma4
```

Verify the setup using the reference patch:

```bash
.venv/bin/python scripts/evaluate.py --reference-check
```

## Project structure

```text
├── src/                    Agent configuration, prompts, and tool reference code
├── scripts/
│   ├── task_pipeline.py    Remote base-model task runner
│   └── evaluate.py         Evaluation and reference checks
├── context/                Competition and harness notes
├── requirements.lock.txt   Python dependencies
├── SETUP.md                Detailed setup instructions
├── data/                   Local task assets (ignored)
├── wheelhouse/             Organizer wheels (ignored)
└── results/                Patches, logs, traces, and results (ignored)
```

## Evaluation

The default pipeline targets `fastapi_11194` with a 50-tool-call budget and a 10-minute agent time limit. Results are written to `results/remote-baseline/`.

Check `resolved` and the test output to determine whether a patch solved the task. A `SUCCESS` execution status alone does not mean the validation tests passed.

No Kaggle leaderboard score is recorded in this repository yet.
