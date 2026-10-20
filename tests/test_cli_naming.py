"""Report folder naming and the no-terminal guard."""
import datetime
import io
import unittest
from unittest import mock

from deck_integrity import cli
from deck_integrity.redact import Redactor

LEGION = {'sys_vendor': 'LENOVO', 'product_name': '83L3', 'product_version': 'Legion Go S 8ARP1',
          'product_family': 'Legion Go S 8ARP1'}


class NamingTests(unittest.TestCase):
    def test_steam_deck_models(self):
        self.assertEqual(cli.device_label({'sys_vendor': 'Valve', 'product_name': 'Galileo'}),
                         'Steam-Deck-OLED')
        self.assertEqual(cli.device_label({'sys_vendor': 'Valve', 'product_name': 'Jupiter'}),
                         'Steam-Deck-LCD')

    def test_legion_go_s_uses_family_name(self):
        self.assertEqual(cli.device_label(LEGION), 'Legion-Go-S-8ARP1')

    def test_placeholder_dmi_falls_back_to_vendor_product(self):
        dmi = {'sys_vendor': 'ACME', 'product_name': 'X1', 'product_family': 'Default string',
               'product_version': 'To Be Filled By O.E.M.'}
        self.assertEqual(cli.device_label(dmi), 'ACME-X1')
        self.assertEqual(cli.device_label({}), 'unknown-device')

    def test_os_label(self):
        self.assertEqual(cli.os_label({'NAME': 'SteamOS', 'VERSION_ID': '3.8.28'}), 'SteamOS-3.8.28')

    def test_modes(self):
        self.assertEqual(cli.mode_label(cli.parse_args(['--quick'])), 'quick')
        self.assertEqual(cli.mode_label(cli.parse_args([])), 'full')
        self.assertEqual(cli.mode_label(cli.parse_args(['--passes', '10'])), 'custom')
        self.assertEqual(cli.mode_label(cli.parse_args(['--skip', 'ssd'])), 'partial')

    def test_run_name_is_sortable_and_descriptive(self):
        with mock.patch.object(cli.sysinfo, 'dmi', return_value=LEGION), \
                mock.patch.object(cli.sysinfo, 'os_release',
                                  return_value={'NAME': 'SteamOS', 'VERSION_ID': '3.8.28'}):
            name = cli.run_name(cli.parse_args(['--quick']), datetime.datetime(2026, 10, 20, 12, 42, 34))
        self.assertEqual(name, '2026-10-20_124234_Legion-Go-S-8ARP1_SteamOS-3.8.28_quick')


class GuardTests(unittest.TestCase):
    def test_no_terminal_refuses_without_running(self):
        with mock.patch.object(cli, 'interactive', return_value=False), \
                mock.patch.object(cli, 'notify') as notify, \
                mock.patch.object(cli, 'run') as run, \
                mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            code = cli.main(['--quick'])
        self.assertEqual(code, cli.EXIT_NOT_CONFIRMED)
        run.assert_not_called()
        notify.assert_called_once()
        self.assertIn('--yes', err.getvalue())

    def test_yes_overrides_guard(self):
        with mock.patch.object(cli, 'interactive', return_value=False), \
                mock.patch.object(cli, 'install_signal_handlers'), \
                mock.patch.object(cli, 'run', return_value=0) as run:
            self.assertEqual(cli.main(['--quick', '--yes']), 0)
        run.assert_called_once()

    def test_terminal_runs_normally(self):
        with mock.patch.object(cli, 'interactive', return_value=True), \
                mock.patch.object(cli, 'install_signal_handlers'), \
                mock.patch.object(cli, 'run', return_value=0) as run:
            cli.main(['--quick'])
        run.assert_called_once()


class GenericHostTests(unittest.TestCase):
    def test_default_steamos_hostname_not_redacted(self):
        r = Redactor(home='/home/deck', users=['deck'], hostname='steamdeck')
        self.assertEqual(r('VARIANT_ID=steamdeck'), 'VARIANT_ID=steamdeck')


if __name__ == '__main__':
    unittest.main()
