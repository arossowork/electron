#!/usr/bin/env python3
"""Circuit-derived, qualified protection model. Not vendor silicon/relay models.

Run from the repository root using the pinned hwflow-audio:ca10fa8e container.
The R/C matrices, gate topology, delay wiring, and contact path come from YAML.
Only documented component semantics and explicitly labeled envelopes are modeled.
"""
from __future__ import annotations
import argparse, copy, hashlib, itertools, json, math, re, subprocess
from pathlib import Path
import numpy as np
from scipy.linalg import expm
import yaml

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / 'design.yaml'
DT = 0.0005
# Engineering envelope, NOT component guarantees: populated with bench gates below.
LIMITS = {'dc_fault_v': 1.0, 'dc_trip_deadline_s': 1.5, 'corner_dc_trip_deadline_s': 2.0,
          'rail_fault_v': 9.0, 'rail_trip_deadline_s': 0.2,
          'minimum_clear_dc_v': 0.15, 'audio_vrms': 4.0, 'audio_hz': 20.0,
          'relay_release_assumption_s': 0.020, 'relay_operate_s': 0.003,
          'opa_disable_max_v': 0.65, 'logic_ioff_a': 10e-6,
          'logic_input_leakage_a': 5e-6, 'comparator_bias_a': 50e-9,
          'comparator_offset_v': 0.0055, 'clamp_leakage_max_a': 80e-9, 'filter_cap_leakage_envelope_a': 500e-9,
          'arm_cap_leakage_envelope_a': 1e-6}

def value(v):
    m = re.fullmatch(r'([0-9.]+)\s*([pnumkM]?)(?:F|[oO]hm)?', str(v))
    if not m:
        raise ValueError(v)
    return float(m[1])*{'':1,'p':1e-12,'n':1e-9,'u':1e-6,'m':1e-3,'k':1e3,'M':1e6}[m[2]]

def load():
    return yaml.safe_load(SOURCE.read_text())['components']

def p(c, ref, pin):
    return c[ref]['pins'][str(pin)]

def resistance(c, ref, factors):
    return value(c[ref]['value'])*factors.get(ref,1)

class Passive:
    """Exact ZOH linear R/C solution, with algebraic nodes Schur-eliminated.

    Every resistor/capacitor is stamped from source connections. Independent
    ngspice cross-check covers startup and audio. The nonlinear clamp region is
    outside this linear model; only fault onset before conduction is evaluated.
    """
    refs = ['R245','R246','R247','R248','R249','R250','R251','R252','R253','R254']
    caps = ['C240','C241','C242']
    inputs = ['3V3_D','AMP_L','AMP_R','GND']
    def __init__(self,c,rf=None,cf=None,leak=(0,0),bias=0,dt=DT):
        rf,cf=rf or {},cf or {}
        self.refs=list(type(self).refs)+[r for r in ['R264','R265','R266','R267'] if r in c]
        self.nodes=sorted({p(c,r,i) for r in self.refs+self.caps for i in (1,2)}-set(self.inputs))
        n=len(self.nodes);self.G=np.zeros((n,n));self.C=np.zeros((n,n));self.B=np.zeros((n,4))
        for ref in self.refs+self.caps:
            ns=[p(c,ref,1),p(c,ref,2)]
            z=1/resistance(c,ref,rf) if ref[0]=='R' else value(c[ref]['value'])*cf.get(ref,1)
            mat=self.G if ref[0]=='R' else self.C
            for side in (0,1):
                aa,bb=ns[side],ns[1-side]
                if aa not in self.nodes:continue
                i=self.nodes.index(aa);mat[i,i]+=z
                if bb in self.nodes:mat[i,self.nodes.index(bb)]-=z
                elif ref[0]=='R':self.B[i,self.inputs.index(bb)]+=z
                elif bb!='GND':raise ValueError('cap to driven rail needs derivative term')
        self.injection=np.zeros(n)
        for channel,cref in enumerate(('C241','C242')):
            self.injection[self.nodes.index(p(c,cref,1))]+=leak[channel]
        for pin in ('4','5','6','7','8','9','10','11'):
            self.injection[self.nodes.index(p(c,'U203',pin))]+=bias
        self.d=np.flatnonzero(np.diag(self.C));self.a=np.flatnonzero(np.diag(self.C)==0)
        ix=np.ix_;gdd=self.G[ix(self.d,self.d)];gda=self.G[ix(self.d,self.a)]
        gaa=self.G[ix(self.a,self.a)];gad=self.G[ix(self.a,self.d)]
        self.ga_inv=np.linalg.inv(gaa);self.ad=-self.ga_inv@gad
        self.ab=self.ga_inv@self.B[self.a];self.ai=self.ga_inv@self.injection[self.a]
        gd=gdd-gda@self.ga_inv@gad
        self.bd=self.B[self.d]-gda@self.ab;self.id=self.injection[self.d]-gda@self.ai
        cd=self.C[ix(self.d,self.d)];self.A=expm(-np.linalg.solve(cd,gd)*dt)
        self.K=(np.eye(len(self.d))-self.A)@np.linalg.inv(gd)
        self.xd=np.zeros(len(self.d));self.x=np.zeros(n)
    def dc(self,u):return np.linalg.solve(self.G,self.B@np.array(u)+self.injection)
    def step(self,u):
        u=np.array(u);self.xd=self.A@self.xd+self.K@(self.bd@u+self.id)
        self.x[self.d]=self.xd;self.x[self.a]=self.ad@self.xd+self.ab@u+self.ai
        return self.x
    def ac(self,hz,u):return np.linalg.solve(self.G+2j*math.pi*hz*self.C,self.B@np.array(u))

