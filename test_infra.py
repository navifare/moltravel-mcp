"""Infrastructure diagnostic tests for MolTravel MCP server.

Verifies DNS, TLS, and HTTP connectivity.
Run: python3 test_infra.py
"""

import json
import socket
import ssl
import urllib.request
import urllib.error


PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

# The correct domain (single 't'): mcp.moltravel.com
CORRECT_DOMAIN = "mcp.moltravel.com"
CORRECT_URL = "https://mcp.moltravel.com/mcp"

# The typo domain (double 't'): mcp.molttravel.com — does NOT work
TYPO_DOMAIN = "mcp.molttravel.com"


def test_dns():
    """Verify correct domain resolves separately from the typo domain."""
    print("\n=== DNS Resolution ===")
    for domain in [CORRECT_DOMAIN, TYPO_DOMAIN, "molttravel.com"]:
        try:
            ips = sorted(set(a[4][0] for a in socket.getaddrinfo(domain, 443)))
            print(f"  {domain} -> {', '.join(ips)}")
        except socket.gaierror as e:
            print(f"  {domain} -> FAILED: {e}")


def test_tls_correct_domain():
    """Verify the correct domain has a valid TLS cert."""
    print("\n=== TLS: Correct Domain ===")
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=CORRECT_DOMAIN) as s:
            s.settimeout(10)
            s.connect((CORRECT_DOMAIN, 443))
            cert = s.getpeercert()
        sans = [entry[1] for entry in cert.get("subjectAltName", [])]
        covered = any(
            san == CORRECT_DOMAIN or (san.startswith("*.") and CORRECT_DOMAIN.endswith(san[1:]))
            for san in sans
        )
        print(f"  {PASS} {CORRECT_DOMAIN}: TLS handshake OK")
        print(f"       SANs: {sans}")
        print(f"       Domain covered: {covered}")
        return True
    except Exception as e:
        print(f"  {FAIL} {CORRECT_DOMAIN}: {e}")
        return False


def test_tls_typo_domain():
    """Verify the typo domain fails TLS (expected)."""
    print("\n=== TLS: Typo Domain (expected to fail) ===")
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=TYPO_DOMAIN) as s:
            s.settimeout(10)
            s.connect((TYPO_DOMAIN, 443))
        print(f"  {WARN} {TYPO_DOMAIN}: TLS unexpectedly succeeded")
        return False
    except (ssl.SSLError, ssl.SSLCertVerificationError) as e:
        print(f"  {PASS} {TYPO_DOMAIN}: TLS fails as expected — {e}")
        return True
    except socket.timeout:
        print(f"  {PASS} {TYPO_DOMAIN}: Connection times out (no TLS service)")
        return True
    except Exception as e:
        print(f"  {WARN} {TYPO_DOMAIN}: {type(e).__name__}: {e}")
        return True


def test_mcp_endpoint():
    """Verify the MCP endpoint responds to initialize."""
    print("\n=== MCP Endpoint ===")
    init_msg = json.dumps({
        "jsonrpc": "2.0", "id": "test-1", "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "infra-test", "version": "1.0.0"},
        },
    }).encode()
    try:
        req = urllib.request.Request(
            CORRECT_URL, data=init_msg, method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        resp = urllib.request.urlopen(req, timeout=15)
        body = resp.read().decode()
        print(f"  {PASS} POST {CORRECT_URL} -> {resp.status}")
        for line in body.split("\n"):
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    info = data.get("result", {}).get("serverInfo", {})
                    if info:
                        print(f"       Server: {info}")
                except json.JSONDecodeError:
                    pass
        return True
    except Exception as e:
        print(f"  {FAIL} POST {CORRECT_URL} -> {e}")
        return False


def test_get_returns_406():
    """Verify GET on /mcp returns 406 (MCP requires POST)."""
    print("\n=== GET /mcp (should be 405/406) ===")
    try:
        req = urllib.request.Request(CORRECT_URL, method="GET")
        resp = urllib.request.urlopen(req, timeout=10)
        print(f"  {WARN} GET -> {resp.status} (unexpected success)")
        return False
    except urllib.error.HTTPError as e:
        if e.code in (405, 406):
            print(f"  {PASS} GET -> {e.code} {e.reason} (correct — MCP requires POST)")
            return True
        else:
            print(f"  {WARN} GET -> {e.code} {e.reason}")
            return False
    except Exception as e:
        print(f"  {FAIL} GET -> {e}")
        return False


def main():
    print("=" * 55)
    print("MolTravel Infrastructure Diagnostics")
    print("=" * 55)

    test_dns()
    tls_ok = test_tls_correct_domain()
    test_tls_typo_domain()
    mcp_ok = test_mcp_endpoint()
    test_get_returns_406()

    print("\n" + "=" * 55)
    print("RESULT")
    print("=" * 55)
    if tls_ok and mcp_ok:
        print(f"  {PASS} mcp.moltravel.com is working correctly")
    else:
        print(f"  {FAIL} Issues detected — see above")
    print()


if __name__ == "__main__":
    main()
