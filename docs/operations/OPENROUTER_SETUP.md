# OpenRouter free inference in Aurora

Aurora keeps NVIDIA as its canonical default. OpenRouter is an opt-in second provider and is registered only when `OPENROUTER_API_KEY` is present in the local process environment. The only authorized model ID is `openrouter/free`; Aurora's `free_only` policy rejects other OpenRouter model IDs unless they are explicitly added to the reviewed policy.

## Configure local WSL

Do not paste API keys into chat, commits, screenshots, or dashboard logs. In the WSL terminal where Aurora will be launched, set the key in that shell:

```bash
export OPENROUTER_API_KEY='YOUR_OPENROUTER_KEY'
export AURORA_DIRECTOR_PROVIDER=openrouter
export OPENROUTER_MODEL_ID=openrouter/free
# If Aurora is already running, stop its current daemon so it inherits the new environment:
if [[ -f .aurora-runtime/aurora.pid ]]; then kill "$(cat .aurora-runtime/aurora.pid)" 2>/dev/null || true; rm -f .aurora-runtime/aurora.pid; fi
bash scripts/launch-local.sh
```

The environment variables apply to that launch process. If the dashboard is already running, stop the Aurora runtime and relaunch it from the shell with these variables. Leave `AURORA_DIRECTOR_PROVIDER` unset to keep the normal dynamic router as the Director default.

## Local verification

1. Open `http://127.0.0.1:8787`.
2. Start a new Director conversation and send: `Reply with exactly: AURORA DASHBOARD OPENROUTER OK`.
3. Confirm that the dashboard returns a non-empty answer and that its response metadata identifies provider `openrouter` and model `openrouter/free`.
4. Inspect runtime logs for `AURORA_INFERENCE_ATTEMPT` or `AURORA_INFERENCE_FAILOVER` only if the request fails. Do not share raw environment output or credentials.

A successful unit test or GitHub LIVE workflow does not prove the user's local dashboard is configured. The local conversation above is a separate acceptance check. If OpenRouter fails, the configured Director failover may use the eligible NVIDIA reserve; record the provider/model metadata before concluding the OpenRouter path itself succeeded.

## GitHub LIVE test

Run the workflow **Live OpenRouter Free Inference** manually from Actions after configuring the repository secret `OPENROUTER_API_KEY`. The test uses a real request and prints provider/model/request identifiers, never the API key.
