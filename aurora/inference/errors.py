"""Typed failures for the provider execution boundary."""

from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class ProviderErrorContext:
    provider: str
    request_id: str
    execution_id: str
    requested_model: str | None = None
    selected_model: str | None = None
    provider_model: str | None = None

class ProviderExecutionError(RuntimeError):
    """Base error for a failed provider execution."""
    code = "provider_execution_error"
    retryable = False
    def __init__(self,message:str,*,context:ProviderErrorContext)->None:
        super().__init__(message); self.context=context

class ProviderConfigurationError(ProviderExecutionError):
    code="provider_configuration_error"; retryable=False

class ProviderTimeoutError(ProviderExecutionError):
    code="provider_timeout"; retryable=True

class ProviderTransportError(ProviderExecutionError):
    code="provider_transport_error"; retryable=True

class ProviderResponseError(ProviderExecutionError):
    code="provider_invalid_response"; retryable=False

class ProviderHTTPError(ProviderExecutionError):
    code="provider_http_error"; retryable=False
    def __init__(self,message:str,*,context:ProviderErrorContext,status_code:int|None=None)->None:
        super().__init__(message,context=context)
        self.status_code=status_code
