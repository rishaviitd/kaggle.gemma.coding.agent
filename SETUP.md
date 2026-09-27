# Local evaluation

Python 3.12 environment: `.venv`. Organizer wheels are in `wheelhouse/`;
`requirements.lock.txt` records the installed dependencies.

Restore the environment:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock.txt
```

Start Docker Desktop, then build the organizer sandbox:

```bash
docker build --platform linux/amd64 -t swebench-sandbox:latest -f data/docker/Dockerfile.sandbox data/docker
```

The amd64 sandbox matches the supplied Linux dependency wheels. On Apple Silicon,
Docker runs it under emulation, so timings will differ from competition hardware.

Verify the known reference fix without a model:

```bash
.venv/bin/python scripts/evaluate.py --reference-check
```

Once an OpenAI-compatible model server is available:

```bash
LOCAL_INFERENCE_URL=http://localhost:8000/v1 .venv/bin/python scripts/evaluate.py
```

The server must expose the competition base model and the starter adapters
`main_lora` and `tool_lora`. Model weights/server setup is pending.

The runner selects `fastapi_11194`, uses `data/graph` explicitly, and sets 50 tool
calls, 30 minutes, and a 300-second command timeout. It does not change the starter
submission's budget file. Results go into `results/reference` or `results/baseline`.

`data/tasks.jsonl` includes all public task records, but only one snapshot, graph,
and embedding file are downloaded. Other tasks require their matching files. The
curated task wheel directory selects Starlette 0.48.0 to meet the snapshot's
declared constraint. The runner isolates the harness temporary wheel cache per
run to prevent stale dependency injection.
Reference patches are used only in `--reference-check`, never as agent input.

Keep `src/tools` as reference source; the installed harness supplies the runtime
tools. Package agent configuration/prompts/adapters only for submission.

export VLLM_API_KEY="your-key-here"
curl https://legacy-repeal-vowed.ngrok-free.dev/v1/chat/completions \
 -H "Authorization: Bearer $VLLM_API_KEY" \
 -H "Content-Type: application/json" \
 -d '{"model":"gemma4","messages":[{"role":"user","content":"Hello"}],"max_tokens":128,"chat_template_kwargs":{"enable_thinking":false}}'

## Remote base-model task pipeline

The remote vLLM launch must include `--enable-auto-tool-choice
--tool-call-parser gemma4 --reasoning-parser gemma4`. A plain chat endpoint is
not sufficient for the agent. Store `VLLM_API_KEY` in the root `.env`.

```bash
.venv/bin/python scripts/task_pipeline.py --task-id fastapi_11194 \
  --api-base https://legacy-repeal-vowed.ngrok-free.dev/v1 --model gemma4
```

The runner uses the starter agent configuration without adapters in a temporary
copy, routes both agents to the remote base model, and keeps Docker workspaces,
tools, and verification local. It checks automatic tool-calling support before
starting the task. Override `--tasks`, `--snapshot`, `--graph`, `--embeddings`,
`--wheels`, and `--submission` for another task; graph/embedding filenames must
match its repository and base commit. Defaults target only `fastapi_11194`.
Results and generated `.patch` files are saved under `results/remote-baseline/`.
The runner caps output at 4096 tokens per request (override with
`--max-output-tokens`) and uses the starter notebook's context compaction settings
to leave room for tool history within the server's 32768-token context.
Structured result saving excludes the trace object; the harness saves the trace
separately under `results/remote-baseline/traces/`.
