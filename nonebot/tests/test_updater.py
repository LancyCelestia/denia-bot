"""受控 Git 更新：只做快进，不碰未跟踪的本地文件。"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot_updater import BotUpdater, UpdateError


def git(cwd, *args):
    result = subprocess.run(
        ['git', *args], cwd=str(cwd), capture_output=True, text=True,
    )
    if result.returncode:
        raise AssertionError(f'git {" ".join(args)} failed: {result.stderr or result.stdout}')
    return result.stdout.strip()


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.remote = root / 'remote.git'
        self.seed = root / 'seed'
        self.work = root / 'work'
        git(root, 'init', '--bare', '--initial-branch=main', str(self.remote))
        git(root, 'clone', str(self.remote), str(self.seed))
        git(self.seed, 'config', 'user.email', 'test@example.invalid')
        git(self.seed, 'config', 'user.name', 'Test')
        (self.seed / 'README.md').write_text('v1\n', encoding='utf-8')
        (self.seed / 'requirements.lock').write_text('a==1\n', encoding='utf-8')
        git(self.seed, 'add', '.')
        git(self.seed, 'commit', '-m', 'v1')
        git(self.seed, 'push', 'origin', 'HEAD:main')
        git(root, 'clone', str(self.remote), str(self.work))
        git(self.work, 'checkout', 'main')
        git(self.work, 'branch', '--set-upstream-to=origin/main', 'main')
        git(self.work, 'config', 'user.email', 'test@example.invalid')
        git(self.work, 'config', 'user.name', 'Test')
        self.updater = BotUpdater(self.work)

    def tearDown(self):
        self.temp.cleanup()

    def publish(self, content='v2\n'):
        (self.seed / 'README.md').write_text(content, encoding='utf-8')
        (self.seed / 'requirements.lock').write_text('a==2\n', encoding='utf-8')
        git(self.seed, 'add', '.')
        git(self.seed, 'commit', '-m', 'v2')
        git(self.seed, 'push', 'origin', 'main')

    def test_status_is_clean_and_current(self):
        status = self.updater.status()
        self.assertTrue(status['available'])
        self.assertEqual(status['branch'], 'main')
        self.assertFalse(status['dirty'])
        self.assertEqual(status['upstream'], 'origin/main')

    def test_check_and_fast_forward_apply(self):
        self.publish()
        checked = self.updater.check()
        self.assertEqual(checked['behind'], 1)
        self.assertTrue(checked['requirements_changed'])
        self.assertTrue(checked['can_apply'])
        applied = self.updater.apply()
        self.assertTrue(applied['updated'])
        self.assertEqual((self.work / 'README.md').read_text(encoding='utf-8'), 'v2\n')
        self.assertFalse((self.work / 'requirements.lock').read_text(encoding='utf-8') == 'a==1\n')
        self.assertEqual(self.updater.status()['head'], applied['head'])

    def test_apply_refuses_dirty_tracked_worktree(self):
        self.publish()
        (self.work / 'README.md').write_text('local\n', encoding='utf-8')
        with self.assertRaises(UpdateError) as raised:
            self.updater.apply()
        self.assertEqual(raised.exception.code, 'LOCAL_CHANGES')

    def test_apply_allows_untracked_local_files(self):
        self.publish()
        (self.work / 'diagnostic.py').write_text('local\n', encoding='utf-8')
        self.assertTrue(self.updater.check()['can_apply'])
        self.assertTrue(self.updater.apply()['updated'])
        self.assertTrue((self.work / 'diagnostic.py').exists())

    def test_no_update_returns_unchanged(self):
        result = self.updater.apply()
        self.assertFalse(result['updated'])

    def test_missing_checkout_is_unavailable(self):
        updater = BotUpdater(Path(self.temp.name) / 'missing')
        self.assertFalse(updater.status()['available'])


if __name__ == '__main__':
    unittest.main()
