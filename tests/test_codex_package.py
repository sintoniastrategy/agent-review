from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from .support import runtime


@unittest.skipUnless(os.environ.get('AGR_TEST_CODEX_PACKAGE'), 'Set AGR_TEST_CODEX_PACKAGE to an installed pinned package')
class CodexPackageTests(unittest.TestCase):
    def test_real_cli_executes_a_code_mode_command_with_a_local_model_fixture(self):
        package = Path(os.environ['AGR_TEST_CODEX_PACKAGE']).resolve()
        requests = []
        command = {'cmd': 'printf AGR_CODEX_PACKAGE_OK', 'login': False, 'yield_time_ms': 10000}
        events = [
            {'type': 'custom_tool_call', 'call_id': 'package-probe', 'name': 'exec', 'input': 'text(await tools.exec_command(' + json.dumps(command) + '));'},
            {'type': 'message', 'role': 'assistant', 'id': 'done', 'content': [{'type': 'output_text', 'text': 'Package probe complete'}]},
        ]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path != '/v1/responses':
                    self.send_response(404)
                    self.end_headers()
                    return
                requests.append(request)
                if len(requests) > len(events):
                    self.send_response(500)
                    self.end_headers()
                    return
                identifier = 'probe-' + str(len(requests))
                response = [
                    {'type': 'response.created', 'response': {'id': identifier}},
                    {'type': 'response.output_item.done', 'item': events[len(requests) - 1]},
                    {'type': 'response.completed', 'response': {'id': identifier, 'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}},
                ]
                payload = ''.join('data: ' + json.dumps(event) + '\n\n' for event in response).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        with tempfile.TemporaryDirectory(prefix='agr-codex-package-') as directory, ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                home = Path(directory)
                config = home / '.codex'
                config.mkdir()
                (home / 'tmp').mkdir()
                settings = {
                    'model': 'gpt-6.1-sol', 'model_provider': 'package_probe',
                    'model_reasoning_effort': 'medium', 'check_for_update_on_startup': False,
                    'features.code_mode_only': True, 'features.code_mode_host': True,
                    'features.memories': False, 'features.apps': False, 'features.plugins': False,
                    'features.daemon_auto_start': False, 'features.external_migration': False,
                    'features.skip_host_skill_discovery': True, 'project_doc_max_bytes': 0,
                    'analytics.enabled': False, 'otel.exporter': 'none',
                    'otel.trace_exporter': 'none', 'otel.metrics_exporter': 'none',
                    'model_providers.package_probe.name': 'local package fixture',
                    'model_providers.package_probe.base_url': 'http://127.0.0.1:' + str(server.server_port) + '/v1',
                    'model_providers.package_probe.wire_api': 'responses',
                    'model_providers.package_probe.request_max_retries': 0,
                    'model_providers.package_probe.stream_max_retries': 0,
                }
                (config / 'config.toml').write_text(''.join(key + ' = ' + json.dumps(value) + '\n' for key, value in settings.items()))
                environment = runtime.environment(home, home, agent='codex')
                result = subprocess.run([
                    str(package / 'bin/codex'), '--no-daemon', 'exec', '--ephemeral', '--skip-git-repo-check',
                    '--dangerously-bypass-approvals-and-sandbox', 'Run the package probe.',
                ], cwd=home, env=environment, capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(requests), 2, result.stderr)
                outputs = [item for item in requests[1]['input'] if item.get('type') == 'custom_tool_call_output' and item.get('call_id') == 'package-probe']
                self.assertEqual(len(outputs), 1, result.stderr)
                output = outputs[0]['output']
                if isinstance(output, list):
                    output = '\n'.join(item.get('text', '') for item in output)
                self.assertIn('AGR_CODEX_PACKAGE_OK', output)
                self.assertIn('"exit_code":0', output.replace(' ', ''))
                self.assertIn('Package probe complete', result.stdout)
            finally:
                server.shutdown()
                thread.join(timeout=5)
