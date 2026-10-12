# OpenRouter free inference in Aurora

Aurora keeps NVIDIA as its canonical default. OpenRouter is an opt-in second provider and is registered only when OPENROUTER_API_KEY is present in the local process environment. The only authorized model ID is openrouter/free; Aurora's free-only policy rejects other OpenRouter model IDs unless they are explicitly added to the reviewed policy.

## Configure persistent local WSL credentials

The credential is stored only on the local machine, outside the repository, in ~/.config/aurora/aurora.env by default (or under XDG_CONFIG_HOME when that variable is set). The configuration directory is restricted to mode 700 and the file to mode 600. The loader reads only three approved setting names and never executes the file as shell code. Never paste the API key into chat, commits, screenshots, or logs.

From ~/AURORA, run:

    bash scripts/configure-local-openrouter.sh

If OPENROUTER_API_KEY is already exported in the current terminal, the script saves that value without displaying it. Otherwise, it prompts for the key with terminal echo disabled. It also saves the explicit OpenRouter provider selection and the approved openrouter/free model ID.

Then restart Aurora so it reloads the saved settings:

    bash scripts/restart-local.sh

Every later launch through bash scripts/launch-local.sh automatically loads this local configuration. You do not need to re-enter the key when opening a new terminal or restarting the daemon. This persists the local configuration; it does not, by itself, enable automatic WSL/Windows boot startup.

## Local verification

1. Open http://127.0.0.1:8787.
2. Start a new Director conversation and send: Reply with exactly: AURORA PERSISTENT OPENROUTER OK
3. Confirm that the answer appears and response metadata identifies provider openrouter and model openrouter/free.
4. To verify configuration survives a fresh shell, run bash scripts/restart-local.sh from a terminal where the key is not exported, then repeat the conversation test. Never print or share the raw key or the full process environment.

If the saved configuration is removed, the launcher returns to Aurora's normal provider selection. If OpenRouter is unavailable, record the actual provider/model metadata before concluding which route handled a request.

## GitHub LIVE test

Run the workflow Live OpenRouter Free Inference manually from Actions after configuring the repository secret OPENROUTER_API_KEY. The test uses a real request and prints provider/model/request identifiers, never the API key.
