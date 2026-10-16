#!/usr/bin/env python3
"""Local Anthropic Messages to OpenAI Responses bridge for Claude Code."""
import argparse
import hmac
import json
import os
import secrets
import signal
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


def compact_json(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


def text_from_content(content):
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return compact_json(content)
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get('text'), str):
            parts.append(block['text'])
    return '\n'.join(parts)


def system_text(system):
    return text_from_content(system)


def message_item(role, blocks):
    content_type = 'output_text' if role == 'assistant' else 'input_text'
    content = []
    for block in blocks:
        if block['type'] == 'text':
            content.append({'type': content_type, 'text': block['text']})
        else:
            content.append(block)
    return {'type': 'message', 'role': role, 'content': content}


def anthropic_to_responses(body, model, effort):
    """Translate the Claude Messages request subset used by Claude Code."""
    input_items = []
    instructions = []
    top_system = system_text(body.get('system'))
    if top_system:
        instructions.append(top_system)

    for message in body.get('messages', []):
        role = message.get('role')
        content = message.get('content', '')
        if role == 'system':
            text = text_from_content(content)
            if text:
                instructions.append(text)
            continue
        if role not in ('user', 'assistant'):
            continue
        if isinstance(content, str):
            if content:
                input_items.append(message_item(role, [{'type': 'text', 'text': content}]))
            continue

        pending = []

        def flush_pending():
            if pending:
                input_items.append(message_item(role, list(pending)))
                pending.clear()

        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            block_type = block.get('type')
            if block_type == 'text' and isinstance(block.get('text'), str):
                pending.append({'type': 'text', 'text': block['text']})
            elif block_type == 'image' and role == 'user':
                source = block.get('source', {})
                if source.get('type') == 'base64' and source.get('media_type') and source.get('data'):
                    pending.append({
                        'type': 'input_image',
                        'image_url': f"data:{source['media_type']};base64,{source['data']}",
                    })
                elif source.get('type') == 'url' and source.get('url'):
                    pending.append({'type': 'input_image', 'image_url': source['url']})
            elif block_type == 'tool_use' and role == 'assistant':
                flush_pending()
                input_items.append({
                    'type': 'function_call',
                    'call_id': str(block.get('id', '')),
                    'name': str(block.get('name', '')),
                    'arguments': compact_json(block.get('input', {})),
                })
            elif block_type == 'tool_result' and role == 'user':
                flush_pending()
                output = text_from_content(block.get('content'))
                if block.get('is_error'):
                    output = 'Tool error: ' + output
                input_items.append({
                    'type': 'function_call_output',
                    'call_id': str(block.get('tool_use_id', '')),
                    'output': output,
                })
        flush_pending()

    translated = {
        'model': model,
        'input': input_items,
        'store': False,
        'stream': True,
        'reasoning': {'effort': effort},
    }
    if instructions:
        translated['instructions'] = '\n\n'.join(instructions)
    max_tokens = body.get('max_tokens')
    if isinstance(max_tokens, int) and max_tokens > 0:
        translated['max_output_tokens'] = max_tokens
    tools = []
    for tool in body.get('tools', []):
        if not isinstance(tool, dict) or not tool.get('name'):
            continue
        converted = {
            'type': 'function',
            'name': tool['name'],
            'parameters': tool.get('input_schema') or {'type': 'object', 'properties': {}},
        }
        if tool.get('description'):
            converted['description'] = tool['description']
        tools.append(converted)
    if tools:
        translated['tools'] = tools
        choice = body.get('tool_choice') or {}
        choice_type = choice.get('type')
        if choice_type == 'any':
            translated['tool_choice'] = 'required'
        elif choice_type == 'tool' and choice.get('name'):
            translated['tool_choice'] = {'type': 'function', 'name': choice['name']}
        elif choice_type in ('auto', 'none'):
            translated['tool_choice'] = choice_type
    return translated