class Delay:
    def __init__(self,c,rref,cref,bleed,dref,discharge,rf,cf,input_leak=0):
        self.node=p(c,cref,1);self.source=p(c,rref,1)
        assert p(c,rref,2)==self.node and p(c,bleed,1)==self.node
        assert p(c,bleed,2)=='GND' and p(c,cref,2)=='GND'
        cathode,anode=(3,1) if c[dref]['mpn'].startswith('BAS116') else (1,2)
        assert p(c,dref,cathode)==self.source
        assert {p(c,discharge,1),p(c,discharge,2)}=={self.node,p(c,dref,anode)}
        self.r=resistance(c,rref,rf);self.rb=resistance(c,bleed,rf)
        self.rd=resistance(c,discharge,rf)
        self.cap=value(c[cref]['value'])*cf.get(cref,1)
        self.leak=input_leak;self.v=0
    def step(self,drive):
        # BAS316 is an explicitly approximate 0.6V clamp during discharge.
        gd=1/self.rd if self.v>drive+0.6 else 0
        g=1/self.r+1/self.rb+gd
        target=(drive/self.r+(drive+0.6)*gd+self.leak)/g
        self.v=target+(self.v-target)*math.exp(-DT*g/self.cap)
        return self.v

class Logic:
    def __init__(self,c):
        self.c=c
        self.gates=[]
        for ref,co in c.items():
            if co.get('group')!='protection':continue
            if co.get('mpn','').startswith('SN74LVC1G08'):
                self.gates.append((ref,'AND',(p(c,ref,1),p(c,ref,2)),p(c,ref,4)))
            elif co.get('mpn','').startswith('SN74LVC2G00'):
                self.gates.extend([(ref+'A','NAND',(p(c,ref,1),p(c,ref,2)),p(c,ref,7)),
                                   (ref+'B','NAND',(p(c,ref,5),p(c,ref,6)),p(c,ref,3))])
        self.state={g[3]:False for g in self.gates}
    def step(self,nets):
        # Asynchronous fixed-point propagation through the ACTUAL NAND feedback.
        # Previous outputs retain the latch state, no hard-coded fault truth table.
        vals=self.state|nets
        for _ in range(16):
            change=False
            for _,kind,ins,out in self.gates:
                v=all(vals[i] for i in ins)
                if kind=='NAND':v=not v
                change|=vals.get(out)!=v;vals[out]=v
            if not change:break
        else:raise RuntimeError('logic did not settle; invalid/oscillating topology')
        self.state={g[3]:vals[g[3]] for g in self.gates}
        return vals


def raw_comparator(c,nets,offset=0):
    outputs=[]
    for out,minus,plus in [('2','4','5'),('1','6','7'),('13','10','11'),('14','8','9')]:
        assert p(c,'U203',out)=='DC_OK_RAW'
        outputs.append(nets[p(c,'U203',plus)]-nets[p(c,'U203',minus)]>=offset)
    return all(outputs)

