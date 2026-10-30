"""Offline installer regressions. All writes stay inside temporary directories."""

import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'skill-hermes' / 'scripts' / 'install.py'
spec = importlib.util.spec_from_file_location('hermes_install', HELPER)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class HermesInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='talk-normal tests ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project with spaces'
        self.project.mkdir()
        self.profile = self.base / 'hermes profile'
        self.env = {**os.environ, 'HERMES_HOME': str(self.profile),
                    'OPENCLAW_WORKSPACE': str(self.base / 'unrelated')}

    def run_installer(self, *args, ok=True, helper=HELPER, cwd=None):
        result = subprocess.run([sys.executable, str(helper), *map(str, args)],
                                cwd=cwd or self.project, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        return result

    def make_git(self):
        (self.project / '.git').mkdir()
        nested = self.project / 'packages' / 'app'
        nested.mkdir(parents=True)
        return nested

    def test_project_install_uses_selected_workspace_from_unrelated_cwd(self):
        self.run_installer('--workspace', self.project, cwd=self.base)
        target = self.project / 'AGENTS.md'
        self.assertIn(installer.BEGIN, target.read_text())
        self.assertFalse((self.base / 'AGENTS.md').exists())
        self.assertFalse(self.profile.exists())

    def test_existing_openclaw_workspace_is_untouched(self):
        unrelated = self.base / 'unrelated'
        unrelated.mkdir()
        (unrelated / 'AGENTS.md').write_text('OpenClaw rules\n')
        self.run_installer()
        self.assertEqual((unrelated / 'AGENTS.md').read_text(), 'OpenClaw rules\n')
        self.assertTrue((self.project / 'AGENTS.md').is_file())

    def test_nested_workspace_updates_git_root_context(self):
        nested = self.make_git()
        target = self.project / 'AGENTS.md'
        target.write_text('Root instructions\n')
        self.run_installer(cwd=nested)
        self.assertTrue(target.read_text().startswith('Root instructions\n'))
        self.assertIn(installer.BEGIN, target.read_text())
        self.assertFalse((nested / 'AGENTS.md').exists())

    def test_fresh_git_project_creates_context_at_git_root(self):
        nested = self.make_git()
        self.run_installer(cwd=nested)
        self.assertTrue((self.project / 'AGENTS.md').is_file())
        self.assertFalse((nested / 'AGENTS.md').exists())

    def test_hermes_context_overrides_nearer_agents_file(self):
        nested = self.make_git()
        target = self.project / 'HERMES.md'
        target.write_text('Hermes rules\n')
        (nested / 'AGENTS.md').write_text('Local rules\n')
        self.run_installer(cwd=nested)
        self.assertIn(installer.BEGIN, target.read_text())
        self.assertEqual((nested / 'AGENTS.md').read_text(), 'Local rules\n')

    def test_nearest_hermes_context_wins(self):
        nested = self.make_git()
        (self.project / '.hermes.md').write_text('Root rules\n')
        (nested / 'HERMES.md').write_text('Local rules\n')
        self.run_installer(cwd=nested)
        self.assertIn(installer.BEGIN, (nested / 'HERMES.md').read_text())
        self.assertEqual((self.project / '.hermes.md').read_text(), 'Root rules\n')

    def test_git_worktree_file_is_a_discovery_boundary(self):
        (self.project / '.git').write_text('gitdir: /unused/worktree\n')
        nested = self.project / 'nested'
        nested.mkdir()
        self.run_installer(cwd=nested)
        self.assertTrue((self.project / 'AGENTS.md').is_file())

    def test_non_git_workspace_stays_inside_selected_directory(self):
        parent = self.base / '.hermes.md'
        parent.write_text('Unrelated parent rules\n')
        self.run_installer()
        self.assertEqual(parent.read_text(), 'Unrelated parent rules\n')
        self.assertTrue((self.project / 'AGENTS.md').is_file())

    def test_nested_git_boundary_blocks_outer_config(self):
        (self.base / '.git').mkdir()
        (self.base / '.hermes.md').write_text('Outer repo\n')
        self.make_git()
        self.run_installer()
        self.assertEqual((self.base / '.hermes.md').read_text(), 'Outer repo\n')
        self.assertTrue((self.project / 'AGENTS.md').is_file())

    def test_context_priority_and_fallback_names(self):
        for name in ('AGENTS.override.md', 'AGENTS.md', 'agents.md',
                     'CLAUDE.md', 'claude.md', '.cursorrules', '.hermes.md', 'HERMES.md'):
            with self.subTest(name=name):
                target = self.project / name
                target.write_text('Existing rules\n')
                self.run_installer()
                self.assertIn(installer.BEGIN, target.read_text())
                target.unlink()

    def test_override_file_preserves_committed_agents(self):
        (self.project / 'AGENTS.override.md').write_text('Personal rules\n')
        (self.project / 'AGENTS.md').write_text('Committed rules\n')
        self.run_installer()
        self.assertIn(installer.BEGIN, (self.project / 'AGENTS.override.md').read_text())
        self.assertEqual((self.project / 'AGENTS.md').read_text(), 'Committed rules\n')

    def test_empty_priority_files_preserve_active_agents_context(self):
        for name in ('.hermes.md', 'HERMES.md', 'AGENTS.override.md'):
            with self.subTest(name=name):
                higher = self.project / name
                higher.write_text('  \n')
                target = self.project / 'AGENTS.md'
                target.write_text('Active project rules\n')
                self.run_installer()
                self.assertEqual(higher.read_text(), '  \n')
                self.assertIn(installer.BEGIN, target.read_text())
                higher.unlink()

    def test_empty_nearest_hermes_file_stops_hermes_ancestor_search(self):
        nested = self.make_git()
        root_context = self.project / 'HERMES.md'
        root_context.write_text('Root Hermes rules\n')
        (nested / '.hermes.md').write_text('')
        target = nested / 'AGENTS.md'
        target.write_text('Active local rules\n')
        self.run_installer(cwd=nested)
        self.assertIn(installer.BEGIN, target.read_text())
        self.assertEqual(root_context.read_text(), 'Root Hermes rules\n')

    def test_global_scope_uses_profile_soul(self):
        self.profile.mkdir()
        target = self.profile / 'SOUL.md'
        target.write_text('Existing identity\n')
        self.run_installer('--global')
        self.assertTrue(target.read_text().startswith('Existing identity\n'))
        self.assertIn(installer.BEGIN, target.read_text())
        self.assertFalse((self.project / 'AGENTS.md').exists())
        self.run_installer('--global', '--uninstall')
        self.assertEqual(target.read_text(), 'Existing identity\n')

    def test_global_default_uses_default_hermes_home(self):
        with mock.patch.dict(os.environ, {'HERMES_HOME': ''}), \
                mock.patch.object(installer.Path, 'home', return_value=self.base), \
                mock.patch('sys.stdout'):
            self.assertEqual(installer.main(['--global']), 0)
        self.assertTrue((self.base / '.hermes' / 'SOUL.md').is_file())

    def test_exact_target_update_idempotence_and_removal(self):
        target = self.base / 'custom file.md'
        target.write_text('Before\n' + installer.BEGIN + '\nOld rules\n' + installer.END + '\nAfter\n')
        self.run_installer('--target', target)
        first = target.read_bytes()
        self.assertTrue(first.startswith(b'Before\n'))
        self.assertTrue(first.endswith(b'After\n'))
        result = self.run_installer('--target', target)
        self.assertIn('Already up to date', result.stdout)
        self.assertEqual(first, target.read_bytes())
        self.run_installer('--target', target, '--uninstall')
        self.assertEqual(target.read_bytes(), b'Before\nAfter\n')

    def test_crlf_unicode_and_permissions_survive_update(self):
        target = self.project / 'AGENTS.md'
        target.write_bytes(('之前\r\n' + installer.BEGIN + '\r\nold\r\n' + installer.END + '\r\n之后\r\n').encode())
        if os.name != 'nt':
            target.chmod(0o640)
        self.run_installer()
        content = target.read_bytes()
        self.assertTrue(content.startswith('之前\r\n'.encode()))
        self.assertTrue(content.endswith('之后\r\n'.encode()))
        self.assertNotIn(b'\n', content.replace(b'\r\n', b''))
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)

    def test_dry_run_prints_target_and_diff_without_creating_directories(self):
        result = self.run_installer('--global', '--dry-run')
        self.assertIn(str(self.profile / 'SOUL.md'), result.stdout)
        self.assertIn('+' + installer.BEGIN, result.stdout)
        self.assertFalse(self.profile.exists())

    def test_uninstall_missing_target_is_a_noop(self):
        self.run_installer('--global', '--uninstall')
        self.assertFalse(self.profile.exists())

    def test_damaged_markers_fail_without_touching_file(self):
        for content in (installer.BEGIN + '\nUser content\n', installer.END + '\n',
                        installer.END + '\n' + installer.BEGIN + '\n',
                        (installer.BEGIN + '\nx\n' + installer.END + '\n') * 2):
            for remove in (False, True):
                with self.subTest(content=content, remove=remove):
                    target = self.project / 'AGENTS.md'
                    target.write_text(content)
                    before = target.read_bytes()
                    args = ['--uninstall'] if remove else []
                    self.run_installer(*args, ok=False)
                    self.assertEqual(target.read_bytes(), before)

    def test_invalid_workspace_and_unknown_arguments_fail(self):
        self.run_installer('--workspace', self.base / 'missing', ok=False)
        result = subprocess.run([sys.executable, str(HELPER), '--globla'],
                                cwd=self.project, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.project / 'AGENTS.md').exists())

    def test_uninstall_works_when_prompt_is_missing(self):
        bundle = self.base / 'bundle'
        (bundle / 'scripts').mkdir(parents=True)
        helper = bundle / 'scripts' / 'install.py'
        shutil.copyfile(HELPER, helper)
        target = self.project / 'AGENTS.md'
        target.write_text('Keep\n' + installer.BEGIN + '\nOld\n' + installer.END + '\n')
        self.run_installer('--uninstall', helper=helper)
        self.assertEqual(target.read_text(), 'Keep\n')

    @unittest.skipIf(os.name == 'nt', 'Symlink creation may require Windows privileges')
    def test_symlink_remains_a_symlink(self):
        target = self.base / 'linked rules.md'
        target.write_text('Keep identity\n')
        link = self.project / 'AGENTS.md'
        link.symlink_to(target)
        self.run_installer()
        self.assertTrue(link.is_symlink())
        self.assertIn(installer.BEGIN, target.read_text())

    def test_atomic_replace_failure_keeps_original_and_cleans_temporary_file(self):
        target = self.project / 'AGENTS.md'
        target.write_text('Keep original\n')
        with mock.patch.object(installer.os, 'replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                installer.atomic_write(target, 'Replacement\n')
        self.assertEqual(target.read_text(), 'Keep original\n')
        self.assertEqual(list(self.project.glob('.talk-normal-*')), [])

    @unittest.skipUnless(os.name != 'nt' and shutil.which('bash') and shutil.which('python3'),
                         'Bash wrapper requires POSIX bash and python3')
    def test_bash_entry_point_resolves_helper_from_other_cwd(self):
        result = subprocess.run(['bash', str(ROOT / 'skill-hermes' / 'install.sh'),
                                 '--workspace', str(self.project)], cwd=self.base,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(installer.BEGIN, (self.project / 'AGENTS.md').read_text())

    @unittest.skipUnless(os.name != 'nt' and shutil.which('bash'), 'Sync script requires POSIX bash')
    def test_sync_preserves_hermes_installer_and_updates_shared_prompt(self):
        checkout = self.base / 'source checkout'
        for name in ('scripts', 'skill', 'skill-hermes/scripts'):
            (checkout / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'scripts/sync-skill.sh', checkout / 'scripts/sync-skill.sh')
        (checkout / 'prompt.md').write_text('<!-- talk-normal 9.8.7 -->\nNew rules\n')
        (checkout / 'install.sh').write_text('OpenClaw installer\n')
        (checkout / 'skill-hermes/install.sh').write_text('Hermes wrapper\n')
        shutil.copyfile(HELPER, checkout / 'skill-hermes/scripts/install.py')
        for name in ('skill', 'skill-hermes'):
            (checkout / name / 'SKILL.md').write_text('---\nversion: 0.0.0\n---\n')
        result = subprocess.run(['bash', str(checkout / 'scripts/sync-skill.sh')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((checkout / 'skill/install.sh').read_text(), 'OpenClaw installer\n')
        self.assertEqual((checkout / 'skill-hermes/install.sh').read_text(), 'Hermes wrapper\n')
        self.assertEqual((checkout / 'skill-hermes/scripts/install.py').read_bytes(), HELPER.read_bytes())
        for name in ('skill', 'skill-hermes'):
            self.assertEqual((checkout / name / 'prompt.md').read_bytes(), (checkout / 'prompt.md').read_bytes())
            self.assertIn('version: 9.8.7', (checkout / name / 'SKILL.md').read_text())


if __name__ == '__main__':
    unittest.main()
