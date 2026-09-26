# ADR-0002: Build/Runtime Separation

The environment that constructs Aurora and the environment that executes Aurora are separate trust domains. Build bootstrap may install dependencies. Runtime uses a dedicated identity, bounded filesystem/network access and stronger sandboxing.