def scenario(c,kind='startup',channel='L',fault=1,rail=3.3,cf=None,rf=None,
             schmitt='typ',leak=(0,0),bias=0,offset=0,input_leak=0,release=.020,drive_drop=.1,relay_input_leak=0):
    cf,rf=cf or {},rf or {}
    passive=Passive(c,rf,cf,leak,bias)
    arm=Delay(c,'R256','C243','R260','D200','R262',rf,cf,input_leak)
    delay=Delay(c,'R257','C244','R261','D202','R263',rf,cf,relay_input_leak)
    logic=Logic(c);armed=delayed=False;contacts=False
    last_cmd=False;cmd_edge=0;events=[];last={};ever_fault=False;valid=True
    # TI's 3V characterized values; scaling elsewhere is an engineering sweep.
    vtplus={'min':1.48,'typ':1.70,'max':1.92}[schmitt]*rail/3.0
    vtminus={'min':.89,'typ':1.04,'max':1.20}[schmitt]*rail/3.0
    armplus=({'min':1.88,'typ':2.085,'max':2.29}[schmitt]*rail/3.0
             if c['U206']['mpn'].startswith('SN74AUP') else vtplus)
    armminus=({'min':.88,'typ':1.06,'max':1.24}[schmitt]*rail/3.0
              if c['U206']['mpn'].startswith('SN74AUP') else vtminus)
    end=50 if schmitt=='max' else (55 if kind=='rearm' else 30)
    margin=10;clamp_time=None;raw_first_after_fault=None;latched_after_clear=None
    for k in range(int(end/DT)+1):
        t=k*DT
        ready=t>=.1
        if kind in ('ready_drop','power_off','brownout'):ready=ready and t<26
        if kind=='rearm' and 26<=t<29:ready=False
        vcc=rail
        if kind=='power_off' and t>=26.05:vcc=0
        if kind=='brownout' and t>=26.05:vcc=max(0,rail*(1-(t-26.05)/.1))
        if 0<vcc<1.65:valid=False
        out=[0.,0.]
        if kind in ('fault','rearm') and 20<=t<23:out['LR'.index(channel)]=fault
        if kind=='fault_persistent' and t>=20:out['LR'.index(channel)]=fault
        if kind=='audio' and t>=20:
            out=[4*math.sqrt(2)*math.sin(2*math.pi*20*(t-20))]*2
        x=passive.step([vcc,*out,0])
        ns=dict(zip(passive.nodes,x))
        operating=min(ns['SENSE_L'],ns['SENSE_R'])>=0 and max(ns['SENSE_L'],ns['SENSE_R'])<=7
        if not operating and clamp_time is None and t>=20:clamp_time=t
        raw=raw_comparator(c,ns,offset) if operating and vcc>=2 else False
        if t>=5 and kind=='audio':margin=min(margin,ns['SENSE_L']-ns['VTH_LO'],ns['VTH_HI']-ns['SENSE_L'])
        if t>=20 and not raw and raw_first_after_fault is None:raw_first_after_fault=t
        # Valid rails only: drive high is Vcc-0.1 for lightly loaded timing gates.
        # For invalid intermediate rails do NOT claim a known logic output.
        av=arm.step(max(0,vcc-drive_drop) if ready else 0)
        if not armed and av>=armplus:armed=True
        elif armed and av<=armminus:armed=False
        dv=delay.step(max(0,vcc-drive_drop) if last.get('RELAY_ENABLE',False) else 0)
        if not delayed and dv>=vtplus:delayed=True
        elif delayed and dv<=vtminus:delayed=False
        vals=logic.step({'AUDIO_READY':ready,'DC_ARM':armed,'DC_OK_RAW':raw,'RELAY_DELAYED':delayed})
        if vcc==0:
            vals['AMP_ENABLE']=False;vals['RELAY_COIL_CMD']=False
        cmd=vals['RELAY_COIL_CMD']
        if cmd!=last_cmd:cmd_edge=t;last_cmd=cmd
        if cmd and t-cmd_edge>=LIMITS['relay_operate_s']:contacts=True
        elif not cmd and t-cmd_edge>=release:contacts=False
        vals['CONTACTS_CLOSED']=contacts;vals['DC_ARM']=armed;vals['DC_OK_RAW']=raw
        for name in ('DC_ARM','DC_OK_RAW','DC_OK','AMP_ENABLE','RELAY_ENABLE','RELAY_COIL_CMD','CONTACTS_CLOSED'):
            if vals[name]!=last.get(name,False):events.append({'t_s':round(t,6),'net':name,'high':vals[name]})
        if t>=25.9 and t<26:latched_after_clear=not vals['DC_OK']
        last=vals
    return {'kind':kind,'channel':channel,'fault_v':fault,'rail_v':rail,'events':events,
            'audio_margin_v':None if margin==10 else margin,'latch_held_after_fault_clear':latched_after_clear,
            'clamp_region_entered_s':clamp_time,'all_rail_states_qualified':valid,
            'final_contacts_closed':contacts,'relay_release_assumption_s':release}

