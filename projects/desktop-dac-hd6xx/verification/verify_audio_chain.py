#!/usr/bin/env python3
"""OPA161x and ideal-current-input analog chain integration tests; no DAC/EN model."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from verify_opa1622 import check, run
from hwflow.design import Design
from hwflow.spice import NodeMap, component_blocks


def fit(data, freq):
    data = data[data[:,0] >= 2/freq]
    basis = np.column_stack((np.sin(2*np.pi*freq*data[:,0]),np.cos(2*np.pi*freq*data[:,0]),np.ones(len(data))))
    coeff = np.linalg.lstsq(basis,data[:,1:],rcond=None)[0]
    return np.hypot(coeff[0],coeff[1])/np.sqrt(2), coeff[2], data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--design',type=Path,default=Path(__file__).resolve().parents[1]/'design.yaml')
    a=p.parse_args(); a.model_dir=a.model_dir.resolve(); a.output=a.output.resolve()
    amp161=a.model_dir/'opa161x_ti.lib'; amp162=a.model_dir/'opa1622_ti.lib'
    design=Design.load(a.design.resolve())
    refs=['U200','U202',*[f'R{i}' for i in range(200,208)],*[f'C{i}' for i in range(200,205)],*[f'R{i}' for i in range(240,244)]]
    selected=Design(path=design.path, raw=design.raw, components={r:design.components[r] for r in refs})
    nm=NodeMap(selected)
    blocks,unmodeled,libs=component_blocks(selected,nm)
    assert not unmodeled, unmodeled
    assert {b.eid for b in blocks} == set(refs)
    generated='\n'.join(line for b in blocks for line in b.lines)
    included='\n'.join(f'.include "{lib}"' for lib in sorted(libs))
    boundary=NodeMap(design)
    vpos=boundary.pin('U200','8'); vneg=boundary.pin('U200','4')
    dacp=boundary.pin('U200','5'); dacn=boundary.pin('U200','6')
    vol=boundary.pin('J200','2'); pot_top=boundary.pin('J200','1'); pot_gnd=boundary.pin('J200','3')
    amp=boundary.pin('U202','9'); quiet_in=boundary.pin('U202','5'); quiet_out=boundary.pin('U202','7')
    enable=boundary.pin('U202','8')
    result={'design_sha256':hashlib.sha256(design.path.read_bytes()).hexdigest(),
            'selected_components':{r:design.components[r].data for r in refs+['J200']},
            'wrapper_sha256':{Path(lib).name:hashlib.sha256(Path(lib).read_bytes()).hexdigest() for lib in sorted(libs)},
            'model_sha256': {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (amp161,amp162)}, 'cases':{}}
    def sim(name,body,commands):
        return run(amp161,a.output,name,body,commands,True,20)[0]
    for dc in (0,-2.82842712474619,2.82842712474619):
        body=f'VPOS VPOS 0 9\nVNEG VNEG 0 -9\nVIN IN 0 {dc}\nRFB OUT FB 10k\nRG FB 0 10k\nRL OUT 0 2k\nXU IN FB VPOS VNEG OUT OPA161x'
        commands = 'op\nwrdata result.dat v(out)' if dc == 0 else f'dc VIN 0 {dc} {dc/100}\nwrdata result.dat v(out)'
        if dc != 0: body=body.replace(f'VIN IN 0 {dc}', 'VIN IN 0 0')
        row=sim(f'opa161x_dc_{dc:+g}',body,commands)[-1]
        check(abs(row[1]-2*dc),0,0.01,'OPA161x dc gain and offset')
        result['cases'][f'opa161x_dc_{dc:+g}']={'input_v':dc,'output_v':row[1]}
    for freq in (20,1000,20000):
        body=f'VPOS VPOS 0 9\nVNEG VNEG 0 -9\nVIN IN 0 SIN(0 2.82842712474619 {freq})\nRFB OUT FB 10k\nRG FB 0 10k\nRL OUT 0 2k\nXU IN FB VPOS VNEG OUT OPA161x'
        data=sim(f'opa161x_sine_{freq}',body,f'tran {1/freq/400} {6/freq} 0 {1/freq/400}\nwrdata result.dat v(in) v(out)')
        rms,offset,data=fit(data,freq)
        gain=rms[1]/rms[0]
        check(gain,1.998,2.002,'OPA161x transient gain')
        check(np.min(data[:,2]),-9,-5.6,'OPA161x negative headroom')
        check(np.max(data[:,2]),5.6,9,'OPA161x positive headroom')
        result['cases'][f'opa161x_sine_{freq}']={'gain':gain,'output_rms_v':rms[1],'offset_v':offset[1]}
    row=sim('opa161x_iq','VPOS VPOS 0 18\nVNEG VNEG 0 -18\nVIN IN 0 0\nRL OUT 0 1T\nXU IN OUT VPOS VNEG OUT OPA161x','op\nwrdata result.dat v(out) i(vpos)')[0]
    check(abs(row[1]),0,0.0005,'OPA161x input offset max500uV')
    check(-row[2],0.0034,0.0038,'OPA161x quiescent current approx3.6mA')
    result['cases']['opa161x_iq']={'offset_v':row[1],'iq_a':-row[2]}
    for rail,freq in ((r,f) for r in (9,8.1) for f in (20,1000,20000)):
        # Circuit elements and package ports are generated from current design.yaml.
        # The physical pot is at maximum; unused U202B has zero input and its actual feedback.
        # Defined ideal DAC currents start from a solved zero-current OP, without UIC.
        body=f''' .include "{amp162}"
{included}
{generated}
VPOS {vpos} 0 {rail}
VNEG {vneg} 0 -{rail}
BIP 0 {dacp} I=min(time/{1/freq/10},1)*(1m+1.571348402637m*sin(2*PI*{freq}*time))
BIN 0 {dacn} I=min(time/{1/freq/10},1)*(1m-1.571348402637m*sin(2*PI*{freq}*time))
RPOT {pot_top} {pot_gnd} 10k
VPOT {pot_top} {vol} 0
VQUIET {quiet_in} 0 0
VEN {enable} 0 3.3
RQUIET {quiet_out} 0 300
RLOAD {amp} 0 300'''
        name=f'chain_{freq}_rail_{rail}'
        data=sim(name,body,f'tran {1/freq/400} {6/freq} 0 {1/freq/400}\nwrdata result.dat v({vol}) v({amp}) v({dacp}) v({dacn})')
        rms,offset,data=fit(data,freq)
        check(rms[1],3.8,4.2,'ideal-current-input analog chain output')
        check(rms[1]/rms[0],1.998,2.002,'chain OPA1622 gain')
        check(np.min(data[:,2]),-rail,-5.3,'chain negative output rail')
        check(np.max(data[:,2]),5.3,rail,'chain positive output rail')
        check(np.min(data[:,3:]),-rail+2,rail-2,'OPA161x input commonmode min')
        check(np.max(data[:,3:]),-rail+2,rail-2,'OPA161x input commonmode max')
        result['cases'][name]={'rail_v':rail,'volume_rms_v':rms[0],'output_rms_v':rms[1],'output_offset_v':offset[1],'dac_input_commonmode_min_v':float(np.min(data[:,3:])),'dac_input_commonmode_max_v':float(np.max(data[:,3:]))}
    result['passed']=True
    a.output.mkdir(exist_ok=True,parents=True)
    (a.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
