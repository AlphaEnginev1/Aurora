# Aurora Security Model

Threat model: agents, downloaded artifacts, repositories, websites and packages may be compromised.

Defaults: deny unknown sources; deny unknown network destinations; no stored interactive passwords; no unrestricted root shell; isolate untrusted code; verify versions/hashes/signatures when available; audit every acquisition.

Runtime should use a dedicated non-root identity, constrained filesystem/network access and sandboxing.