def edge(s,name,high,after=0):
    return next((e['t_s'] for e in s['events'] if e['net']==name and e['high']==high and e['t_s']>=after),None)

def dc_corners(c):
    # All fourteen resistor endpoints independently. Linear interval arithmetic
    # covers each of the eight comparator input currents independently, each
    # filter's leakage independently, reference-cap leakage, and both offsets.
    refs=Passive.refs+['R264','R265','R266','R267']
    lower=[];upper=[];min_gain=1e9;count=0;lower_margin=upper_margin=1e9
    for signs in itertools.product((.99,1.01),repeat=len(refs)):
        pas=Passive(c,dict(zip(refs,signs)));inv=np.linalg.inv(pas.G)
        ids={n:i for i,n in enumerate(pas.nodes)}
        for rail in (3.,3.6):
            base=inv@pas.B@np.array([rail,0,0,0])
            for channel in ('L','R'):
                slope=inv@pas.B[:,1+'LR'.index(channel)]
                for threshold in ('VTH_LO','VTH_HI'):
                    row=np.zeros(len(ids));row[ids['SENSE_'+channel]]=1;row[ids[threshold]]=-1
                    coeff=row@inv;gain=float(row@slope);min_gain=min(min_gain,gain)
                    delta_min=delta_max=0.
                    for pin in ('4','5','6','7','8','9','10','11'):
                        z=coeff[ids[p(c,'U203',pin)]]*50e-9
                        delta_min+=min(0,z);delta_max+=max(0,z)
                    for cap in ('C241','C242'):
                        z=abs(coeff[ids[p(c,cap,1)]])*580e-9
                        delta_min-=z;delta_max+=z
                    z=coeff[ids['VREF_1V65']]*-100e-9
                    delta_min+=min(0,z);delta_max+=max(0,z)
                    d=float(row@base)
                    if threshold=='VTH_LO':lower_margin=min(lower_margin,d+delta_min-.0055)
                    else:upper_margin=min(upper_margin,-d-delta_max-.0055)
                    bounds=[(-d-delta_max-.0055)/gain,(-d-delta_min+.0055)/gain]
                    (lower if threshold=='VTH_LO' else upper).extend(bounds)
            count+=1
    return {'lower_output_trip_v':[min(lower),max(lower)],'upper_output_trip_v':[min(upper),max(upper)],
            'minimum_differential_dc_gain':min_gain,'resistor_rail_corner_count':count,
            'lower_comparator_clearance_v':lower_margin,'upper_comparator_clearance_v':upper_margin,
            'filter_leakage_a':580e-9,'reference_cap_leakage_a':100e-9,
            'scope':'all14 independent1%R endpoints;3.0/3.6V;independent0..50nA on8 comparator inputs;±5.5mV offset;independent±580nA/filter plus0..100nA reference-cap leakage'}

def static(c):
    # These fail on the original100k source values without relying on logic idealizations.
    amp=LIMITS['logic_ioff_a']*value(c['R244']['value'])
    relay=LIMITS['logic_ioff_a']*value(c['R259']['value'])
    assert {p(c,'R244',1),p(c,'R244',2)}=={p(c,'U202',8),'GND'}
    assert p(c,'R259',1)==p(c,'Q200',1) and p(c,'R259',2)==p(c,'Q200',2)=='GND'
    assert p(c,'K200',1)=='V5_SYS' and p(c,'K200',8)==p(c,'Q200',3)
    assert p(c,'D201',1)=='V5_SYS' and p(c,'D201',2)==p(c,'Q200',3)
    assert [p(c,'K200',i) for i in (3,4,6,5)]==['AMP_L','HP_L','AMP_R','HP_R']
    return {'amp_enable_ioff_bound_v':amp,'amp_disabled_at_vcc_zero':amp<.65,
            'relay_base_ioff_bound_v':relay,'relay_base_below_0p2v':relay<=.2,
            'coil_nominal_ohm':237,'coil_max_current_5p25v_a':5.25/(237*.9),
            'opa_enable_and_contacts_pinmap_checked':True,
            'logic_source_urls_match_mpn':all('ti.com' in v.get('datasheet','') for v in c.values() if v.get('group')=='protection' and v.get('mpn','').startswith('SN74'))}

