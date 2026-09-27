"""Autonomous capability subsystem."""

from .capability_resolution import (
    CapabilityDiscoveryProvider,
    CapabilityResolution,
    CapabilityResolutionError,
    CapabilityResolutionRuntime,
    HttpCapabilityDiscoveryProvider,
)
from .computer_operator import (
    ComputerAction,
    ComputerOperator,
    ComputerOperatorError,
    ComputerPolicy,
    CredentialProvider,
    CredentialRef,
    PlaywrightSession,
)

__all__ = [
    "CapabilityDiscoveryProvider",
    "CapabilityResolution",
    "CapabilityResolutionError",
    "CapabilityResolutionRuntime",
    "HttpCapabilityDiscoveryProvider",
    "ComputerAction",
    "ComputerOperator",
    "ComputerOperatorError",
    "ComputerPolicy",
    "CredentialProvider",
    "CredentialRef",
    "PlaywrightSession",
]
