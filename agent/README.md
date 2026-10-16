# oai-code maintenance notes

This project launches Claude Code through a local Anthropic-to-Responses bridge
and a separate private OAI Gateway.

Files:

- `oai-code` selects the account model and effort, retrieves the gateway key,
  starts the loopback proxy, and launches Claude Code.
- `proxy.py` translates Anthropic Messages requests and SSE responses to and
  from OpenAI Responses format.
- `tests/test_oai_code.py` verifies launcher credential boundaries, request
  translation, tool calls, and streaming without real credentials or network
  access.
- `README.md` documents installation, behavior, and compatibility limits.

Preserve these constraints:

- Never put a gateway key, OAuth token, private host configuration, or other
  credential in source, tests, fixtures, process arguments, or Git history.
- Keep the persistent gateway key in the desktop keyring or caller environment.
- Give Claude Code only a random per-session token for the loopback proxy.
- Keep `PI_AI_GATEWAY_KEY` out of the Claude Code and agent-tool environments.
- Bind the compatibility proxy only to `127.0.0.1` on an ephemeral port.
- Preserve Anthropic's complete streaming event sequence through
  `message_delta` and `message_stop`.
- Preserve tool-call IDs across `tool_use`, Responses `function_call`, and
  `tool_result` translation.
- Keep this project independent from the gateway server repository.

Verify changes with:

```bash
bash -n oai-code
python3 -m py_compile proxy.py
python3 -m unittest discover -v -s tests
```

The suite is intentionally offline. Update this file and `README.md` when the
launcher interface, protocol surface, or security boundary changes.
