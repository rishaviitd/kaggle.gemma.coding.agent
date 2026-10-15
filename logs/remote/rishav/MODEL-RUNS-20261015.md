# Selected20-task model evaluations —2026-10-15

| Model | Passed | Failed | Remark | Artifacts |
| --- | --- | --- | --- | --- |
| Opus5.5 |16/20|4|Medium effort|[run](itr-1_v2/opus-5p5-20261015/)|
| Sonnet5.5 |14/20|6|High effort|[run](itr-1_v2/sonnet-5p5-20261015/)|
| GLM5.3 |13/20|7|Combined330s baseline+600s timeout retries|[run](itr-1_v2/glm-5p3-20261015/)|
| Sonnet4.5 |10/20|10|Includes retries|[run](itr-1_v2/sonnet-4p5-20261015/)|
| DeepSeek V4 Pro |10/20|10|Rishav v3 graph-ignore prompt|[run](itr-1_v3_graph-ignore/deepseek-v4-pro-rishav-v3-20261015/)|
| Kimi K3 |10/20|10|Includes ten authentication retries|[run](itr-1_v2/kimi-k3-20261015/)|
| DeepSeek V4 Flash |0/7|7|Stopped;13 unfinished|[run](itr-1_v3_graph-ignore/deepseek-v4-flash-rishav-v3-20261015/)|
| DeepSeek V4 Pro original |5/20|15|Original v2;superseded comparison retained|[run](itr-1_v2/deepseek-v4-pro-20261015/)|

One canonical folder per model/run. Each has an ordered task-index.json,task-ids.txt,results-summary.json,run-config.json,and per-task val/ artifacts. Prior GLM budgets and Kimi401 attempts are retained within that model's attempts/ folder. GLM's mixed-budget aggregate is not a single330s score. Pro v3/Flash also differ in prompt;do not interpret all rows as an identical-prompt controlled experiment.
