import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import Pipeline, save


class PortableAPITests(unittest.TestCase):
    def check_platform(self, platform, configured=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root / 'project_state.json', {'topic': 'synthetic', 'stages': {}, 'requests': {}})
            profile = {'tools': {'ffmpeg': 'ffmpeg', 'ffprobe': 'ffprobe', 'curl': configured},
                       'tts': {'key_env': 'STORYDICTOR_SYNTHETIC_TEST_KEY', 'key_file': None,
                               'api_base': 'https://example.invalid'}}
            p = Pipeline(root, profile)
            key = 'synthetic-test-key'

            def fake_run(command, **kwargs):
                self.assertNotIn(key, ' '.join(command))
                self.assertIn(key.encode(), kwargs['input'])
                Path(command[command.index('--output') + 1]).write_text('{}', encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, b'200', b'')

            with patch.dict(os.environ, {'STORYDICTOR_SYNTHETIC_TEST_KEY': key}), \
                 patch('pipeline.sys.platform', platform), patch('pipeline.subprocess.run', side_effect=fake_run) as run:
                p.api('synthetic-read', '/read', paid=False)
            command = run.call_args.args[0]
            self.assertEqual(command[0], configured or ('curl.exe' if platform == 'win32' else 'curl'))
            self.assertEqual('--ssl-revoke-best-effort' in command, platform == 'win32')
            self.assertEqual(p.state['requests']['synthetic-read']['status'], 'completed')

    def test_windows_uses_platform_curl_and_stdin_credentials(self):
        self.check_platform('win32')

    def test_linux_does_not_pass_schannel_only_option(self):
        self.check_platform('linux')

    def test_custom_curl_executable_on_macos(self):
        self.check_platform('darwin', '/custom/curl')


if __name__ == '__main__':
    unittest.main()
