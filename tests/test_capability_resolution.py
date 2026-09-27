from aurora.autonomy.acquisition import AcquisitionResult
from aurora.autonomy.broker import CapabilityBroker
from aurora.autonomy.capability_resolution import CapabilityResolutionRuntime
from aurora.autonomy.models import CapabilityRequest
from aurora.autonomy.policy import Policy
from aurora.autonomy.registry import ToolSpec


class FakeAcquisition:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def acquire(self, spec):
        self.calls.append(spec.name)
        return self.result


class Discovery:
    def __init__(self, candidates):
        self.candidates = tuple(candidates)
        self.calls = []

    def discover(self, request):
        self.calls.append(request.name)
        return self.candidates


def test_unresolved_capability_is_discovered_then_acquired():
    spec = ToolSpec("browser-driver", "runtime", "pypi", "browser-driver", version="1.2")
    acquisition = FakeAcquisition(
        AcquisitionResult(
            "browser-driver",
            "installed",
            "pypi",
            "browser-driver",
            "/managed-site",
            version="1.2",
            verification="verified",
        )
    )
    broker = CapabilityBroker(
        Policy(frozenset({"python.package:browser-driver"}), frozenset({"pypi"})),
        {},
        acquisition,
    )
    discovery = Discovery((spec,))
    registered = []
    runtime = CapabilityResolutionRuntime(
        broker,
        discovery=discovery,
        registrar=registered.append,
    )

    resolved = runtime.ensure(
        CapabilityRequest(
            "python.package:browser-driver",
            "browser-specialist",
            "need a browser driver",
        )
    )

    assert resolved.result.status == "installed"
    assert resolved.result.verification == "verified"
    assert resolved.discovered == ("browser-driver",)
    assert acquisition.calls == ["browser-driver"]
    assert discovery.calls == ["python.package:browser-driver"]
    assert registered == [spec]
    assert broker.resolve(CapabilityRequest("python.package:browser-driver", "x")) == spec


def test_discovery_respects_source_policy_and_fails_closed():
    spec = ToolSpec("unsafe-tool", "runtime", "unknown-source", "unsafe-tool")
    acquisition = FakeAcquisition(
        AcquisitionResult(
            "unsafe-tool",
            "installed",
            "unknown-source",
            "unsafe-tool",
            "/managed-site",
            verification="verified",
        )
    )
    broker = CapabilityBroker(
        Policy(frozenset({"python.package:unsafe-tool"}), frozenset({"pypi"})),
        {},
        acquisition,
    )
    runtime = CapabilityResolutionRuntime(
        broker,
        discovery=Discovery((spec,)),
    )

    resolved = runtime.ensure(
        CapabilityRequest("python.package:unsafe-tool", "agent", "test")
    )

    assert resolved.result.status == "blocked_source"
    assert acquisition.calls == []


def test_no_discovery_provider_preserves_fail_closed_unresolved_state():
    broker = CapabilityBroker(
        Policy(frozenset({"python.package:missing"}), frozenset({"pypi"})),
        {},
        FakeAcquisition(
            AcquisitionResult("missing", "installed", "pypi", "missing", "/x", verification="verified")
        ),
    )
    runtime = CapabilityResolutionRuntime(broker)

    resolved = runtime.ensure(
        CapabilityRequest("python.package:missing", "agent", "test")
    )

    assert resolved.result.status == "unresolved"
    assert resolved.attempts == 1
