# Aurora Production Deployment

**Target:** one persistent 24/7 Aurora host running the canonical \`main\` line.

## Runtime topology

\`\`\`
Browser
  |
  v
Dashboard HTTP boundary
  |
  +--> DirectorConversationService --> Director
  |                                  |
  |                                  +--> Gemini (AURORA_DIRECTOR_PROVIDER=gemini)
  |
  +--> read-only DashboardGateway
                                       |
                                       +--> ModelRouter --> NVIDIA
                                       +--> ResearchService --> governed InternetGateway
                                       +--> durable Task/Event stores
\`\`\`

The browser never receives provider credentials and never calls Gemini/NVIDIA directly.

## Host installation

The reference host layout is \`/opt/aurora\` with service account \`aurora\`. A different location is valid only after adjusting the systemd unit and preflight path.

1. Install Python 3.11+ and the host dependencies required by Aurora's sandbox/network boundary, including \`bubblewrap\`.
2. Create the unprivileged \`aurora\` service account and ensure it can read the repository and write only \`.aurora-runtime\`.
3. Create \`/opt/aurora/.venv\` and install Aurora:
   \`/opt/aurora/.venv/bin/pip install -e /opt/aurora\`
4. Copy \`deploy/aurora.env.example\` to \`/etc/aurora/aurora.env\`, insert the real provider/proxy values, and restrict the file to the service account plus root.
5. Set \`AURORA_CANONICAL_COMMIT\` to the exact \`main\` SHA deployed on the host.
6. Configure the real governed egress proxy. Aurora must not run with direct egress.
7. Enable at least one real NVIDIA model in the deployed inference policy.
8. For the selected Director architecture, set \`AURORA_DIRECTOR_PROVIDER=gemini\` and supply the Gemini model/credential.
9. Keep the service command without \`--live\` unless the operator has separately created and validated the explicit live authorization record.

## Persistent service

Install \`deploy/aurora.service\` as \`aurora.service\`.

The unit:
- starts Aurora's continuous Director runtime and dashboard;
- runs as the unprivileged \`aurora\` user;
- restarts after process failure;
- executes \`deploy/aurora-preflight.sh\` before every start;
- persists runtime state under \`.aurora-runtime\`;
- does not enable live mode automatically.

## Preflight

Run before enabling the service:

\`\`\`bash
/opt/aurora/deploy/aurora-preflight.sh
\`\`\`

The preflight fails closed unless:
- all required config files and the canonical portrait asset exist;
- an enabled inference model exists;
- NVIDIA credentials are present;
- Gemini credentials/model are present when Gemini is the Director provider;
- the egress proxy is reachable;
- the runtime directory is writable;
- the dashboard is loopback or has a transport token;
- the deployed Git SHA matches \`AURORA_CANONICAL_COMMIT\`.

## Dashboard acceptance on the host

With the service running, verify:

\`\`\`bash
curl -fsS http://127.0.0.1:8787/ >/dev/null
curl -fsS http://127.0.0.1:8787/api/dashboard/snapshot >/dev/null
curl -fsS http://127.0.0.1:8787/aurora-central-avatar.jpg >/dev/null
\`\`\`

Conversation acceptance then verifies:
- session creation;
- one Director turn;
- provider/model/execution identity;
- avatar \`SPEAKING\` presentation hint;
- durable history retrieval;
- history recovery after service restart.

## Automated acceptance

Run on the deployed checkout:

\`\`\`bash
/opt/aurora/.venv/bin/python -m pytest -q \
  tests/e2e/test_aurora_core_e2e.py \
  tests/test_dashboard_conversation_api.py \
  tests/test_dashboard_ui_contract.py \
  tests/test_avatar_voice_contract.py \
  tests/test_daemon_live_authorization.py \
  tests/test_production_deployment_contract.py
\`\`\`

The physical microphone/browser interaction is not claimed by these tests; browser voice behavior is contract-tested and mocked at the CI boundary. The dedicated LIVE-GEMINI, LIVE-NVIDIA, and LIVE-RESEARCH-DISCOVERY gates remain the authoritative live-provider evidence.

## Live operation

Live operation remains fail-closed. The operator must create the authorization artifact outside Git, matching the deployed canonical commit and all verified prerequisites, then invoke:

\`\`\`bash
/opt/aurora/.venv/bin/python -m aurora.orchestration.daemon \
  --dashboard \
  --live \
  --live-operation director_live_execution
\`\`\`

No API key or authorization JSON belongs in the repository.
