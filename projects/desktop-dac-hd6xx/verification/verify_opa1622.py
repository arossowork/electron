#!/usr/bin/env python3
"""Bounded, reproducible OPA1622 model integration checks (not board qualification)."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import numpy as np


def check(value, low, high, label):
    assert np.isfinite(value) and low <= value <= high, f'{label}: {value} outside [{low}, {high}]'


def run(model, output, name, body, commands, compatibility, timeout):
    path = output / name
    path.mkdir(parents=True, exist_ok=True)
    # Compatibility is local to this isolated case; do not alter user settings.
    text = f'* OPA1622 validation: {name}\n.include "{model}"\n{body}\n.control\nset noaskquit\nset numdgt=15\nset wr_singlescale\nset wr_vecnames\n{commands}\nquit\n.endc\n.end\n'
    (path / 'bench.cir').write_text(text)
    start = time.monotonic()
    proc = subprocess.run(['ngspice', '-n', *(['-D', 'ngbehavior=ps'] if compatibility else []), '-b', 'bench.cir'], cwd=path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    (path / 'ngspice.log').write_text(proc.stdout)
    assert proc.returncode == 0, f'{name}: ngspice exit {proc.returncode}'
    assert not any(x in proc.stdout.lower() for x in ['error:', 'timestep too small', 'simulation interrupted']), f'{name}: simulation error'
    data = np.atleast_2d(np.loadtxt(path / 'result.dat', skiprows=1))
    assert np.isfinite(data).all(), f'{name}: nonfinite output'
    return data, time.monotonic() - start


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--compatibility', action='store_true', help='Reference TI PSpice model via native ngbehavior=ps')
    p.add_argument('--timeout', type=float, default=20)
    a = p.parse_args()
    a.model = a.model.resolve(); a.output = a.output.resolve(); a.output.mkdir(parents=True, exist_ok=True)
    metrics = {'source_sha256': hashlib.sha256(a.model.read_bytes()).hexdigest(), 'compatibility': a.compatibility, 'cases': {}}
    def sim(name, body, commands):
        return run(a.model, a.output, name, body, commands, a.compatibility, a.timeout)[0]
    def gain2(source, rail=9, load=300):
        return f'VPOS VPOS 0 {rail}\nVNEG VNEG 0 -{rail}\nVIN IN 0 {source}\nRFB OUT FB 10k\nRG FB 0 10k\nRL OUT 0 {load}\nXU IN FB VPOS VNEG OUT OPA1622'
    for vin in (0, -2.82842712474619, 2.82842712474619, -5, 5):
        row = sim(f'op_{vin:+g}', gain2(str(vin)), 'op\nwrdata result.dat v(out) v(fb) i(vpos) i(vneg)')[0]
        out = row[1]
        check(out, -9, 9, 'output remains inside supply rails')
        if abs(vin) < 4:
            check(out - 2*vin, 0.011, 0.0135, 'gain2 offset including input bias current')
        else:
            check(np.sign(vin)*out, 7, 8.9, 'both overload polarities clip below rail')
        metrics['cases'][f'op_{vin:+g}'] = {'input_v': vin, 'output_v': out, 'feedback_v': row[2], 'positive_supply_a': -row[3], 'negative_supply_a': row[4]}
    for rail, freq in ((r, f) for r in (9, 8.1) for f in (20, 1000, 20000)):
        period = 1/freq
        data = sim(f'sine_{freq}_rail_{rail}', gain2(f'DC 0 SIN(0 2.82842712474619 {freq})', rail=rail), f'tran {period/400:.12g} {6*period:.12g} 0 {period/400:.12g}\nwrdata result.dat v(in) v(out)')
        data = data[data[:,0] >= 2*period]
        t = data[:,0]; vin = data[:,1]; out = data[:,2]
        basis = np.column_stack((np.sin(2*np.pi*freq*t), np.cos(2*np.pi*freq*t), np.ones_like(t)))
        fit = np.linalg.lstsq(basis, out, rcond=None)[0]
        input_fit = np.linalg.lstsq(basis, vin, rcond=None)[0]
        amp = float(np.hypot(fit[0],fit[1])); gain = amp / np.hypot(*input_fit[:2])
        residual = out - basis@fit
        check(gain, 1.998, 2.002, 'transient gain')
        check(float(np.min(out)), -rail, -5.6, 'negative signal excursion')
        check(float(np.max(out)), 5.6, rail, 'positive signal excursion')
        check(float(np.sqrt(np.mean(residual**2))), 0, 0.005, 'sine fit residual (not THD+N qualification)')
        metrics['cases'][f'sine_{freq}_rail_{rail}'] = {'gain': gain, 'output_rms_v': amp/np.sqrt(2), 'phase_degrees': float(np.degrees(np.arctan2(fit[1],fit[0]))), 'dc_v': fit[2], 'rail_v': rail, 'min_v': float(np.min(out)), 'max_v': float(np.max(out)), 'fit_residual_rms_v': float(np.sqrt(np.mean(residual**2))), 'points': len(t)}
    data = sim('audio_ac', gain2('DC 0 AC 1'), 'ac dec 20 20 20000\nlet gain=mag(v(out))\nlet phase=180/PI*ph(v(out))\nwrdata result.dat gain phase')
    check(float(np.min(data[:,1])), 1.998, 2.002, 'AC minimum gain')
    check(float(np.max(data[:,1])), 1.998, 2.002, 'AC maximum gain')
    metrics['cases']['audio_ac'] = {'min_gain': float(np.min(data[:,1])), 'max_gain': float(np.max(data[:,1])), 'phase_20khz_degrees': float(data[-1,2])}
    follower = 'VPOS VPOS 0 18\nVNEG VNEG 0 -18\nVIN IN 0 0\nRL OUT 0 1T\nXU IN OUT VPOS VNEG OUT OPA1622'
    row = sim('datasheet_offset_bias_iq', follower, 'op\nwrdata result.dat v(out) i(vin) i(vpos) i(vneg)')[0]
    check(abs(row[1]), 0, 0.0005, 'input offset max 500uV')
    check(abs(row[2]), 1e-6, 1.4e-6, 'input bias approx 1.2uA')
    check(-row[3], 0.0024, 0.0028, 'quiescent current approx 2.6mA')
    metrics['cases']['datasheet_offset_bias_iq'] = {'offset_v': row[1], 'bias_a': abs(row[2]), 'iq_a': -row[3]}
    for polarity in (-1,1):
        body = f'VPOS VPOS 0 18\nVNEG VNEG 0 -18\nVIN IN 0 {-0.1*polarity}\nRIN IN FB 1k\nRFB OUT FB 100k\nRL OUT 0 1\nXU 0 FB VPOS VNEG OUT OPA1622'
        row = sim(f'datasheet_short_{polarity:+d}', body, 'op\nwrdata result.dat v(out)')[0]
        check(abs(row[1]), 0.12, 0.16, 'short current in 1ohm load approx +145/-130mA')
        assert np.sign(row[1]) == polarity
        metrics['cases'][f'datasheet_short_{polarity:+d}'] = {'output_a': row[1]}
    body = 'VPOS VPOS 0 18\nVNEG VNEG 0 -18\nVIN IN 0 PULSE(-5 5 2u 1n 1n 4u 8u)\nRIN IN FB 1k\nRFB OUT FB 1k\nRL OUT 0 2k\nXU 0 FB VPOS VNEG OUT OPA1622'
    data = sim('datasheet_slew', body, 'tran 10n 12u 0 10n\nwrdata result.dat v(out)')
    sr = {}
    for name, t1, t2 in [('fall',2e-6,4e-6),('rise',6e-6,8e-6)]:
        points = data[(data[:,0]>t1)&(data[:,0]<t2)&(abs(data[:,1])<3)]
        assert len(points)>5
        rate = abs(float(np.polyfit(points[:,0],points[:,1],1)[0]))/1e6
        check(rate, 8, 12, 'slew rate approx 10V/us')
        sr[f'{name}_v_per_us']=rate
    metrics['cases']['datasheet_slew']=sr
    metrics['passed'] = True
    (a.output / 'summary.json').write_text(json.dumps(metrics, indent=2)+'\n')
    print(json.dumps(metrics, indent=2))

if __name__ == '__main__':
    main()