class AnthropicStream:
    """Convert Responses SSE events into an Anthropic Messages event stream."""

    def __init__(self, model):
        self.model = model
        self.message_id = 'msg_' + secrets.token_hex(12)
        self.blocks = {}
        self.next_index = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.had_tool = False
        self.stop_reason = None
        self.finished = False

    def message_start(self):
        return 'message_start', {
            'type': 'message_start',
            'message': {
                'id': self.message_id,
                'type': 'message',
                'role': 'assistant',
                'content': [],
                'model': self.model,
                'stop_reason': None,
                'stop_sequence': None,
                'usage': {'input_tokens': 0, 'output_tokens': 0},
            },
        }

    def _new_block(self, key, block):
        state = dict(block, index=self.next_index, closed=False)
        self.next_index += 1
        self.blocks[key] = state
        if state['kind'] == 'text':
            content = {'type': 'text', 'text': ''}
        else:
            content = {
                'type': 'tool_use', 'id': state['id'],
                'name': state['name'], 'input': {},
            }
        return state, ('content_block_start', {
            'type': 'content_block_start',
            'index': state['index'],
            'content_block': content,
        })

    def _text_block(self, event):
        key = ('text', event.get('output_index', 0), event.get('content_index', 0))
        state = self.blocks.get(key)
        start = None
        if state is None:
            state, start = self._new_block(key, {
                'kind': 'text', 'text': '',
                'output_index': event.get('output_index', 0),
            })
        return state, start

    def _tool_block(self, event, item=None):
        output_index = event.get('output_index', 0)
        key = ('tool', output_index)
        state = self.blocks.get(key)
        start = None
        item = item or event.get('item') or {}
        if state is None:
            state, start = self._new_block(key, {
                'kind': 'tool',
                'id': str(item.get('call_id') or item.get('id') or 'call_' + secrets.token_hex(8)),
                'name': str(item.get('name', '')),
                'arguments': '',
                'output_index': output_index,
            })
            self.had_tool = True
        return state, start

    def _close(self, state):
        if state['closed']:
            return None
        state['closed'] = True
        return 'content_block_stop', {
            'type': 'content_block_stop', 'index': state['index'],
        }

    def _finish(self, stop_reason, usage=None):
        events = []
        for state in self.blocks.values():
            close = self._close(state)
            if close:
                events.append(close)
        usage = usage or {}
        self.input_tokens = int(usage.get('input_tokens') or self.input_tokens)
        self.output_tokens = int(usage.get('output_tokens') or self.output_tokens)
        self.stop_reason = 'tool_use' if self.had_tool else stop_reason
        self.finished = True
        events.extend([
            ('message_delta', {
                'type': 'message_delta',
                'delta': {'stop_reason': self.stop_reason, 'stop_sequence': None},
                'usage': {'output_tokens': self.output_tokens},
            }),
            ('message_stop', {'type': 'message_stop'}),
        ])
        return events

    def feed(self, event):
        event_type = event.get('type')
        events = []
        if event_type in ('response.output_text.delta', 'response.refusal.delta'):
            state, start = self._text_block(event)
            if start:
                events.append(start)
            delta = event.get('delta', '')
            if delta:
                state['text'] += delta
                events.append(('content_block_delta', {
                    'type': 'content_block_delta', 'index': state['index'],
                    'delta': {'type': 'text_delta', 'text': delta},
                }))
        elif event_type == 'response.output_item.added':
            item = event.get('item') or {}
            if item.get('type') == 'function_call':
                state, start = self._tool_block(event, item)
                if start:
                    events.append(start)
                initial = item.get('arguments') or ''
                if initial:
                    state['arguments'] += initial
                    events.append(('content_block_delta', {
                        'type': 'content_block_delta', 'index': state['index'],
                        'delta': {'type': 'input_json_delta', 'partial_json': initial},
                    }))
        elif event_type == 'response.function_call_arguments.delta':
            state, start = self._tool_block(event)
            if start:
                events.append(start)
            delta = event.get('delta', '')
            if delta:
                state['arguments'] += delta
                events.append(('content_block_delta', {
                    'type': 'content_block_delta', 'index': state['index'],
                    'delta': {'type': 'input_json_delta', 'partial_json': delta},
                }))
        elif event_type == 'response.output_item.done':
            item = event.get('item') or {}
            output_index = event.get('output_index', 0)
            if item.get('type') == 'function_call':
                state, start = self._tool_block(event, item)
                if start:
                    events.append(start)
                final_arguments = item.get('arguments') or ''
                if final_arguments.startswith(state['arguments']):
                    remainder = final_arguments[len(state['arguments']):]
                    if remainder:
                        state['arguments'] += remainder
                        events.append(('content_block_delta', {
                            'type': 'content_block_delta', 'index': state['index'],
                            'delta': {'type': 'input_json_delta', 'partial_json': remainder},
                        }))
                elif not state['arguments'] and final_arguments:
                    state['arguments'] = final_arguments
                close = self._close(state)
                if close:
                    events.append(close)
            elif item.get('type') == 'message':
                for state in self.blocks.values():
                    if state['output_index'] == output_index:
                        close = self._close(state)
                        if close:
                            events.append(close)
        elif event_type == 'response.completed':
            events.extend(self._finish('end_turn', (event.get('response') or {}).get('usage')))
        elif event_type == 'response.incomplete':
            events.extend(self._finish('max_tokens', (event.get('response') or {}).get('usage')))
        elif event_type in ('response.failed', 'error'):
            error = event.get('error') or (event.get('response') or {}).get('error') or {}
            self.finished = True
            events.append(('error', {
                'type': 'error',
                'error': {
                    'type': 'api_error',
                    'message': error.get('message') or 'Upstream response failed',
                },
            }))
        return events

    def final_message(self):
        content = []
        for state in sorted(self.blocks.values(), key=lambda item: item['index']):
            if state['kind'] == 'text':
                if state['text']:
                    content.append({'type': 'text', 'text': state['text']})
            else:
                try:
                    tool_input = json.loads(state['arguments'] or '{}')
                except json.JSONDecodeError:
                    tool_input = {}
                content.append({
                    'type': 'tool_use', 'id': state['id'],
                    'name': state['name'], 'input': tool_input,
                })
        return {
            'id': self.message_id,
            'type': 'message',
            'role': 'assistant',
            'model': self.model,
            'content': content,
            'stop_reason': self.stop_reason or ('tool_use' if self.had_tool else 'end_turn'),
            'stop_sequence': None,
            'usage': {
                'input_tokens': self.input_tokens,
                'output_tokens': self.output_tokens,
            },
        }


