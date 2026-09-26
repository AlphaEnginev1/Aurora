from aurora.autonomy.egress_firewall import EgressFirewallPolicy, FirewallPolicyError
from aurora.autonomy.egress_proxy import EgressProxyPolicy

def test_proxy_policy_allows_approved_https_host():
    p = EgressProxyPolicy(frozenset({"github.com"}))
    assert p.authorize("github.com:443") == ("github.com", 443)

def test_proxy_policy_rejects_unapproved_host_and_port():
    p = EgressProxyPolicy(frozenset({"github.com"}))
    for value in ("example.com:443", "github.com:80"):
        try:
            p.authorize(value)
        except PermissionError:
            pass
        else:
            raise AssertionError("policy bypass")

def test_proxy_policy_rejects_ip_literal():
    try:
        EgressProxyPolicy(frozenset({"github.com"})).authorize("1.1.1.1:443")
    except PermissionError:
        pass
    else:
        raise AssertionError("IP literal must be rejected")

def test_firewall_defaults_to_drop_and_allows_proxy_only():
    p = EgressFirewallPolicy()
    rules = p.iptables_rules()
    assert ("-P", "OUTPUT", "DROP") in rules
    p.assert_complete(rules)

def test_firewall_rejects_incomplete_rules():
    p = EgressFirewallPolicy()
    try:
        p.assert_complete((("-P", "OUTPUT", "DROP"),))
    except FirewallPolicyError:
        pass
    else:
        raise AssertionError("incomplete firewall must fail closed")
