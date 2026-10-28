# Unattended ML pipeline runner (macOS launchd)

The long-running satellite stages take hours:
- `features_extract`: about 183k Planetary Computer point requests, resumable per H3 cell;
- `train_classifier`.

They must not depend on an interactive terminal, an IDE session or an AI assistant staying alive.
This folder installs a per-user **launchd agent** that runs them by itself, step by step, until they
succeed.

| Need | How |
|---|---|
| Survive session end, IDE close, assistant limits | launchd owns the process, not a terminal |
| Survive crashes and network failures | exit ≠ 0 → launchd restarts after 10 min (`ThrottleInterval`); `MAX_FAILURES` (20) caps it |
| Survive sleep, log-out, reboot | `caffeinate -i` while working; `RunAtLoad` restarts it at log-in; caches make it resume, not restart |
| Never lose finished work | DVC caches each stage; `dvc push` to the configured remote at the end |
| No duplicate runs | atomic lock; waits if someone already runs `dvc repro` in the same checkout |
| Know what happened | `status.json`, `run.log`, a macOS notification when finished or when it gives up |

## Use
```bash
infra/local/ml-pipeline/aquacreds-ml.sh install ~/Documents/aquacreds-work/<checkout>   # starts now
infra/local/ml-pipeline/aquacreds-ml.sh status
infra/local/ml-pipeline/aquacreds-ml.sh logs
infra/local/ml-pipeline/aquacreds-ml.sh stop       # unload (resume later with `start`)
infra/local/ml-pipeline/aquacreds-ml.sh reset      # after success, to run the stages again
infra/local/ml-pipeline/aquacreds-ml.sh uninstall
```
- **Optional:** pass stage names after the checkout path (default: `features_extract train_classifier`).
- **State and logs:** `~/Library/Application Support/AquaCreds/ml-pipeline/`.
- **Checkout location:** use a checkout in a permanent folder. The installer refuses `/tmp` paths,
  because macOS deletes their files after a few idle days.

## Limits
- **Sleep:** a Mac that is asleep does no work; the runner resumes when it wakes.
  - Keep it on power for long runs.
  - On AC power, closing the lid sleeps the Mac unless an external display is attached.
  - `caffeinate -i` only prevents *idle* sleep.
- **Judgement steps:** these still need a person. For example, deciding whether a retrained model
  becomes v1.1, and opening the PR. The runner leaves the results and the metrics JSON in the checkout
  for that.
- **Production:** the satellite pipeline does not use this. On the server, Celery beat runs nightly
  ingestion, classification each April, and alerts (`services/worker/src/aqua_worker/celery_app.py`).
  This runner is for the offline ML data/training pipeline on a laptop.
