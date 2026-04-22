# Review Mode: Security

**Applies when:** Changed files include `**/auth/**`, `**/crypto/**`, `**/permissions/**`, or PR label `security`.

**Severity multipliers:** `warning` → `critical` (×2 penalty). All security findings are elevated. This mode is additive — standard checklist still applies.

---

## Checklist

### Injection (OWASP A03)
- [ ] SQL injection: any string interpolation into SQL — require parameterized queries
- [ ] Command injection: `subprocess`, `os.system`, `exec()` with user-controlled input
- [ ] Path traversal: file paths constructed from user input without canonicalization and prefix check
- [ ] Template injection: user input rendered into Jinja2/Mako/similar templates without escaping
- [ ] LDAP/XML/XPath injection: user input composed into query strings

### Authentication and Session (OWASP A01, A07)
- [ ] Authentication bypass: logic that short-circuits auth checks (e.g., trusting a client-supplied role claim)
- [ ] Insecure session tokens: tokens generated with `random` (not `secrets`), too short, or not invalidated on logout
- [ ] Missing auth enforcement: new endpoints or routes that skip the auth middleware/decorator
- [ ] Password handling: passwords logged, compared with `==` instead of constant-time compare, stored without hashing

### Secrets and Credentials (OWASP A02)
- [ ] Hardcoded secrets: API keys, passwords, tokens, private keys in source code or config files
- [ ] Secrets in logs: `logging.info(f"token={token}")` or similar patterns
- [ ] Secrets in URLs: credentials embedded in query parameters or request bodies logged as-is
- [ ] Insecure key storage: cryptographic keys stored in plaintext files, env vars without rotation plan

### Cryptography (OWASP A02)
- [ ] Weak algorithms: MD5, SHA1, DES, RC4 used for security-sensitive operations
- [ ] Insecure randomness: `random.random()` used for tokens, nonces, or session IDs — require `secrets`
- [ ] ECB mode: AES-ECB used instead of AES-GCM or AES-CBC with HMAC
- [ ] Missing integrity: data encrypted but not authenticated (unauthenticated encryption)

### Authorization / Access Control (OWASP A01)
- [ ] Insecure direct object references: ID from request used to fetch resource without checking ownership
- [ ] Missing permission check: new action that reads/writes data without verifying caller's role/scope
- [ ] Privilege escalation path: code that allows a lower-privileged caller to trigger a higher-privileged operation

### Insecure Defaults (OWASP A05)
- [ ] Debug mode enabled in production path
- [ ] CORS set to `*` on authenticated endpoints
- [ ] TLS verification disabled (`verify=False` in requests, `check_hostname=False`)
- [ ] Default credentials not forced to change

### Dependency CVEs (OWASP A06)
- [ ] New dependency added — check for known CVEs (note: flag if version pinned to a known-vulnerable range)
- [ ] Dependency unpinned or uses `>=` with no upper bound in security-sensitive context

---

## Confidence Guidance

Security findings should be flagged even at moderate confidence (0.7+) if the blast radius is high. A SQL injection risk with 0.75 confidence is worth flagging — the cost of a false positive is a comment; the cost of a false negative can be a breach.

Do not flag theoretical vulnerabilities in code paths that are demonstrably unreachable from user input.
