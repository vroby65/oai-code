# oai-code

`oai-code` runs Claude Code with models provided by a private OAI Gateway. It
opens a terminal picker for the account model and reasoning effort, starts an
ephemeral loopback proxy that translates Anthropic Messages requests into
OpenAI Responses requests, and then launches the normal Claude Code TUI.

The gateway server remains in the separate `oai-gateway` project. Routing
Claude Code to non-Claude models is an unofficial compatibility setup and is
not supported by Anthropic.

## Requirements

- Linux with Claude Code and Python 3 on `PATH`;
- `curl`, `jq`, and `fzf` for the interactive picker;
- `secret-tool` and an available desktop keyring;
- an OAI Gateway exposing `/v1/models` and `/codex/v1/responses`.

The bridge supports streamed text, Claude Code tool calls and tool results,
system prompts, image inputs, token-count estimates, and non-streaming Messages
responses.

## Install

Link the launcher somewhere on your `PATH`:

```bash
ln -s "$PWD/oai-code" ~/.local/bin/oai-code
```

Create `~/.config/oai-code/config` and restrict its permissions:

```ini
gateway_url=http://192.168.1.50:18765
model=gpt-6.1-sol
reasoning_effort=medium
```

```bash
chmod 600 ~/.config/oai-code/config
```

Run it from a project directory:

```bash
oai-code
```

With no arguments in a terminal, the launcher retrieves the current account
model catalog and opens two `fzf` screens. After selection it starts Claude
Code with the chosen model and one of `low`, `medium`, `high`, `xhigh`, or
`max`.

Arguments are forwarded to Claude Code. Non-interactive commands use the model
and effort from configuration or environment variables:

```bash
oai-code -p "Summarize this project"
```

`OAI_GATEWAY_URL`, `OAI_CODE_MODEL`, `OAI_CODE_REASONING_EFFORT`, and
`PI_AI_GATEWAY_KEY` may be supplied in the environment. Environment values take
precedence over the config file.

## Security

On first use, the launcher asks for the local gateway key and stores it in the
desktop keyring. The key is given only to the local proxy and is removed before
Claude Code starts. Claude Code receives a random per-session token for a proxy
bound to an ephemeral `127.0.0.1` port. The proxy and its temporary files are
removed when Claude Code exits.

The launcher enables Claude Code's subprocess environment scrubbing so agent
tools do not inherit gateway credentials. Keys and tokens are never placed in
process arguments or repository files.

## Test

The offline suite uses fake executables and simulated protocol events:

```bash
bash -n oai-code
python3 -m py_compile proxy.py
python3 -m unittest discover -v -s tests
```

## Compatibility

Claude Code evolves its gateway protocol over time. This bridge implements the
Messages endpoints and event sequence used by Claude Code 2.1.158. Features
that require a claude.ai identity outside the Messages API, such as Remote
Control and voice dictation, are unavailable while a custom gateway credential
is active.

## License

[MIT](LICENSE)
