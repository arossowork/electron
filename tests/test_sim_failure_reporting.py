"""Keep model failures bounded, observable, and distinct from a clean audit."""
import subprocess

import pytest

from hwflow.sim import spice
from hwflow.core.design import Design


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
    with pytest.raises(spice.DeratingTimeoutError, match='derating.*timed out'):
        spice.derating_audit(d, tmp_path)
    assert seen == [0.2]


@pytest.mark.parametrize('out, rc', [('Error: timestep too small', 1), ('No operating point produced', 0)])
def test_derating_rejects_missing_operating_point(tmp_path, monkeypatch, out, rc):
    d = Design.load('examples/sensor_preamp')
    monkeypatch.setattr(spice, 'run_ngspice', lambda *a, **kw: (out, rc))
    with pytest.raises(RuntimeError, match='derating'):
        spice.derating_audit(d, tmp_path)


def test_sim_stage_reports_failed_audit(tmp_path, monkeypatch):
    from hwflow.pipeline import stages
    from hwflow.sim import simrobust
    d = Design.load('examples/sensor_preamp')
    monkeypatch.setattr(stages, '_out', lambda d: tmp_path)
    monkeypatch.setattr(stages, '_status', lambda d: {})
    monkeypatch.setattr(spice, 'run_all', lambda *a, **kw: [])
    monkeypatch.setattr(simrobust, 'sim_summary', lambda *a, **kw: {})
    def fail(*a, **kw):
        raise RuntimeError('derating: no valid operating point')
    monkeypatch.setattr(spice, 'derating_audit', fail)
    result = stages.stage_sim(d)
    assert result['ok'] is False
    assert result['derating_status'] == 'failed'
    assert any('derating' in e and 'operating point' in e for e in result['errors'])
    assert 'exception' not in result
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


def test_derating_timeout_is_not_cached_as_sim_stage_failure(tmp_path, monkeypatch):
    import re
    import shutil

    from hwflow.artifacts import LocalCAS, read_tree
    from hwflow.core import settings
    from hwflow.pipeline.exec import execute_stage, read_json
    from hwflow.pipeline.runner import put_project
    from hwflow.pipeline.spec import STAGE_SPECS, LocalStageCache, StageInput
    from hwflow.sim import simrobust

    project = tmp_path / 'sensor_preamp'
    shutil.copytree('examples/sensor_preamp', project, ignore=shutil.ignore_patterns('build'))
    d = Design.load(project)
    store = LocalCAS(tmp_path / 'cas')
    cache = LocalStageCache(store.root)
    inp = StageInput(run_id='audit-retry', stage='sim', design=store.put_file(d.path),
                     project=put_project(store, d), settings=settings.get_settings().snapshot())
    monkeypatch.setattr(spice, 'run_all', lambda *a, **kw: [])
    monkeypatch.setattr(simrobust, 'sim_summary', lambda *a, **kw: {})
    calls = []

    def run(netlist, *args, **kwargs):
        calls.append(kwargs['timeout'])
        if len(calls) == 1:
            raise subprocess.TimeoutExpired('ngspice', kwargs['timeout'], output=b'partial operating point')
        pins = re.findall(r'^(Vam_\S+)\s+\S+\s+(\S+)\s', netlist, re.M)
        return '\n'.join(f'{name.lower()}#branch = 0\n{node} = 0' for name, node in pins), 0

    monkeypatch.setattr(spice, 'run_ngspice', run)
    spec = STAGE_SPECS['sim']
    first = execute_stage(spec, inp, store, cache, tmp_path / 'scratch')
    assert first.verdict == 'error' and not first.cached
    assert cache.get('sim', first.cache_key) is None
    failed = read_json(store, first.result)
    assert failed['derating_status'] == 'failed' and not failed['ok']
    assert 'timed out' in failed['errors'][0]
    assert 'sim/derating.log' in read_tree(store, first.outputs['logs'])

    second = execute_stage(spec, inp, store, cache, tmp_path / 'scratch')
    assert second.verdict == 'pass' and not second.cached and len(calls) == 2
    third = execute_stage(spec, inp, store, cache, tmp_path / 'scratch')
    assert third.verdict == 'pass' and third.cached and len(calls) == 2