def iter_sse(response):
    data_lines = []
    for raw_line in response:
        line = raw_line.decode('utf-8', errors='replace').rstrip('\r\n')
        if not line:
            if data_lines:
                payload = '\n'.join(data_lines)
                data_lines.clear()
                if payload != '[DONE]':
                    yield json.loads(payload)
            continue
        if line.startswith('data:'):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        payload = '\n'.join(data_lines)
        if payload != '[DONE]':
            yield json.loads(payload)


class ProxyHandler(BaseHTTPRequestHandler):
    gateway_url = ''
    gateway_key = ''
    local_token = ''
    model = ''
    effort = 'medium'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def log_message(self, *_args):
        pass

    def _authorized(self):
        authorization = self.headers.get('Authorization', '')
        x_api_key = self.headers.get('x-api-key', '')
        candidates = [x_api_key]
        if authorization.startswith('Bearer '):
            candidates.append(authorization[7:])
        return any(value and hmac.compare_digest(value, self.local_token) for value in candidates)

    def _json(self, status, value):
        payload = compact_json(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status, message):
        error_type = 'authentication_error' if status == 401 else 'api_error'
        self._json(status, {'type': 'error', 'error': {'type': error_type, 'message': message}})

    def _body(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size < 1 or size > 8 * 1024 * 1024:
            raise ValueError('Invalid request size')
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError('JSON body must be an object')
        return value

    def do_HEAD(self):
        if urlparse(self.path).path != '/api/hello':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        if not self._authorized():
            self._error(401, 'Invalid local proxy token')
            return
        if urlparse(self.path).path != '/v1/models':
            self._error(404, 'Not found')
            return
        model = {
            'type': 'model', 'id': self.model, 'display_name': self.model,
            'created_at': '2026-01-01T00:00:00Z',
        }
        self._json(200, {
            'data': [model], 'has_more': False,
            'first_id': self.model, 'last_id': self.model,
        })

    def _upstream(self, body):
        translated = anthropic_to_responses(body, self.model, self.effort)
        request = urllib.request.Request(
            self.gateway_url + '/codex/v1/responses',
            data=compact_json(translated).encode(),
            headers={
                'Authorization': 'Bearer ' + self.gateway_key,
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        return self.opener.open(request, timeout=360)

    def do_POST(self):
        if not self._authorized():
            self._error(401, 'Invalid local proxy token')
            return
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == '/v1/messages/count_tokens':
                estimate = max(1, len(compact_json(body)) // 4)
                self._json(200, {'input_tokens': estimate})
                return
            if path != '/v1/messages':
                self._error(404, 'Not found')
                return
            upstream = self._upstream(body)
        except (ValueError, json.JSONDecodeError) as error:
            self._error(400, str(error))
            return
        except urllib.error.HTTPError as error:
            details = error.read(4096).decode('utf-8', errors='replace')
            self._error(error.code, details or 'Gateway request failed')
            return
        except (urllib.error.URLError, TimeoutError) as error:
            self._error(502, str(error))
            return

        translator = AnthropicStream(self.model)
        if body.get('stream'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()

            def write_event(name, value):
                self.wfile.write(f'event: {name}\n'.encode())
                self.wfile.write(('data: ' + compact_json(value) + '\n\n').encode())
                self.wfile.flush()

            try:
                write_event(*translator.message_start())
                with upstream:
                    for event in iter_sse(upstream):
                        for translated_event in translator.feed(event):
                            write_event(*translated_event)
                if not translator.finished:
                    write_event('error', {
                        'type': 'error',
                        'error': {'type': 'api_error', 'message': 'Upstream stream ended early'},
                    })
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                try:
                    write_event('error', {
                        'type': 'error',
                        'error': {'type': 'api_error', 'message': str(error)},
                    })
                except (BrokenPipeError, ConnectionResetError):
                    pass
            self.close_connection = True
            return

        try:
            with upstream:
                for event in iter_sse(upstream):
                    translator.feed(event)
            if not translator.finished:
                self._error(502, 'Upstream stream ended early')
                return
            self._json(200, translator.final_message())
        except Exception as error:
            self._error(502, str(error))


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def write_port(path, port):
    target = Path(path)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as port_file:
        port_file.write(str(port) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gateway-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--reasoning-effort', required=True,
                        choices=['low', 'medium', 'high', 'xhigh', 'max'])
    parser.add_argument('--port-file', required=True)
    args = parser.parse_args()
    gateway_key = os.environ.get('PI_AI_GATEWAY_KEY', '')
    local_token = os.environ.get('OAI_CODE_LOCAL_TOKEN', '')
    if not gateway_key or not local_token:
        parser.error('Gateway key and local proxy token are required')
    ProxyHandler.gateway_url = args.gateway_url.rstrip('/')
    ProxyHandler.gateway_key = gateway_key
    ProxyHandler.local_token = local_token
    ProxyHandler.model = args.model
    ProxyHandler.effort = args.reasoning_effort
    server = ProxyServer(('127.0.0.1', 0), ProxyHandler)
    write_port(args.port_file, server.server_port)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        Path(args.port_file).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