def passive_spice(c,outdir):
    lines=['Actual YAML passive protection network: independent ngspice cross-check',
           'VDD 3V3_D 0 PWL(0 0 1u 3.3)','VL AMP_L 0 SIN(0 5.65685424949238 20 2)',
           'VR AMP_R 0 SIN(0 5.65685424949238 20 2)']
    for ref in Passive(c).refs+Passive.caps:
        n1,n2=[p(c,ref,i).replace('GND','0') for i in (1,2)]
        lines.append(f'{ref} {n1} {n2} {value(c[ref]["value"]):.12g}')
    lines+=['.options method=gear reltol=1e-7 abstol=1e-12','.tran 0.5m 4 0 0.1m uic',
            '.control','run','set wr_singlescale','set wr_vecnames',
            'wrdata protection-passive.dat '+ ' '.join('v('+n+')' for n in Passive(c).nodes), 'quit','.endc','.end']
    cir=outdir/'protection-passive.cir';cir.write_text('\n'.join(lines)+'\n')
    run=subprocess.run(['timeout','20s','ngspice','-b',str(cir.resolve())],cwd=outdir,capture_output=True,text=True)
    (outdir/'protection-passive.log').write_text(run.stdout+run.stderr)
    if run.returncode:raise RuntimeError(f'ngspice exit{run.returncode}')
    a=np.loadtxt(outdir/'protection-passive.dat',skiprows=1)
    pas=Passive(c);t=a[:,0];mask=t>3.5
    expected=pas.dc([3.3,0,0,0]);ac=pas.ac(20,[0,4*math.sqrt(2),4*math.sqrt(2),0])
    # Residual startup settles slowly; compare time-domain exact ZOH at actual times.
    idx=0;errors=[]
    for k in range(int(4/DT)):
        tt=(k+1)*DT
        v=4*math.sqrt(2)*math.sin(2*math.pi*20*(tt-2)) if tt>=2 else 0
        x=pas.step([3.3,v,v,0])
        if tt>=.01:
            actual=np.array([np.interp(tt,t,a[:,j]) for j in range(1,len(pas.nodes)+1)])
            errors.append(np.max(abs(x-actual)))
    err=max(errors)
    return {'ngspice_exit':run.returncode,'max_exact_zoh_vs_spice_error_v':float(err),
            'agreement_within_0p003v':bool(err<.003),'nodes':pas.nodes,
            'dt_s':DT,'spice_max_step_s':.0001,'scope':'passive startup and20Hz; clamp diodes inactive',
            'dc_v':expected.tolist(),'audio_steady_peak_v':abs(ac).tolist()}

def corner_fault_times(c):
    from scipy.optimize import brentq
    rows=[]
    # Selected opposite resistor corners plus input-offset/leakage extremes.
    for rsign,leak,vos,ch,fault in itertools.product((.99,1.01),(-580e-9,580e-9),(-.0055,.0055),('L','R'),(-9.,-1.,1.,9.)):
        rf={r:rsign for r in Passive.refs+['R264','R265','R266','R267'] if r in c}
        pas=Passive(c,rf,{'C240':1.2,'C241':1.2,'C242':1.2},(leak,leak),50e-9)
        start=pas.dc([3.3,0,0,0]);u=[3.3,0,0,0];u[1+'LR'.index(ch)]=fault;finish=pas.dc(u)
        ids={n:i for i,n in enumerate(pas.nodes)}
        # Matrix generator for differential states recovered from source C/G.
        gd=pas.G[np.ix_(pas.d,pas.d)]-pas.G[np.ix_(pas.d,pas.a)]@pas.ga_inv@pas.G[np.ix_(pas.a,pas.d)]
        gen=-np.linalg.solve(pas.C[np.ix_(pas.d,pas.d)],gd)
        def margin(t):
            xd=finish[pas.d]+expm(gen*t)@(start[pas.d]-finish[pas.d])
            x=np.empty(len(ids));x[pas.d]=xd;x[pas.a]=pas.ad@xd+pas.ab@np.array(u)+pas.ai
            ns=dict(zip(pas.nodes,x));return min(ns['SENSE_L']-ns['VTH_LO']-vos,ns['VTH_HI']-ns['SENSE_L']-vos,ns['SENSE_R']-ns['VTH_LO']-vos,ns['VTH_HI']-ns['SENSE_R']-vos)
        trip=brentq(margin,0,5) if margin(0)>0 and margin(5)<0 else None
        rows.append({'channel':ch,'fault_v':fault,'trip_s':trip})
    return {'case_count':len(rows),'max_1v_trip_s':max(r['trip_s'] or 999 for r in rows if abs(r['fault_v'])==1),
            'max_9v_trip_s':max(r['trip_s'] or 999 for r in rows if abs(r['fault_v'])==9),
            'scope':'selected R endpoints,1.2C,±5.5mV and±580nA; nonlinear clamp begins after cutoff'}

