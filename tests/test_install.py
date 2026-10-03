import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load('install_skill', ROOT / 'skills/agent-review/scripts/install_skill.py')
builder = load('build_release', ROOT / 'scripts/build_release.py')


class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='agr-install-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.home = self.directory / "user's home"
        self.root = self.home / '.local/share/sst-agent-review'
        self.urls = []
        self.artifacts = {}
        self.release('v0.1.0')
        download = patch.object(installer, 'download', side_effect=self.download)
        download.start()
        self.addCleanup(download.stop)

    def release(self, version, extra=None):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for name in installer.REQUIRED:
                data = (version + '\n').encode()
                if name == 'scripts/review.py':
                    data = ('print(' + repr(version) + ')\n').encode()
                member = tarfile.TarInfo('agent-review/' + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            if extra:
                archive.addfile(extra, io.BytesIO(b'x') if extra.isfile() else None)
        archive = stream.getvalue()
        manifest = {'version': version, 'sha256': hashlib.sha256(archive).hexdigest()}
        self.artifacts[installer.RELEASES + '/latest/download/release.json'] = json.dumps(manifest).encode()
        self.artifacts[installer.RELEASES + '/download/' + version + '/agent-review.tar.gz'] = archive

    def download(self, url, destination):
        self.urls.append(url)
        destination.write_bytes(self.artifacts[url])

    def installed_script(self, version='v0.1.0'):
        return self.root / 'releases' / version / 'agent-review/scripts/install_skill.py'

    def expired(self):
        old = time.time() - installer.INTERVAL - 1
        os.utime(self.root / 'last-check', (old, old))

    def invoke(self, *args, script=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with patch.object(installer, '__file__', str(script or installer.__file__)):
                status = installer.main(list(args))
        return status, output.getvalue()

    def install(self):
        status, output = self.invoke('--home', str(self.home))
        self.assertEqual(status, 0, output)
        return self.root / 'current/agent-review'

    def test_install_and_repeat_use_one_shared_version_and_two_links(self):
        skill = self.install()
        self.assertEqual(skill.resolve(), self.root / 'releases/v0.1.0/agent-review')
        for link in installer.skill_links(self.home):
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), str(self.root / 'current/agent-review'))
            self.assertEqual(link.resolve(), skill.resolve())
        self.install()
        archive_url = installer.RELEASES + '/download/v0.1.0/agent-review.tar.gz'
        self.assertEqual(self.urls.count(archive_url), 1)
        self.assertEqual(sorted(path.name for path in (self.root / 'releases').iterdir()), ['v0.1.0'])

    def test_update_returns_new_instructions_and_keeps_pinned_old_helper(self):
        pinned = self.install().resolve() / 'scripts/review.py'
        self.expired()
        self.release('v0.1.1')
        status, output = self.invoke('--check', script=self.installed_script())
        self.assertEqual(status, 0, output)
        self.assertIn('Installed v0.1.1. Read its SKILL.md', output)
        self.assertIn(str(self.root / 'releases/v0.1.1/agent-review/SKILL.md'), output)
        self.assertEqual(subprocess.check_output([sys.executable, str(pinned)], text=True).strip(), 'v0.1.0')
        self.assertEqual(subprocess.check_output([sys.executable, str(self.root / 'current/agent-review/scripts/review.py')], text=True).strip(), 'v0.1.1')

    def test_daily_check_uses_current_version_even_when_called_from_an_old_one(self):
        self.install()
        self.release('v0.1.1')
        self.install()
        self.urls.clear()
        status, output = self.invoke('--check', script=self.installed_script())
        self.assertEqual(status, 0, output)
        self.assertIn('within the last 24 hours', output)
        self.assertIn('releases/v0.1.1/agent-review/SKILL.md', output)
        self.assertEqual(self.urls, [])

    def test_manual_install_bypasses_daily_interval(self):
        self.install()
        self.release('v0.1.1')
        self.install()
        self.assertEqual((self.root / 'current').resolve().name, 'v0.1.1')

    def test_failed_download_retains_version_and_prints_manual_command(self):
        original = self.install().resolve()
        self.expired()
        with patch.object(installer, 'download', side_effect=ValueError('Network unavailable')):
            status, output = self.invoke('--check', script=self.installed_script())
        self.assertEqual(status, 0)
        self.assertIn('Network unavailable', output)
        self.assertIn(installer.INSTALL_COMMAND, output)
        self.assertIn('normal terminal outside the agent sandbox', output)
        self.assertIn(str(original / 'SKILL.md'), output)
        self.assertEqual((self.root / 'current/agent-review').resolve(), original)
        self.assertFalse(list(self.root.glob('.download-*')))

    def test_sandbox_denial_reports_the_path_and_keeps_installed_skill(self):
        original = self.install().resolve()
        open_file = Path.open

        def denied(path, *args, **kwargs):
            if path == self.root / 'update.lock':
                raise PermissionError('Sandbox denied: ' + str(path))
            return open_file(path, *args, **kwargs)

        with patch.object(Path, 'open', denied):
            status, output = self.invoke('--check', script=self.installed_script())
        self.assertEqual(status, 0, output)
        self.assertIn('Sandbox denied:', output)
        self.assertIn(installer.INSTALL_COMMAND, output)
        self.assertIn(str(original / 'SKILL.md'), output)
        self.assertEqual((self.root / 'current/agent-review').resolve(), original)

    def test_bad_checksum_and_archive_entries_never_switch_current(self):
        original = self.install().resolve()
        bad_paths = ['../escaped', '/absolute', 'agent-review/../../escaped']
        members = []
        for name in bad_paths:
            member = tarfile.TarInfo(name)
            member.size = 1
            members.append(member)
        link = tarfile.TarInfo('agent-review/linked')
        link.type = tarfile.SYMTYPE
        link.linkname = '/tmp'
        members.append(link)
        for member in members:
            with self.subTest(name=member.name):
                self.release('v0.1.1', member)
                status, output = self.invoke('--home', str(self.home))
                self.assertEqual(status, 1, output)
                self.assertEqual((self.root / 'current/agent-review').resolve(), original)
                self.assertFalse((self.root / 'releases/v0.1.1').exists())
        self.release('v0.1.1')
        self.artifacts[installer.RELEASES + '/download/v0.1.1/agent-review.tar.gz'] = b'broken download'
        status, output = self.invoke('--home', str(self.home))
        self.assertEqual(status, 1)
        self.assertIn('checksum mismatch', output)
        self.assertEqual((self.root / 'current/agent-review').resolve(), original)

    def test_foreign_skill_is_not_overwritten_and_other_link_is_not_created(self):
        existing = self.home / '.claude/skills/sst-agent-review'
        existing.mkdir(parents=True)
        (existing / 'SKILL.md').write_text('my existing skill')
        status, output = self.invoke('--home', str(self.home))
        self.assertEqual(status, 1)
        self.assertIn(str(existing), output)
        self.assertEqual((existing / 'SKILL.md').read_text(), 'my existing skill')
        self.assertFalse((self.home / '.agents/skills/sst-agent-review').is_symlink())
        self.assertFalse((self.root / 'current').is_symlink())
        self.assertEqual(self.urls, [])

    def test_parallel_installer_reports_lock_instead_of_switching_current(self):
        original = self.install().resolve()
        with (self.root / 'update.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status, output = self.invoke('--home', str(self.home))
        self.assertEqual(status, 1, output)
        self.assertEqual((self.root / 'current/agent-review').resolve(), original)

    def test_development_copy_does_not_update_or_create_user_directories(self):
        status, output = self.invoke('--check', '--home', str(self.home))
        self.assertEqual(status, 0)
        self.assertIn('automatic updates are disabled', output)
        self.assertEqual(self.urls, [])
        self.assertFalse(self.home.exists())

    def test_macos_install_uses_the_same_layout(self):
        with patch.object(installer.platform, 'system', return_value='Darwin'):
            self.install()
        self.assertTrue((self.home / '.agents/skills/sst-agent-review/SKILL.md').is_file())
        self.assertTrue((self.home / '.claude/skills/sst-agent-review/SKILL.md').is_file())

    def test_global_preferences_survive_install_and_update(self):
        self.root.mkdir(parents=True)
        config = self.root / 'config.ini'
        config.write_text('[review]\nmodel = sonnet\nruntime = native\n')
        self.install()
        self.release('v0.1.1')
        self.install()
        self.assertEqual(config.read_text(), '[review]\nmodel = sonnet\nruntime = native\n')

    def test_release_assets_are_reproducible_and_contain_only_the_skill(self):
        first = builder.build(ROOT, 'v0.1.0', self.directory / 'first')
        second = builder.build(ROOT, 'v0.1.0', self.directory / 'second')
        self.assertEqual(first, second)
        archive = self.directory / 'first/agent-review.tar.gz'
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), first['sha256'])
        with tarfile.open(archive) as bundle:
            names = bundle.getnames()
        self.assertTrue(all(name.startswith('agent-review/') for name in names))
        self.assertFalse(any('__pycache__' in name or '.agr/' in name for name in names))
        self.assertIn('agent-review/scripts/install_skill.py', names)
        self.assertEqual((self.directory / 'first/install_skill.py').read_bytes(), (ROOT / 'skills/agent-review/scripts/install_skill.py').read_bytes())
        self.artifacts[installer.RELEASES + '/latest/download/release.json'] = (self.directory / 'first/release.json').read_bytes()
        self.artifacts[installer.RELEASES + '/download/v0.1.0/agent-review.tar.gz'] = archive.read_bytes()
        skill = self.install()
        self.assertIn('name: sst-agent-review\n', (skill / 'SKILL.md').read_text())
        helper = self.home / '.agents/skills/sst-agent-review/scripts/review.py'
        repo = self.directory / 'project'
        subprocess.run(['git', 'init', '-q', str(repo)], check=True, capture_output=True)
        preset = self.directory / 'custom-preset'
        for name in ('reviewer/review.md', 'reviewer/policy.md', 'reviewer/followup.md', 'author/discuss.md', 'author/fix.md'):
            path = preset / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Installed custom instructions: ' + name + '\n')
        config = self.root / 'config.ini'
        original = '[review]\npreset = ' + str(preset) + '\n'
        config.write_text(original)
        result = subprocess.run([sys.executable, str(helper), '--repo', str(repo), 'instructions', 'discuss'],
                                env={**os.environ, 'HOME': str(self.home)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Installed custom instructions: author/discuss.md', result.stdout)
        self.assertEqual(config.read_text(), original)

    def test_bootstrap_forwards_arguments_and_cleans_failed_download(self):
        binary = self.directory / 'bin'
        binary.mkdir()
        curl = binary / 'curl'
        curl.write_text('#!' + sys.executable + '\nimport os, pathlib, sys\nif os.environ.get("AGR_FIXTURE_FAIL"): sys.exit(22)\nout = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])\nout.write_text("import sys\\nprint(repr(sys.argv[1:]))\\n")\n')
        curl.chmod(0o755)
        temporary = self.directory / 'bootstrap'
        temporary.mkdir()
        environment = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'], 'TMPDIR': str(temporary)}
        command = ['bash', str(ROOT / 'install.sh'), '--home', str(self.home)]
        result = subprocess.run(command, capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(repr(['--home', str(self.home)]), result.stdout)
        self.assertEqual(list(temporary.iterdir()), [])
        result = subprocess.run(command, capture_output=True, text=True, env={**environment, 'AGR_FIXTURE_FAIL': '1'})
        self.assertEqual(result.returncode, 22, result.stderr)
        self.assertEqual(list(temporary.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
