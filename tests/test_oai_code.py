"""Offline launcher and protocol tests; no real gateway request is made."""
import importlib.util
import os
import pty
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
PROXY_PATH = ROOT / 'proxy.py'


def load_proxy():
    spec = importlib.util.spec_from_file_location('oai_code_proxy', PROXY_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OaiCodeTests(unittest.TestCase):
    def test_launcher_starts_local_proxy_then_claude_without_sharing_gateway_key(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            claude_capture = temp_path / 'claude-capture'
            proxy_capture = temp_path / 'proxy-capture'
            fake_claude = temp_path / 'claude'
            fake_claude.write_text(
                '#!/bin/bash\n'
                '{\n'
                '  printf "base=%s\\n" "$ANTHROPIC_BASE_URL"\n'
                '  printf "auth=%s\\n" "$ANTHROPIC_AUTH_TOKEN"\n'
                '  printf "gateway_key=%s\\n" "${PI_AI_GATEWAY_KEY:-}"\n'
                '  printf "arg=%s\\n" "$@"\n'
                '} > "$CLAUDE_CAPTURE"\n'
            )
            fake_claude.chmod(0o700)
            fake_python = temp_path / 'python3'
            fake_python.write_text(
                '#!/bin/bash\n'
                'printf "gateway_key=%s\\n" "$PI_AI_GATEWAY_KEY" > "$PROXY_CAPTURE"\n'
                'printf "arg=%s\\n" "$@" >> "$PROXY_CAPTURE"\n'
                'while [ "$#" -gt 0 ]; do\n'
                '  if [ "$1" = --port-file ]; then printf "32123\\n" > "$2"; fi\n'
                '  shift\n'
                'done\n'
                'trap "exit 0" TERM INT\n'
                'while :; do sleep 1; done\n'
            )
            fake_python.chmod(0o700)
            env = dict(os.environ,
                       PATH=temp + os.pathsep + os.environ['PATH'],
                       CLAUDE_CAPTURE=str(claude_capture),
                       PROXY_CAPTURE=str(proxy_capture),
                       PI_AI_GATEWAY_KEY='offline-secret',
                       OAI_GATEWAY_URL='http://192.168.1.50:18765',
                       OAI_CODE_MODEL='gpt-test',
                       OAI_CODE_REASONING_EFFORT='high')
            subprocess.run([str(ROOT / 'oai-code'), '-p', 'hello'],
                           env=env, check=True)
            claude = claude_capture.read_text()
            proxy = proxy_capture.read_text()
            self.assertIn('base=http://127.0.0.1:32123', claude)
            self.assertRegex(claude, r'auth=[0-9a-f-]{36}')
            self.assertIn('gateway_key=\n', claude)
            self.assertIn('arg=--model\narg=gpt-test', claude)
            self.assertIn('arg=--effort\narg=high', claude)
            self.assertTrue(claude.endswith('arg=-p\narg=hello\n'))
            self.assertIn('gateway_key=offline-secret', proxy)
            self.assertIn('arg=--gateway-url\narg=http://192.168.1.50:18765', proxy)
            self.assertNotIn('offline-secret', '\n'.join(proxy.splitlines()[1:]))

    def test_interactive_launcher_selects_model_and_effort_before_claude(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            claude_capture = temp_path / 'claude-capture'
            proxy_capture = temp_path / 'proxy-capture'
            fake_claude = temp_path / 'claude'
            fake_claude.write_text(
                '#!/bin/bash\nprintf "%s\\n" "$@" > "$CLAUDE_CAPTURE"\n'
            )
            fake_claude.chmod(0o700)
            fake_python = temp_path / 'python3'
            fake_python.write_text(
                '#!/bin/bash\n'
                'printf "%s\\n" "$@" > "$PROXY_CAPTURE"\n'
                'while [ "$#" -gt 0 ]; do\n'
                '  if [ "$1" = --port-file ]; then printf "32124\\n" > "$2"; fi\n'
                '  shift\n'
                'done\n'
                'trap "exit 0" TERM INT\n'
                'while :; do sleep 1; done\n'
            )
            fake_python.chmod(0o700)
            fake_curl = temp_path / 'curl'
            fake_curl.write_text(
                '#!/bin/bash\n'
                'cat >/dev/null\n'
                "printf '%s\\n' '{\"models\":["
                "{\"slug\":\"gpt-first\",\"display_name\":\"GPT First\",\"visibility\":\"list\"},"
                "{\"slug\":\"gpt-choice\",\"display_name\":\"GPT Choice\",\"visibility\":\"list\"}]}'\n"
            )
            fake_curl.chmod(0o700)
            fake_fzf = temp_path / 'fzf'
            fake_fzf.write_text(
                '#!/bin/bash\n'
                'case "$*" in\n'
                '  *"Model > "*) awk -F "\\t" \'$1 == "gpt-choice" { print; exit }\' ;;\n'
                '  *"Reasoning > "*) awk -F "\\t" \'$1 == "xhigh" { print; exit }\' ;;\n'
                'esac\n'
            )
            fake_fzf.chmod(0o700)
            env = dict(os.environ,
                       PATH=temp + os.pathsep + os.environ['PATH'],
                       CLAUDE_CAPTURE=str(claude_capture),
                       PROXY_CAPTURE=str(proxy_capture),
                       PI_AI_GATEWAY_KEY='offline-secret',
                       OAI_GATEWAY_URL='http://192.168.1.50:18765')
            master, slave = pty.openpty()
            try:
                process = subprocess.Popen(
                    [str(ROOT / 'oai-code')], env=env,
                    stdin=slave, stdout=slave, stderr=slave,
                )
                os.close(slave)
                slave = None
                output = bytearray()
                while True:
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                self.assertEqual(process.wait(), 0, output.decode(errors='replace'))
            finally:
                os.close(master)
                if slave is not None:
                    os.close(slave)
            claude_args = claude_capture.read_text().splitlines()
            proxy_args = proxy_capture.read_text().splitlines()
            self.assertEqual(claude_args[:4], ['--model', 'gpt-choice', '--effort', 'xhigh'])
            self.assertIn('gpt-choice', proxy_args)
            self.assertIn('xhigh', proxy_args)


class ProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proxy = load_proxy()

    def test_translates_messages_tools_and_selected_reasoning(self):
        body = {
            'model': 'ignored-claude-name',
            'max_tokens': 4096,
            'system': [{'type': 'text', 'text': 'Work carefully.'}],
            'messages': [
                {'role': 'user', 'content': 'Inspect the project.'},
                {'role': 'assistant', 'content': [
                    {'type': 'text', 'text': 'I will inspect it.'},
                    {'type': 'tool_use', 'id': 'toolu_1', 'name': 'Read',
                     'input': {'file_path': '/tmp/a'}},
                ]},
                {'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': 'toolu_1',
                     'content': 'file contents'},
                ]},
            ],
            'tools': [{
                'name': 'Read', 'description': 'Read a file',
                'input_schema': {'type': 'object', 'properties': {
                    'file_path': {'type': 'string'},
                }},
            }],
            'tool_choice': {'type': 'auto'},
        }
        translated = self.proxy.anthropic_to_responses(body, 'gpt-test', 'high')
        self.assertEqual(translated['model'], 'gpt-test')
        self.assertEqual(translated['instructions'], 'Work carefully.')
        self.assertEqual(translated['max_output_tokens'], 4096)
        self.assertEqual(translated['reasoning'], {'effort': 'high'})
        self.assertEqual(translated['tools'][0]['name'], 'Read')
        self.assertEqual(translated['tool_choice'], 'auto')
        self.assertEqual(translated['input'][0]['role'], 'user')
        self.assertEqual(translated['input'][1]['role'], 'assistant')
        self.assertEqual(translated['input'][2], {
            'type': 'function_call', 'call_id': 'toolu_1', 'name': 'Read',
            'arguments': '{"file_path":"/tmp/a"}',
        })
        self.assertEqual(translated['input'][3], {
            'type': 'function_call_output', 'call_id': 'toolu_1',
            'output': 'file contents',
        })

    def test_translates_responses_stream_with_text_and_tool_call(self):
        stream = self.proxy.AnthropicStream('gpt-test')
        events = [stream.message_start()]
        upstream = [
            {'type': 'response.output_text.delta', 'output_index': 0,
             'content_index': 0, 'delta': 'Checking.'},
            {'type': 'response.output_item.done', 'output_index': 0,
             'item': {'type': 'message'}},
            {'type': 'response.output_item.added', 'output_index': 1,
             'item': {'type': 'function_call', 'call_id': 'call_1',
                      'name': 'Read', 'arguments': ''}},
            {'type': 'response.function_call_arguments.delta', 'output_index': 1,
             'delta': '{"file_path":"README.md"}'},
            {'type': 'response.output_item.done', 'output_index': 1,
             'item': {'type': 'function_call', 'call_id': 'call_1',
                      'name': 'Read', 'arguments': '{"file_path":"README.md"}'}},
            {'type': 'response.completed', 'response': {
                'usage': {'input_tokens': 25, 'output_tokens': 12},
            }},
        ]
        for event in upstream:
            events.extend(stream.feed(event))
        names = [name for name, _ in events]
        self.assertEqual(names, [
            'message_start',
            'content_block_start', 'content_block_delta', 'content_block_stop',
            'content_block_start', 'content_block_delta', 'content_block_stop',
            'message_delta', 'message_stop',
        ])
        tool_start = events[4][1]['content_block']
        self.assertEqual(tool_start, {
            'type': 'tool_use', 'id': 'call_1', 'name': 'Read', 'input': {},
        })
        self.assertEqual(events[-2][1]['delta']['stop_reason'], 'tool_use')
        self.assertEqual(events[-2][1]['usage']['output_tokens'], 12)
        self.assertEqual(stream.final_message()['content'], [
            {'type': 'text', 'text': 'Checking.'},
            {'type': 'tool_use', 'id': 'call_1', 'name': 'Read',
             'input': {'file_path': 'README.md'}},
        ])


if __name__ == '__main__':
    unittest.main()
