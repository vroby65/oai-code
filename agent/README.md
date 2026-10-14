# oai-code maintenance notes

This project is a small Bash launcher for running Codex CLI through a private
Pi AI Gateway. It contains no gateway server implementation.

Files:

- `oai-code` reads client configuration, retrieves the local gateway key, and
  starts Codex with a custom Responses provider.
- `tests/test_oai_code.py` verifies the generated Codex arguments without network
  access or real credentials.
- `README.md` is the user-facing installation and security documentation.

Preserve these constraints:

- Never put a gateway key, OAuth token, private host configuration, or other
  credential in source, tests, fixtures, process arguments, or Git history.
- Keep the key in the desktop keyring or the caller's environment.
- Keep `PI_AI_GATEWAY_KEY` excluded from agent-run shell environments.
- Keep `--no-daemon` so provider credentials do not reach an existing Codex
  app-server session.
- Keep this project independent from the gateway server repository.

Verify changes with:

```bash
bash -n oai-code
python3 -m unittest discover -v -s tests
```

The test is intentionally offline. Update this file and `README.md` when the
launcher interface, security boundary, or verification commands change.