def phase_envelope(c,corners):
    """Actual independent resistor corners; analytic arbitrary stereo phases.

    Complex sin/cos superposition includes an abrupt start at any phase. Modal
    envelopes bound the infinite tail; a second-derivative interpolation bound
    covers between time samples. Startup adds the zero-initial-state settling
    residual to the same worst DC clearance, then tests every audio start time
    conservatively using the global audio excursion.
    """
    results=[];times=np.arange(0,3+DT/2,DT);phase=np.exp(2j*math.pi*20*times)
    refs=Passive.refs+['R264','R265','R266','R267']
    # Earliest physical timing: maximum driver voltage, minimum threshold/C/R,
    # +input leakage; capacitor leakage only removes charge, never adds it.
    aup=c['U206']['mpn'].startswith('SN74AUP')
    il=(.5e-6 if aup else 5e-6)+(80e-9 if c['D200']['mpn'].startswith('BAS116') else 30e-6)
    rr=value(c['R256']['value'])*.99;rb=value(c['R260']['value'])*1.01
    cap=value(c['C243']['value'])*.5;vt=(1.88 if aup else 1.48)
    delays=[]
    for rail in (3.,3.6):
        target=(rail/rr+il)/(1/rr+1/rb)
        delays.append(-cap/(1/rr+1/rb)*math.log(1-vt/target))
    earliest=.1+min(delays)
    clearances=np.array([corners['lower_comparator_clearance_v'],corners['upper_comparator_clearance_v']]*2)
    clearance=float(min(clearances))
    for factors in itertools.product((.5,1.2),repeat=3):
        maximum=0.;startup_loss=0.;startup_combined=0.;tail_max=0.;case_count=0;audio_margin=startup_margin=1e9
        for signs in itertools.product((.99,1.01),repeat=len(refs)):
            pas=Passive(c,dict(zip(refs,signs)),dict(zip(Passive.caps,factors)))
            n=len(pas.nodes);ids={n:i for i,n in enumerate(pas.nodes)};rows=[]
            for channel in ('L','R'):
                for threshold in ('VTH_LO','VTH_HI'):
                    row=np.zeros(n);row[ids['SENSE_'+channel]]=1;row[ids[threshold]]=-1
                    if threshold=='VTH_HI':row=-row
                    rows.append(row)
            rows=np.array(rows);transform=np.zeros((n,len(pas.d)))
            transform[pas.d]=np.eye(len(pas.d));transform[pas.a]=pas.ad
            gd=pas.G[np.ix_(pas.d,pas.d)]-pas.G[np.ix_(pas.d,pas.a)]@pas.ga_inv@pas.G[np.ix_(pas.a,pas.d)]
            gen=-np.linalg.solve(pas.C[np.ix_(pas.d,pas.d)],gd);ev,vec=np.linalg.eig(gen)
            assert max(ev)<0
            modal=rows@transform@vec;expvals=np.exp(ev[:,None]*times)
            peaks=np.zeros((4,len(times)));tail=np.zeros(4);interpolation=np.zeros(4)
            for k in (1,2):
                u=np.zeros(4);u[k]=4*math.sqrt(2);h=pas.ac(20,u)
                coeff=modal*np.linalg.solve(vec,h[pas.d])
                steady=rows@h
                peaks+=abs(steady[:,None]*phase-coeff@expvals)
                tail+=abs(steady)+abs(coeff)@np.exp(ev*times[-1])
                interpolation+=(abs(steady)*(2*math.pi*20)**2+abs(coeff)@(ev**2))*DT**2/8
            row_excursion=np.maximum(peaks.max(axis=1)+interpolation,tail)
            excursion=float(row_excursion.max())
            maximum=max(maximum,excursion);tail_max=max(tail_max,float(tail.max()))
            audio_margin=min(audio_margin,float(min(clearances-row_excursion)))
            # 3.6 V maximizes passive startup residual. Modal triangle bound at
            # earliest arm covers every later time and every signed channel.
            dc=pas.dc([3.6,0,0,0]);coeff=modal*np.linalg.solve(vec,dc[pas.d])
            row_loss=np.maximum(coeff,0)@np.exp(ev*earliest)
            injection_max=np.zeros(n)
            for pin in ('4','5','6','7','8','9','10','11'):injection_max[ids[p(c,'U203',pin)]]+=50e-9
            for cref in ('C241','C242'):injection_max[ids[p(c,cref,1)]]+=580e-9
            injection_max[ids['VREF_1V65']]+=100e-9
            injected_modes=abs(np.linalg.solve(vec,np.linalg.inv(pas.G)[pas.d]))@injection_max
            row_loss+=abs(modal)@(injected_modes*np.exp(ev*earliest))
            loss=float(row_loss.max())
            startup_combined=max(startup_combined,float((row_excursion+row_loss).max()))
            startup_margin=min(startup_margin,float(min(clearances-row_excursion-row_loss)))
            startup_loss=max(startup_loss,loss);case_count+=1
        results.append({'cap_factors':dict(zip(Passive.caps,factors)),
                        'independent_resistor_corners':case_count,
                        'max_audio_excursion_including_interpolation_v':maximum,
                        'infinite_tail_bound_v':tail_max,
                        'startup_residual_bound_at_earliest_arm_v':startup_loss,
                        'startup_plus_audio_bound_v':startup_combined,
                        'earliest_arm_s':earliest,'conservative_dc_clearance_v':clearance,
                        'minimum_audio_margin_v':audio_margin,'minimum_startup_audio_margin_v':startup_margin,
                        'passed':audio_margin>0,
                        'startup_all_audio_start_times_passed':startup_margin>0})
        print(json.dumps({'phase_corner_completed':dict(zip(Passive.caps,factors)),
                          'audio_bound_v':maximum,'startup_bound_v':startup_loss,
                          'clearance_v':clearance}),flush=True)
    return {'method':'independent fourteen resistor endpoint corners; exact complex modal response, any stereo starting phase, interpolation and infinite-tail bounds',
            'earliest_arm_s':earliest,'corners':results,'passed':all(v['passed'] for v in results),
            'startup_passed':all(v['startup_all_audio_start_times_passed'] for v in results)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=PROJECT/'verification');ap.add_argument('--spice',action='store_true')
    ap.add_argument('--candidate',action='store_true',help='in-memory proposed passive/source changes only; never edits YAML')
    args=ap.parse_args();c=load();sourcehash=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if args.candidate:
        c['U206']['mpn']='SN74AUP1G17DBVR'
        c['D200']['mpn']='BAS116,215';c['D200']['pins']={'1':'DC_ARM_DISCHARGE','3':'AUDIO_READY'}
        c['R256']['value']='120k'
        for ref in ['R244','R259']:c[ref]['value']='10k'
        for ref in ['R247','R248','R249','R250']:c[ref]['value']='100k'
        for ref in ['C241','C242']:c[ref]['value']='33uF'
        c['C243']['value']='100uF';c['U203']['pins']['3']='VPOS'
        for channel,cap,ra,rb,rs,rg in [('L','C241','R247','R248','R264','R265'),('R','C242','R249','R250','R266','R267')]:
            filt='DC_FILTER_'+channel
            c[cap]['pins']['1']=filt;c[cap]['pins']['2']='GND';c[ra]['pins']['2']=filt;c[rb]['pins']['2']=filt
            c[rs]={'value':'49.9k','pins':{'1':filt,'2':'SENSE_'+channel}}
            c[rg]={'value':'10k','pins':{'1':'SENSE_'+channel,'2':'VREF_1V65'}}
        for ref,val in [('R251','6.81k'),('R252','100k'),('R253','2.87k'),('R254','100k')]:c[ref]['value']=val
        c['R251']['pins']['1']='VREF_1V65';c['R253']['pins']['1']='VREF_1V65'
    cases=[];checks=[]
    def check(name,condition,details):checks.append({'name':name,'passed':bool(condition),'details':details})
    st=static(c)
    check('power_off_amp_enable_pulldown',st['amp_disabled_at_vcc_zero'],st['amp_enable_ioff_bound_v'])
    check('power_off_relay_base_pulldown',st['relay_base_below_0p2v'],st['relay_base_ioff_bound_v'])
    for kind in ('startup','audio','ready_drop','rearm','power_off','brownout'):
        s=scenario(c,kind);cases.append(s)
        if kind in ('startup','audio'):check(kind,s['final_contacts_closed'] and (kind!='audio' or s['audio_margin_v']>0),s['audio_margin_v'])
        if kind in ('ready_drop','power_off','brownout'):
            e=edge(s,'RELAY_COIL_CMD',False,26)
            check(kind+'_command_off',e is not None and e-26<=DT,e)
        if kind=='rearm':check('latched_fault_and_rearm',s['latch_held_after_fault_clear'] and edge(s,'CONTACTS_CLOSED',True,29) is not None,s['events'])
    for ch,fault in itertools.product('LR',(-9.,-1.,1.,9.)):
        s=scenario(c,'fault',ch,fault);cases.append(s)
        e=edge(s,'RELAY_COIL_CMD',False,20);d=.2 if abs(fault)==9 else 1.5
        check(f'{ch}_{fault:+g}V_fault',e is not None and e-20<=d and s['latch_held_after_fault_clear'],None if e is None else e-20)
    arm_input=.5e-6 if c['U206']['mpn'].startswith('SN74AUP') else 5e-6
    diode_reverse=80e-9 if c['D200']['mpn'].startswith('BAS116') else 30e-6
    timing_early=arm_input+diode_reverse;timing_late=-arm_input-1e-6
    # Interacting delay corners: earliest arm vs slowest passive settling; and latest arm.
    for mode,cf,rf,il in [('early',{'C243':.5,'C244':.5,'C240':1.2,'C241':1.2,'C242':1.2},{'R256':.99,'R257':.99},timing_early),
                          ('late',{'C243':1.2,'C244':1.2,'C240':.5,'C241':.5,'C242':.5},{'R256':1.01,'R257':1.01},timing_late)]:
        s=scenario(c,'startup',cf=cf,rf=rf,schmitt='min' if mode=='early' else'max',input_leak=il,drive_drop=0 if mode=='early' else .1,leak=(-580e-9,-580e-9),bias=50e-9,offset=.0055,relay_input_leak=35e-6 if mode=='early' else -6e-6);cases.append(s)
        check(mode+'_startup',s['final_contacts_closed'],edge(s,'CONTACTS_CLOSED',True))
    corners=dc_corners(c)
    check('dc_threshold_corner_clearance',corners['lower_output_trip_v'][1]<-.15 and corners['upper_output_trip_v'][0]>.15,corners)
    check('dc_threshold_corner_detects_1V',corners['lower_output_trip_v'][0]>-1 and corners['upper_output_trip_v'][1]<1,corners)
    # Worst full-scale20Hz amplitude including abrupt sine start: low effective C.
    s=scenario(c,'audio',cf={'C241':.5,'C242':.5,'C240':.5},leak=(-580e-9,-580e-9),offset=.0055);cases.append(s)
    check('20Hz_minC_leakage_corner',s['final_contacts_closed'] and s['audio_margin_v']>.0055,s['audio_margin_v'])
    envelope=phase_envelope(c,corners) if 'R264' in c else None
    if envelope:
        check('20Hz_all_start_phases_and_cap_corners',envelope['passed'],envelope)
        check('fast_arm_slow_filter_all_audio_phases',envelope['startup_passed'],envelope)
    fault_corners=corner_fault_times(c) if 'R264' in c else None
    if fault_corners:check('corner_fault_latency',fault_corners['max_1v_trip_s']<2 and fault_corners['max_9v_trip_s']<.2,fault_corners)
    report={'source_sha256':sourcehash,'model_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'candidate_override':args.candidate,'limits':LIMITS,'static':st,'dc_corners':corners,
            'checks':checks,'scenarios':cases,'phase_envelope':envelope,'fault_corners':fault_corners,
            'limitations':['not a vendor comparator, logic, relay or OPA1622 model','0<Vcc<1.65V logic behavior unqualified',
            '20ms relay release is bench acceptance assumption, not guaranteed with SS14 flyback',
            'BAS116forwardclamp1.5V envelope requires selected diode/temperature check;80nAmax covers150C',
            'MLCC effective C>=50% nominal and arm capacitor leakage<=1uA must be demonstrated for selected order codes',
            'U206 earliest VT+>=1.88V across3.0..3.6V;latest<=2.29*Vcc/3;characterization envelope, guaranteed data only at3V',
            'U209 input leakage5uA and BAS316reverse30uA treated independently;forward discharge0.6V remains approximate',
            'fault waveforms imposed before relay and persist regardless of amplifier shutdown; silicon shutdown transient not modeled']}
    args.output.mkdir(parents=True,exist_ok=True)
    if args.spice:report['passive_spice']=passive_spice(c,args.output)
    report['passed']=all(x['passed'] for x in checks) and report.get('passive_spice',{}).get('agreement_within_0p003v',True)
    name='protection-candidate-results.json' if args.candidate else'protection-results.json'
    (args.output/name).write_text(json.dumps(report,indent=2,default=lambda x:x.item() if isinstance(x,np.generic) else str(x))+'\n')
    print(json.dumps({'passed':report['passed'],'checks':len(checks),'failed':[x for x in checks if not x['passed']], 'static':st,'dc_corners':corners,'passive_spice':report.get('passive_spice')},indent=2))
    raise SystemExit(0 if report['passed'] else 1)
if __name__=='__main__':main()
