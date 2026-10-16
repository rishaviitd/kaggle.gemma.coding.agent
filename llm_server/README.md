# Gemma 4 inference

Model: /datadrive/models/gemma-4-31B-it-qat-w4a16-ct
Environment: /datadrive/llm_inference/.venv

Start on the server:
```bash
cd /home/azureuser/VoxCPM/rishav_experiments/llm_inference
nohup ./serve.sh > server.log 2>&1 < /dev/null &
echo $! > server.pid
```

Request from your computer:
```bash
curl http://10.55.1.6:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma4","messages":[{"role":"user","content":"Say hello in one sentence."}],"max_tokens":128,"chat_template_kwargs":{"enable_thinking":false}}'
```

Health: GET /health. Models: GET /v1/models.
Stop the process started above: `kill "$(cat server.pid)"`.

Guide: https://github.com/vllm-project/recipes/blob/main/Google/Gemma4.md

Default startup: context 32768, automatic tool choice, Gemma 4 tool and reasoning parsers; GPU memory utilization 0.60.
