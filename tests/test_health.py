"""Battery and SSD health readings, and the health overview cards."""
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from deck_integrity import cli, health, overview, reports
from deck_integrity.runner import CommandResult
from helpers import make_args, make_ctx

SMART_OK = {
    'model_name': 'ESMP512GHV7C3-E21TS', 'firmware_version': 'X1', 'serial_number': 'SECRET123',
    'smart_status': {'passed': True},
    'nvme_smart_health_information_log': {
        'critical_warning': 0, 'temperature': 41, 'available_spare': 100,
        'available_spare_threshold': 10, 'percentage_used': 3, 'data_units_read': 1000,
        'data_units_written': 4000000, 'power_on_hours': 812, 'power_cycles': 300,
        'unsafe_shutdowns': 12, 'media_errors': 0, 'num_err_log_entries': 0}}
SMART_DENIED = {'smartctl': {'exit_status': 2, 'messages': [
    {'string': 'Smartctl open device: /dev/nvme0 failed: Permission denied', 'severity': 'error'}]}}


def sysfs_battery(root: pathlib.Path, **files) -> None:
    b = root / 'class' / 'power_supply' / 'BAT0'
    b.mkdir(parents=True)
    (b / 'type').write_text('Battery\n')
    for k, v in files.items():
        (b / k).write_text(f'{v}\n')


class BatteryTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_energy_based_health(self):
        sysfs_battery(self.root, energy_full=37600000, energy_full_design=40000000, cycle_count=212,
                      capacity=80, status='Discharging')
        b = health.battery_health(self.root)
        self.assertEqual(b['health_pct'], 94.0)
        status, value, detail = health.battery_verdict(b)
        self.assertEqual((status, value), ('good', '94% of original'))
        self.assertIn('212 charge cycles', detail)

    def test_charge_based_and_worn(self):
        sysfs_battery(self.root, charge_full=2900000, charge_full_design=5000000, cycle_count=0)
        b = health.battery_health(self.root)
        self.assertEqual(b['health_pct'], 58.0)
        self.assertIsNone(b['cycle_count'])            # 0 means "not tracked"
        self.assertEqual(health.battery_verdict(b)[0], 'bad')

    def test_above_design_is_capped(self):
        sysfs_battery(self.root, energy_full=41000000, energy_full_design=40000000)
        self.assertEqual(health.battery_health(self.root)['health_pct'], 100.0)

    def test_missing_design_and_no_battery(self):
        sysfs_battery(self.root, capacity=50)
        self.assertEqual(health.battery_verdict(health.battery_health(self.root))[0], 'unknown')
        self.assertIsNone(health.battery_health(self.root / 'nothing'))
        self.assertEqual(health.battery_verdict(None)[1], 'No battery')


class SsdTests(unittest.TestCase):
    def test_parse_drops_serial_and_converts_units(self):
        s = health.parse_smartctl(json.dumps(SMART_OK))
        self.assertNotIn('SECRET123', json.dumps(s))
        self.assertEqual(s['percentage_used'], 3)
        self.assertEqual(s['tb_written'], 2.05)        # 4,000,000 units x 512,000 bytes

    def test_parse_rejects_output_without_health_log(self):
        with self.assertRaises(ValueError):
            health.parse_smartctl(json.dumps(SMART_DENIED))
        with self.assertRaises(ValueError):
            health.parse_smartctl('')

    def test_verdicts(self):
        good = health.parse_smartctl(json.dumps(SMART_OK))
        self.assertEqual(health.ssd_verdict(good)[:2], ('good', '3% worn'))
        self.assertEqual(health.ssd_verdict({**good, 'percentage_used': 85})[0], 'warn')
        self.assertEqual(health.ssd_verdict({**good, 'percentage_used': 100})[0], 'bad')
        self.assertEqual(health.ssd_verdict({**good, 'media_errors': 2})[0], 'bad')
        self.assertEqual(health.ssd_verdict({**good, 'available_spare': 5})[0], 'bad')
        self.assertEqual(health.ssd_verdict({**good, 'critical_warning': 4})[0], 'bad')
        self.assertEqual(health.ssd_verdict(None, 'needs admin')[:2], ('unknown', 'Not measured'))

    def test_command_validates_device_and_uses_absolute_paths(self):
        with self.assertRaises(ValueError):
            health.smart_command('nvme0; rm -rf ~', elevated=False)
        with mock.patch.object(health.shutil, 'which', side_effect=lambda n: '/usr/bin/' + n):
            self.assertEqual(health.smart_command('nvme0', False),
                             ['/usr/bin/smartctl', '-j', '-a', '/dev/nvme0'])
            self.assertEqual(health.smart_command('nvme0', True)[0], '/usr/bin/pkexec')
        with mock.patch.object(health.shutil, 'which', return_value=None):
            self.assertIsNone(health.smart_command('nvme0', False))


