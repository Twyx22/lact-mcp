# Security Policy

Report vulnerabilities privately via GitHub Private Vulnerability Reporting.
No secrets or credentials exist in this repo (stdlib-only, local socket only).

- Secret scanning: Gitleaks in CI (`security.yml`)
- Dependencies: none (Python stdlib only) — nothing to audit
- Code scanning: CodeQL removed (needs GitHub Advanced Security, unavailable
  on private repos) — re-add `codeql.yml` if GHAS is ever enabled
