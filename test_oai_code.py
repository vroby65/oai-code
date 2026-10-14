"""Offline launcher test; no gateway or OpenAI request is made."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class OaiCodeTests(unittest.TestCase):
    def test_launcher_uses_gateway_without_putting_key_in_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            capture = temp_path / 'args'
            fake_codex = temp_path / 'codex'
            fake_codex.write_text(
                '#!/bin/bash\nprintf "%s\\n" "$@" > "$CAPTURE_FILE"\n'
            )
            fake_codex.chmod(0o700)
            env = dict(os.environ,
                       PATH=temp + os.pathsep + os.environ['PATH'],
                       CAPTURE_FILE=str(capture),
                       PI_AI_GATEWAY_KEY='offline-secret',
                       OAI_GATEWAY_URL='http://192.168.1.50:18765',
                       OAI_CODE_MODEL='test-model')
            subprocess.run([str(Path(__file__).parent / 'oai-code'), 'exec', 'hello'],
                           env=env, check=True)
            args = capture.read_text().splitlines()
            self.assertIn('model_provider="pi_ai_gateway"', args)
            self.assertIn(
                'model_providers.pi_ai_gateway.base_url="http://192.168.1.50:18765/codex/v1"',
                args,
            )
            self.assertIn('shell_environment_policy.filters.PI_AI_GATEWAY_KEY="exclude"',
                          args)
            self.assertIn('test-model', args)
            self.assertEqual(args[-2:], ['exec', 'hello'])
            self.assertNotIn('offline-secret', '\n'.join(args))


if __name__ == '__main__':
    unittest.main()
