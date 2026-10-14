# oai-code

`oai-code` starts Codex CLI through a private Pi AI Gateway while preserving
the normal Codex coding tools. It is a client launcher only: the gateway server
is maintained in the separate `oai-gateway` project.

It does not modify Claude Code or emulate the Anthropic Messages API.

## Requirements

- Linux with Codex CLI on `PATH`;
- `secret-tool` and an available desktop keyring;
- a Pi AI Gateway exposing the Responses endpoint at `/codex/v1/responses`.

## Install

Link the launcher somewhere on your `PATH`:

```bash
ln -s "$PWD/oai-code" ~/.local/bin/oai-code
```

Create `~/.config/oai-code/config` and restrict its permissions:

```ini
gateway_url=http://192.168.1.50:18765
model=gpt-6.1-sol
```

```bash
chmod 600 ~/.config/oai-code/config
```

Then run it from a project directory:

```bash
oai-code
```

On first use, the launcher asks for the local gateway key and stores it in the
desktop keyring. The key is never written to this project or passed in process
arguments. It is provided to Codex through `PI_AI_GATEWAY_KEY`, and an explicit
Codex environment filter prevents agent-run shell commands from receiving it.

`OAI_GATEWAY_URL`, `OAI_CODE_MODEL`, and `PI_AI_GATEWAY_KEY` may be supplied in
the environment. Environment values take precedence over the config file.

## Test

The offline test uses a fake Codex executable and makes no network requests:

```bash
bash -n oai-code
python3 -m unittest discover -v -s tests
```

## License

[MIT](LICENSE)
