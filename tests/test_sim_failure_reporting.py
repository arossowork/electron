"""Keep model failures bounded, observable, and distinct from a clean audit."""
import subprocess
from pathlib import Path

import pytest

from hwflow import spice
from hwflow.design import Design


def test_timeout_keeps_solver_diagnostic(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('ngspice', kwargs['timeout'],
                                        output=b'last point: v(out)=0\n',
                                        stderr=b'Trying gmin stepping\n')
    monkeypatch.setattr(subprocess, 'run', timeout)
    out, rc, cached, expired = spice.run_cached('* blocked\n.end\n', tmp_path, 'blocked', timeout=0.25)
    assert expired and rc == -1 and not cached
    assert 'Trying gmin stepping' in out and 'last point' in out
    assert (tmp_path / 'blocked.log').read_text() == out


def test_derating_uses_its_limit_and_rejects_timeout(tmp_path, monkeypatch):
    d = Design.load('examples/sensor_preamp')
    d.raw['sim']['derating_timeout_s'] = 0.2
    seen = []
    def timeout(*args, **kwargs):
        seen.append(kwargs.get('timeout'))
        raise subprocess.TimeoutExpired('ngspice', kwargs.get('timeout', 600))
    monkeypatch.setattr(spice, 'run_ngspice', timeout)
    with pytest.raises(RuntimeError, match='derating.*timed out'):
        spice.derating_audit(d, tmp_path)
    assert seen == [0.2]


@pytest.mark.parametrize('out, rc', [('Error: timestep too small', 1), ('No operating point produced', 0)])
def test_derating_rejects_missing_operating_point(tmp_path, monkeypatch, out, rc):
    d = Design.load('examples/sensor_preamp')
    monkeypatch.setattr(spice, 'run_ngspice', lambda *a, **kw: (out, rc))
    with pytest.raises(RuntimeError, match='derating'):
        spice.derating_audit(d, tmp_path)


def test_sim_stage_reports_failed_audit(tmp_path, monkeypatch):
    from hwflow import cli, simrobust
    d = Design.load('examples/sensor_preamp')
    monkeypatch.setattr(cli, '_out', lambda d: tmp_path)
    monkeypatch.setattr(cli, '_status', lambda d: {})
    monkeypatch.setattr(spice, 'run_all', lambda *a, **kw: [])
    monkeypatch.setattr(simrobust, 'sim_summary', lambda *a, **kw: {})
    def fail(*a, **kw):
        raise RuntimeError('derating: ngspice timed out after 0.2 s')
    monkeypatch.setattr(spice, 'derating_audit', fail)
    result = cli.stage_sim(d)
    assert result['ok'] is False
    assert result['derating_status'] == 'failed'
    assert any('derating' in e and 'timed out' in e for e in result['errors'])
    assert not result['derating']


def test_pspice_mode_is_explicit_and_per_run(tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout='', stderr='', returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    d = Design.load('examples/sensor_preamp')
    a = d.raw['sim']['analyses'][0]
    d.raw['sim']['ngbehavior'] = 'ps'
    ps, _ = spice.build_netlist(d, a)
    spice.run_ngspice(ps, tmp_path, 'ps')
    assert '-D' in calls[0] and 'ngbehavior=ps' in calls[0] and '-n' in calls[0]
    d.raw['sim'].pop('ngbehavior')
    native, _ = spice.build_netlist(d, a)
    spice.run_ngspice(native, tmp_path, 'native')
    assert '-D' not in calls[1] and ps != native


def test_unknown_compatibility_mode_is_rejected():
    d = Design.load('examples/sensor_preamp')
    d.raw['sim']['ngbehavior'] = 'typo'
    with pytest.raises(ValueError, match='ngbehavior'):
        spice.build_netlist(d, d.raw['sim']['analyses'][0])


def test_derating_requires_pin_voltages_and_finite_values(tmp_path, monkeypatch):
    import re
    d = Design.load('examples/sensor_preamp')
    def currents_only(netlist, *args, **kwargs):
        names = re.findall(r'^(Vam_\S+)\s', netlist, re.M)
        return '\n'.join(f'{n.lower()}#branch = 0' for n in names), 0
    monkeypatch.setattr(spice, 'run_ngspice', currents_only)
    with pytest.raises(RuntimeError, match='derating.*missing'):
        spice.derating_audit(d, tmp_path)

    def nonfinite_voltage(netlist, *args, **kwargs):
        pins = re.findall(r'^(Vam_\S+)\s+\S+\s+(\S+)\s', netlist, re.M)
        return '\n'.join(f'{n.lower()}#branch = 0\n{v} = 1e999' for n,v in pins), 0
    monkeypatch.setattr(spice, 'run_ngspice', nonfinite_voltage)
    with pytest.raises(RuntimeError, match='derating.*nonfinite'):
        spice.derating_audit(d, tmp_path)
