# E1 OEC student kernel (Run A)

Runs the E4B base student on Kaggle (4x L4, internet off) for the first k agent calls of each
task and writes one `snapshot/` per job. Haiku continues later, locally, from the snapshot
(`scripts/runs/run_teacher_docker.sh`). This folder holds the kernel script and its metadata.
The kernel needs no Anthropic key.

## What is in git and what is not

| Item | In git | Where it comes from |
|---|---|---|
| `gen_student.py`, `kernel-metadata.json` | yes (this folder) | this repo |
| Pipeline code (`scripts/runs/`, `scripts/traces/`, `src/`) | yes | this repo, branch `e1-oec-dataset` |
| Competition data (snapshots, graphs, embeddings, wheels) | no | Kaggle competition `gemma-4-developer-agent` |
| Kaggle dataset `akshayggupta1/e1-oec-code` | no | private Kaggle dataset, shared by akshay |
| Kaggle dataset `akshayggupta1/e1-oec-assets` | no | private Kaggle dataset, shared by akshay |
| Kaggle dataset `akshayggupta1/e1-oec-py313-env` (`h313.bin`) | no | private Kaggle dataset, shared by akshay |
| Gemma model `google/gemma-4/other/gemma-4-e4b-it-qat-w4a16-ct/2` | no | Kaggle Models, accept the license |

Do not commit competition data or the `h313.bin` bundle. Do not make any of these datasets
public.

## One-time setup (teammate)

1. Join the Kaggle competition team and accept the competition rules. The kernel mounts the
   competition, and the mount fails without an accepted rule set.
2. Accept the Gemma model license on the model page.
3. Ask akshay to share the three private datasets above with your Kaggle account.
4. Install the Kaggle CLI (version 2.2.4 is what the run was tested with):

   ```bash
   uvx --from kaggle==2.2.4 kaggle --version
   ```

   The CLI needs your own Kaggle API credentials. Do not paste them anywhere or commit them.

## Run it

From the repo root:

```bash
git checkout e1-oec-dataset
cp -r kaggle/e1-oec-student /tmp/e1-oec-student-run
```

Edit `/tmp/e1-oec-student-run/kernel-metadata.json` and change the `id` to your own account,
for example `yourname/e1-oec-student`. Keep the rest as it is.

```bash
uvx --from kaggle==2.2.4 kaggle kernels push -p /tmp/e1-oec-student-run --accelerator NvidiaL4
uvx --from kaggle==2.2.4 kaggle kernels status yourname/e1-oec-student
```

Pushing starts a run. It uses your weekly GPU quota (30 h). The last run of this job set took
about 170 minutes on 4 L4s.

When the run finishes, download the output:

```bash
uvx --from kaggle==2.2.4 kaggle kernels output yourname/e1-oec-student -p /tmp/e1-oec-out
```

The output contains `gen_student_report.json`, `run_config.json`, the console logs, and
`logs/remote/rishav/inspiration-one/oec-v1-a<N>/<split>/<task>/snapshot/` for each attempt.
Check the report's `snapshots_produced_by_attempt` and `snapshots_saved_by_attempt`. They must
match, or the run has lost snapshots.

## Settings to know

- `ATTEMPTS = 5`, `SWITCH_LOW, SWITCH_HIGH = 2, 10`, `TEMPERATURE = 1.0` in `gen_student.py`.
- Run B must use the same `--temperature 1.0` (the agent config hash depends on it).
- The run writes to `/kaggle/working` only. Anything else is lost when the session ends.

## Known limits

- Kaggle only keeps the output of the latest version of a kernel. Download the output before
  the next push.
- The kernel with the current range (k 2 to 10) has not run yet. The earlier run, with a wider k range, left 64 attempt-1 snapshots; the other 80 were lost (see the logs in that output).