class ReadFlowTests(unittest.TestCase):
    """Unprivileged first; admin only on opt-in; declining is reported clearly."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, responses, elevated):
        ctx = make_ctx(self.tmp)

        def fake(argv, timeout):
            self.calls.append(argv)
            code, payload = responses[len(self.calls) - 1]
            return CommandResult(argv=argv, returncode=code, stdout=json.dumps(payload) if payload else '')

        ctx.run_command = fake
        with mock.patch.object(health, 'nvme_devices', return_value=['nvme0']), \
                mock.patch.object(health.shutil, 'which', side_effect=lambda n: '/usr/bin/' + n):
            return health.read_ssd_health(ctx, elevated=elevated)

    def test_unprivileged_success_never_asks_for_password(self):
        out = self.run_with([(0, SMART_OK)], elevated=True)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNotNone(out[0]['smart'])
        self.assertFalse(out[0]['elevated'])

    def test_without_opt_in_no_password_prompt(self):
        out = self.run_with([(2, SMART_DENIED)], elevated=False)
        self.assertEqual(len(self.calls), 1)
        self.assertIn('needs admin rights', out[0]['reason'])

    def test_opt_in_uses_pkexec(self):
        out = self.run_with([(2, SMART_DENIED), (0, SMART_OK)], elevated=True)
        self.assertEqual(self.calls[1][0], '/usr/bin/pkexec')
        self.assertEqual(out[0]['smart']['percentage_used'], 3)

    def test_declined_password_is_explained(self):
        out = self.run_with([(2, SMART_DENIED), (126, None)], elevated=True)
        self.assertIsNone(out[0]['smart'])
        self.assertIn('passwd', out[0]['reason'])


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cards_from_a_run(self):
        from deck_integrity.checks import zero
        from helpers import FakeTools
        ctx = make_ctx(self.tmp, FakeTools({'head': lambda n: 'wrong' if n == 2 else None}))
        zero.run(ctx)
        ctx.rec.run_info['hardware'] = {
            'battery': {'health_pct': 94.0, 'full_display': '37.6 Wh', 'design_display': '40.0 Wh',
                        'cycle_count': None},
            'ssd': [{'device': 'nvme0', 'smart': health.parse_smartctl(json.dumps(SMART_OK)),
                     'reason': ''}]}
        cards = {c['key']: c for c in overview.cards(ctx.rec)}
        self.assertEqual(cards['cpu_mem']['status'], 'bad')
        self.assertEqual(cards['memory']['value'], 'Not tested')
        self.assertEqual(cards['ssd_health']['value'], '3% worn')
        self.assertEqual(cards['battery']['value'], '94% of original')
        self.assertEqual(cards['temps']['value'], 'Peak 55 °C')
        pending = {c['key']: c for c in overview.cards(ctx.rec, final=False)}
        self.assertEqual(pending['storage']['status'], 'pending')

    def test_summary_includes_health_overview(self):
        ctx = make_ctx(self.tmp)
        ctx.rec.run_info['health'] = [overview.card('battery', 'Battery', 'warn', '70% of original', 'Worn.')]
        reports.write_all(ctx.rec, {})
        text = (ctx.rec.out / 'support-summary.txt').read_text(encoding='utf-8')
        self.assertIn('HEALTH OVERVIEW', text)
        self.assertIn('Battery: CHECK - 70% of original', text)

    def test_cli_flag(self):
        self.assertTrue(cli.parse_args(['--ssd-health']).ssd_health)
        self.assertFalse(cli.parse_args([]).ssd_health)
        self.assertIn('health', cli.parse_args(['--skip', 'health']).skip)


if __name__ == '__main__':
    unittest.main()
