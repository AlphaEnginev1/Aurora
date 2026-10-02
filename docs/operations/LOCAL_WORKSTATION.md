# Aurora local workstation

This is the supported first-use path for Aurora on a Windows workstation with WSL2 Ubuntu.

## One-time setup

From the Ubuntu WSL shell:

```bash
cd ~/AURORA
bash scripts/install-workstation.sh
bash scripts/install-desktop-launcher.sh
```

The second command creates **Aurora.lnk** on the Windows desktop. Daily use does not require opening a terminal.

## Daily use

Double-click **Aurora.lnk**.

The launcher starts Aurora's controlled local egress proxy, starts the continuous Dashboard/Director runtime, waits for the dashboard to answer, and opens:

`http://127.0.0.1:8787`

If Aurora is already running, a second launcher invocation does not start a duplicate runtime.

## Provider configuration

Open **SETUP** in the right rail.

Enter the Gemini and NVIDIA API keys there. Keys are stored only in the ignored `.aurora-runtime/operator-settings.json` file with mode 0600 on Linux. The dashboard receives only redacted provider state and never renders saved secrets.

Gemini can be selected as the Director. NVIDIA provides the inference fabric; the NVIDIA test action queries the account's model catalog so a model can be selected without editing source-controlled policy files.

## Stopping Aurora

The launcher keeps the runtime detached from the short-lived launcher shell. To stop Aurora during local development, terminate the Aurora process recorded in `.aurora-runtime/aurora.pid` and the local proxy recorded in `.aurora-runtime/egress-proxy.pid`. A future dashboard control can wrap this operational action without exposing shell usage to the operator.

## Security boundary

Aurora keeps its existing controlled network path. Networked sandbox work must reach the governed local egress proxy; direct fallback is not enabled by the launcher.
