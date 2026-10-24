#!/usr/bin/env python3
"""Source-derived power-sequence calculations, not vendor transistor models.

Run: python projects/desktop-dac-hd6xx/verification/power_checks.py
All numeric qualifications belong in power-results.json and power-validation.md.
The model intentionally separates logic/conditional envelope checks from hardware
checks for which the datasheets contain no sufficient guaranteed limits.
"""
from __future__ import annotations
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import yaml

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / 'design.yaml'
OUT = PROJECT / 'verification'
# Design qualification envelope. Never describe these as guaranteed silicon limits.
ENV = {
    'resistor_tolerance': .01, 'timing_cap_tolerance': .10,
    'reservoir_cap_tolerance': .20, 'ldo_cap_tolerance': .20,
    'adapter_max_v': 5.25, 'supervisor_nominal_trip_v': 4.65,
    'supervisor_trip_tolerance': .02, 'supervisor_assertion_s': 20e-6,
    'supervisor_release_min_s': .180, 'supervisor_release_max_s': .420,
    'hold_diode_drop_max_v': .50,
    'buffer_voh_loss_assumption_v': .50,
    'charge_diode_drop_assumption_v': .40,
    'charge_diode_reverse_leak_assumption_a': 2e-6,
    'schmitt_input_leak_a': 1e-6,
    'hcs_vtp_fraction_min': 2.99/6, 'hcs_vtp_fraction_max': 1.37/2,
    'hcs_vtm_fraction_min': 1.56/6, 'hcs_vtm_fraction_max': .71/2,
    'ldo_enable_off_max_v': .30, 'ldo_enable_on_min_v': .90,
    'ldo_enable_leak_a': .25e-6, 'lvc_ioff_a': 10e-6,
    'ldo_full_regulation_input_v': 3.60,
    'ldo_dropout_95percent_proxy_v': 3.3+.145,
    'load_D_a': .040, 'load_C_a': .020, 'load_A_a': .050,
    'hold_overhead_assumption_a': .005,
    'ldo_ramp_assumption_s': .00115,
    'ldo_discharge_typical_ohm': 150,
}

def val(v):
    m = re.fullmatch(r'([0-9.]+)\s*([pnumkM]?)(?:F|[oO]hm)?', str(v))
    if m is None:
        raise ValueError(f'Unsupported source value: {v}')
    return float(m[1])*{'':1,'p':1e-12,'n':1e-9,'u':1e-6,'m':1e-3,'k':1e3,'M':1e6}[m[2]]

def pin(c, ref, n):
    return c[ref]['pins'][str(n)]

def size(c, ref):
    return val(c[ref]['value'])

def pair(c, ref):
    return {pin(c, ref, 1), pin(c, ref, 2)}

def verify_topology(c):
    """Reject stale calculations if any source path changes."""
    for ref in ('U330','U333','U335'):
        assert c[ref]['mpn']=='SN74HCS14DR', ref
    assert c['U336']['mpn']=='SN74LVC2G08DCUR'
    assert c['U336']['pins']=={'1':'PWR_EN_C_RISE','2':'PWR_HOLD_C','7':'EN_C',
        '5':'PWR_EN_A_RISE','6':'PWR_HOLD_A','3':'EN_A','4':'GND','8':'3V3_D'}
    assert pin(c,'U335',1)=='ADAPTER_OK' and pin(c,'U335',4)=='ADAPTER_OK_BUF'
    assert pin(c,'U335',2)==pin(c,'U335',3)=='ADAPTER_OK_N'
    assert pin(c,'U335',14)==pin(c,'U333',14)=='V5_HOLD'
    assert pin(c,'U330',14)=='3V3_D'
    assert [pin(c,'U330',p) for p in (1,2,3,4,5,6,9,8,11,10,13,12)]==[
        'PWR_C_DELAY_RC','PWR_C_DELAY_N','PWR_C_DELAY_N','PWR_EN_C_RISE',
        'PWR_A_DELAY_RC','PWR_A_DELAY_N','PWR_A_DELAY_N','PWR_EN_A_RISE',
        'PWR_DAC_DELAY_RC','PWR_DAC_DELAY_N','PWR_DAC_DELAY_N','PWR_DAC_EN_DELAY']
    assert [pin(c,'U333',p) for p in (1,2,3,4,5,6,9,8,11,10,13,12)]==[
        'PWR_HOLD_A_RC','PWR_HOLD_A_N','PWR_HOLD_A_N','PWR_HOLD_A',
        'PWR_HOLD_C_RC','PWR_HOLD_C_N','PWR_HOLD_C_N','PWR_HOLD_C',
        'PWR_HOLD_D_RC','PWR_HOLD_D_N','PWR_HOLD_D_N','EN_D']
    assert pair(c,'R352')=={'V5_HOLD','ADAPTER_OK'}
    assert pair(c,'R365')=={'ADAPTER_OK_BUF','PWR_HOLD_CHARGE_LOGIC'}
    users=[(r,p) for r,d in c.items() for p,n in d.get('pins',{}).items() if n=='ADAPTER_OK']
    assert set(users)=={('R352','2'),('U334','1'),('U335','1')}, users
    for ref,rc,r,cap in [('D340','PWR_HOLD_A_RC','R360','C342'),
                          ('D341','PWR_HOLD_C_RC','R361','C343'),
                          ('D342','PWR_HOLD_D_RC','R362','C344')]:
        assert pin(c,ref,1)==rc and pin(c,ref,2)=='PWR_HOLD_CHARGE_LOGIC'
        assert pair(c,r)==pair(c,cap)=={rc,'GND'}
    for ref,en in [('R366','EN_C'),('R367','EN_A')]:
        assert pair(c,ref)=={en,'GND'}
    for ref in ('R363','R364','D343','D344','D345','D346'):
        assert ref not in c, f'Obsolete diode-AND path: {ref}'
    assert pin(c,'D347',1)==pin(c,'D348',1)=='ADAPTER_OK_BUF'
    assert pin(c,'D347',2)=='PWR_GOOD' and pin(c,'D348',2)=='DAC_EN'
    assert pair(c,'R354')=={'V5_SYS','PWR_HOLD_CHARGE'}
    assert pin(c,'D301',1)=='V5_HOLD' and pin(c,'D301',2)=='PWR_HOLD_CHARGE'
    assert pair(c,'C340')=={'V5_HOLD','GND'}
    for ref,en,rail in [('U320','EN_D','3V3_D'),('U321','EN_C','3V3_C'),('U322','EN_A','3V3_A')]:
        assert c[ref]['mpn']=='TPS7A2033PDBVR'
        assert (pin(c,ref,1),pin(c,ref,3),pin(c,ref,5))==('V5_HOLD',en,rail)
    for r,cap,rail,rc in [('R330','C330','3V3_D','PWR_C_DELAY_RC'),
                         ('R331','C331','3V3_C','PWR_A_DELAY_RC'),
                         ('R332','C332','3V3_A','PWR_DAC_DELAY_RC')]:
        assert pair(c,r)=={rail,rc} and pair(c,cap)=={rc,'GND'}
    assert [pin(c,'U102',n) for n in (1,2,4,5,6)]==[
        'PWR_GOOD','USB_CABLE','USB_MUTE_N','USB_PCM_ONLY','STREAM_OK']
    assert [pin(c,'U102',n) for n in (9,10,12,13,8)]==[
        'STREAM_OK','DAC_EN','DAC_DELAY_OK','3V3_D','AUDIO_READY']
    assert pin(c,'U100',18)=='DAC_EN'

def rc_time(r, cap, start, finish, asymptote):
    if not start > finish > asymptote:
        raise ValueError((start, finish, asymptote))
    return r*cap*math.log((start-asymptote)/(finish-asymptote))

def rail_caps(c):
    return {rail: sum(size(c,r) for r,d in c.items() if r.startswith('C')
                 and set(d.get('pins',{}).values())=={rail,'GND'})
            for rail in ('3V3_A','3V3_C','3V3_D')}

def shutdown_envelope(c, initial_hold, charge_fraction_min=1.0):
    """Interval bounds for ONE shared initial supply, independent gate/RC corners.

    Falling thresholds may see any hold voltage down to3.6V. We first calculate
    with this lower bound, then confirm calculated reservoir voltage stays above
    it. This avoids assuming constant supply in a reservoir discharge calculation.
    Diode leakage hastens falling RCs; positive gate leakage delays them.
    """
    e=ENV; rlo=1-e['resistor_tolerance']; rhi=1+e['resistor_tolerance']
    clo=1-e['timing_cap_tolerance']; chi=1+e['timing_cap_tolerance']
    charge_min=size(c,'R365')*rlo; charge_max=size(c,'R365')*rhi
    gs_max=sum(1/(size(c,r)*rlo) for r in ('R360','R361','R362'))
    gs_min=sum(1/(size(c,r)*rhi) for r in ('R360','R361','R362'))
    # Three gate leakage currents sink during initial charging at the low corner.
    v0_min=charge_fraction_min*(initial_hold-e['buffer_voh_loss_assumption_v']-
            e['charge_diode_drop_assumption_v']-charge_max*3*e['schmitt_input_leak_a'])/(1+charge_max*gs_max)
    # No diode/driver voltage loss is a safe high bound for the latest fall.
    v0_max=initial_hold/(1+charge_min*gs_min)+charge_max*3*e['schmitt_input_leak_a']
    timings={}
    for rail,r,cap in [('A','R360','C342'),('C','R361','C343'),('D','R362','C344')]:
        early=rc_time(size(c,r)*rlo,size(c,cap)*clo,v0_min,
                      e['hcs_vtm_fraction_max']*initial_hold,
                      -(e['schmitt_input_leak_a']+e['charge_diode_reverse_leak_assumption_a'])*size(c,r)*rlo)
        late=rc_time(size(c,r)*rhi,size(c,cap)*chi,v0_max,
                     e['hcs_vtm_fraction_min']*e['ldo_full_regulation_input_v'],
                     e['schmitt_input_leak_a']*size(c,r)*rhi)
        timings[rail]={'earliest_s':early,'latest_s':late}
    q=sum(e[f'load_{rail}_a']*timings[rail]['latest_s'] for rail in ('A','C','D'))
    q+=e['hold_overhead_assumption_a']*timings['D']['latest_s']
    q+=(sum(e[f'load_{rail}_a'] for rail in ('A','C','D'))+e['hold_overhead_assumption_a'])*e['supervisor_assertion_s']
    end_hold=initial_hold-q/(size(c,'C340')*(1-e['reservoir_cap_tolerance']))
    return {'initial_hold_v':initial_hold,'charged_rc_min_v':v0_min,
       'charge_threshold_margin_v':v0_min-e['hcs_vtp_fraction_max']*initial_hold,
       'timing':timings,'hold_charge_c':q,'end_hold_v':end_hold,
       'regulation_margin_v':end_hold-e['ldo_full_regulation_input_v'],
       'A_before_C_margin_s':timings['C']['earliest_s']-timings['A']['latest_s'],
       'C_before_D_margin_s':timings['D']['earliest_s']-timings['C']['latest_s']}

def startup_envelope(c):
    e=ENV; rows=[]
    for r,cap,stage in [('R330','C330','AVDD_to_VCCA'),('R331','C331','VCCA_to_AVCC'),('R332','C332','AVCC_to_DAC_EN')]:
        times=[]
        for rf,cf,v,vlogic,ratio,leak in itertools.product((.99,1.01),(.9,1.1),(3.3*.985,3.3*1.015),(3.3*.985,3.3*1.015),
                             (e['hcs_vtp_fraction_min'],e['hcs_vtp_fraction_max']),(-1e-6,1e-6)):
            if stage=='AVDD_to_VCCA' and v!=vlogic:continue
            asymptote=v+leak*size(c,r)*rf
            threshold=ratio*vlogic
            assert asymptote>threshold
            times.append(-size(c,r)*rf*size(c,cap)*cf*math.log(1-threshold/asymptote))
        rows.append({'stage':stage,'earliest_s':min(times),'latest_s':max(times),
                     'meaning':'RC delay after an ideal rail step; actual LDO ramp must be measured'})
    return rows

def interrupted_startup(c, initial_hold, startup):
    """Cutpoint analysis over the same qualified corners used for normal shutdown.

    All capacitors are charged through the common R365/anode. Unequalforwarddrops
    and leakinginputs are covered by the lowestfinalRCvoltage plus the upper
    R365*sum(C)timeconstant (ignoringbleedconductance onlyslowscharging). Anycut
    beforeearliestCcanenable has atmostDpowered; anylatercut has RCssettled to
    betterthan99.9%, whosefallorder ischecked again withthatinitialvoltageloss.
    """
    tau=size(c,'R365')*1.01*sum(size(c,cap)*1.1 for cap in ('C342','C343','C344'))
    earliest_c=startup[0]['earliest_s']  # Dcannotbeenabled beforeADAPTER_OK.
    earliest_a=earliest_c+startup[1]['earliest_s']
    settled=1-math.exp(-earliest_c/tau)
    corner_rows=[]
    for i in range(101):
        vh=initial_hold+(ENV['adapter_max_v']-initial_hold)*i/100
        r=shutdown_envelope(c,vh,.999)
        final=r['charged_rc_min_v']/.999
        latest_d=-tau*math.log(1-ENV['hcs_vtp_fraction_max']*vh/final)
        cuts=[]
        for t in (latest_d/2,latest_d,earliest_c*.5,earliest_c,earliest_a):
            cuts.append({'cut_after_supervisor_release_s':t,
                         'rc_voltage_lower_bound_v':final*(1-math.exp(-t/tau)),
                         'possible_enabled_rails':(['D'] if t<earliest_c else ['D','C'] if t<earliest_a else ['D','C','A'])})
        corner_rows.append({'initial_hold_v':vh,'latest_D_enable_s':latest_d,'cuts':cuts,
                            'A_before_C_margin_s':r['A_before_C_margin_s'],
                            'C_before_D_margin_s':r['C_before_D_margin_s']})
    return {'charge_tau_upper_bound_s':tau,'fraction_charged_before_earliest_C':settled,
            'earliest_C_possible_s':earliest_c,'earliest_A_possible_s':earliest_a,
            'low_hold_cutpoints':corner_rows[0],'number_of_supply_corners':len(corner_rows),
            'minimum_A_before_C_margin_s':min(r['A_before_C_margin_s'] for r in corner_rows),
            'minimum_C_before_D_margin_s':min(r['C_before_D_margin_s'] for r in corner_rows),
            'limitations':'Valid logic and qualified driver/diode envelope; IC behavior below minimum supply is not modeled.'}

def waveform(c, result):
    """Representative startup, short brownout, restart, and unplug event trace.

    Rails use explicitly assumed linear startup and150ohm typical discharge.
    This trace cannot establish real rail voltage order. Enable events use source
    RCs and the HCS typical4.5V threshold ratio; brownout resets supervisor release.
    """
    e=ENV; caps=rail_caps(c); nominal={}
    for rail,r,cap in [('A','R360','C342'),('C','R361','C343'),('D','R362','C344')]:
        nominal[rail]=size(c,r)*size(c,cap)*math.log((4.3-.24)/(4.3*(1.36/4.5)))
    rise={rail:size(c,r)*size(c,cap)*-math.log(1-2.47/4.5) for rail,r,cap in
          [('C','R330','C330'),('A','R331','C331'),('DAC','R332','C332')]}
    # While all three ideal charge diodes conduct their capacitors charge together.
    # A sharedseries resistor therefore sees their SUM, not onlytheDcapacitor.
    rcharge=size(c,'R365')
    parallel=1/sum(1/size(c,r) for r in ('R360','R361','R362'))
    charge_tau=sum(size(c,cap) for cap in ('C342','C343','C344'))/(1/rcharge+1/parallel)
    charge_final=(4.3-.24)/(1+rcharge/parallel)
    d_on_delay=-charge_tau*math.log(1-(2.47/4.5)*4.3/charge_final)
    events=[]
    for start in (.300,.705):
        times={'D':start+d_on_delay,'C':start+d_on_delay+rise['C']+e['ldo_ramp_assumption_s']}
        times['A']=times['C']+rise['A']+e['ldo_ramp_assumption_s']
        times['DAC']=times['A']+rise['DAC']+e['ldo_ramp_assumption_s']
        for rail,t in times.items(): events.append((t,rail,1))
    for lost in (.400,.800):
        events.append((lost+e['supervisor_assertion_s'],'DAC',0))
        for rail,delay in nominal.items():events.append((lost+e['supervisor_assertion_s']+delay,rail,0))
    events.sort(); flags={k:0 for k in ('A','C','D','DAC')}; v={k:0.0 for k in ('A','C','D')}
    last_on={}; last_off={}; idx=0; dt=10e-6; hold=0.; rows=[]
    for i in range(85001):
        t=i*dt
        while idx<len(events) and events[idx][0]<=t:
            et,k,state=events[idx];flags[k]=state
            if state:last_on[k]=et
            else:last_off[k]=(et,v.get(k,0))
            idx+=1
        adapter=5.0 if t<.4 or .405<=t<.8 else 0.
        total=e['hold_overhead_assumption_a']+sum(e[f'load_{k}_a']*flags[k] for k in ('A','C','D'))
        target=adapter-.35-total*size(c,'R354')
        if target>hold:hold=target+(hold-target)*math.exp(-dt/(size(c,'R354')*size(c,'C340')))
        else:hold=max(0,hold-total*dt/size(c,'C340'))
        for k in v:
            if flags[k]:v[k]=3.3*min(1,(t-last_on[k])/e['ldo_ramp_assumption_s'])
            elif k in last_off:
                et,init=last_off[k];v[k]=init*math.exp(-(t-et)/(e['ldo_discharge_typical_ohm']*caps[f'3V3_{k}']))
        if i%10==0:rows.append([t,adapter,hold,v['D'],v['C'],v['A'],flags['DAC']*3.3,flags['D'],flags['C'],flags['A']])
    with (OUT/'power-waveforms.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['time_s','V5_SYS','V5_HOLD','AVDD','VCCA','AVCC','DAC_EN','EN_D_bool','EN_C_bool','EN_A_bool']);w.writerows(rows)
    return {'events':[{'time_s':t,'signal':k,'high':bool(s)} for t,k,s in events],
       'brownout_duration_s':.005,'brownout_restart_at_s':.705,
       'brownout_no_reenable_during_release_delay':not any(.40002<t<.705 and s for t,k,s in events),
       'scope':'Representative event trace; linear LDO ramp and typical discharge are assumptions.'}

def main():
    raw=SOURCE.read_bytes();d=yaml.safe_load(raw);c=d['components'];verify_topology(c)
    e=ENV; load=sum(e[f'load_{k}_a'] for k in ('A','C','D'))+e['hold_overhead_assumption_a']
    initial=e['supervisor_nominal_trip_v']*(1-e['supervisor_trip_tolerance'])-e['hold_diode_drop_max_v']-size(c,'R354')*1.01*load
    low=shutdown_envelope(c,initial)
    # Sweep common initial supply; independently adverse RC/gate corners per rail.
    corners=[shutdown_envelope(c,initial+(e['adapter_max_v']-initial)*i/100) for i in range(101)]
    checks={
      'topology_matches_model':True,
      'charged_RCs_reach_HCS_rising_threshold':min(x['charge_threshold_margin_v'] for x in corners)>0,
      'conditional_enable_A_before_C':min(x['A_before_C_margin_s'] for x in corners)>0,
      'conditional_enable_C_before_D':min(x['C_before_D_margin_s'] for x in corners)>0,
      'conditional_reservoir_above_full_regulation_input':min(x['regulation_margin_v'] for x in corners)>0,
      'supervisor_sink_below_1mA':e['adapter_max_v']/(size(c,'R352')*.99)+e['schmitt_input_leak_a']<.001,
      'ldo_disabled_powered_logic':.1<e['ldo_enable_off_max_v'],
      'ldo_disabled_unpowered_logic':max(size(c,r)*1.01*e['lvc_ioff_a'] for r in ('R366','R367'))<e['ldo_enable_off_max_v'],
      'DAC_off_before_earliest_analog_disable':low['timing']['A']['earliest_s']>2e-6,
    }
    # Buffer must sink both clamp pullups plus the chargedDACdelay capacitor.
    sink=3.3*1.015*(1/(size(c,'R343')*.99)+1/(size(c,'R342')*.99)+1/(size(c,'R118')*.99))
    checks['adapter_buffer_sink_below_4mA']=sink<.004
    startup=startup_envelope(c)
    checks['conditional_startup_AVDD_delay_over_200us']=startup[0]['earliest_s']>.0002
    # Any interruption beforeCcanstart has onlyDenergized. For later interruptions
    # theholdRCs have settled; comparethesource-derivedworstchargeconstantwiththe
    # earliestCdelay. A99.9%chargedstateisusedandreported,notanICpoweronclaim.
    tau_charge=size(c,'R365')*1.01*sum(size(c,cap)*1.1 for cap in ('C342','C343','C344'))
    settle_s=-tau_charge*math.log(.001)
    checks['hold_RCs_settle_before_C_can_start']=settle_s<startup[0]['earliest_s']
    checks['brownout_release_exceeds_latest_shutdown']=e['supervisor_release_min_s']>max(x['timing']['D']['latest_s'] for x in corners)
    interrupted=interrupted_startup(c,initial,startup)
    checks['interrupted_startup_hold_RCs_charged_before_C']=interrupted['fraction_charged_before_earliest_C']>.999
    checks['interrupted_startup_maintains_reverse_enable_order']=min(interrupted['minimum_A_before_C_margin_s'],interrupted['minimum_C_before_D_margin_s'])>0
    # Full combinational truth table from the validatedU102pinmap.
    truths=[]
    for pg,usb,unmute,pcm,dac,delay in itertools.product((False,True),repeat=6):
        stream=pg and usb and unmute and pcm
        audio=stream and dac and delay
        assert audio==all((pg,usb,unmute,pcm,dac,delay))
        truths.append(audio)
    checks['AUDIO_READY_requires_all_6_qualifiers']=sum(truths)==1
    r={'source_sha256':hashlib.sha256(raw).hexdigest(),'model_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
      'analysis':'Qualified analytical RC bounds + representative event model. No vendor powerICsimulation.',
      'assumptions':ENV,'source_values':{r:c[r]['value'] for r in ('R354','C340','R352','R365','R360','R361','R362','C342','C343','C344','R330','C330','R331','C331','R332','C332','R118')},
      'source_rail_capacitance_f':rail_caps(c),'checks':checks,'low_hold_shutdown':low,
      'sweep':{'initial_hold_range_v':[initial,e['adapter_max_v']],'points':len(corners),
              'minimum_charge_threshold_margin_v':min(x['charge_threshold_margin_v'] for x in corners),
              'minimum_regulation_margin_v':min(x['regulation_margin_v'] for x in corners),
              'minimum_A_before_C_margin_s':min(x['A_before_C_margin_s'] for x in corners),
              'minimum_C_before_D_margin_s':min(x['C_before_D_margin_s'] for x in corners)},
      'buffer_sink_peak_bound_a':sink,'startup_RC_delays':startup,
      'hold_RC_99_9percent_charge_bound_s':settle_s,
      'interrupted_startup':interrupted,
      'hardware_status':'not checked',
      'not_proven':['Mode 8 actual maximum load','HCS timing between published supply points',
       'HCS loaded VOH/VOL at falling reservoir voltage','BAT54 cold forward and hot reverse leakage',
       'B340 forward drop at temperature','Capacitor effective value and aging leakage',
       'Real LDO startup/discharge and output rail order','Fast dips below 20 us; invalid logic supply during startup',
       'Converter dynamics/ripple','Hot-plug inrush and resistor pulse','USB back-power','Hard shorts'],
      'rejected_alternative':'LM3880 completes interrupted startup then waits about 120 ms before power down; a 4700 uF reservoir cannot cover 110 mA for 120 ms.'}
    r['waveform']=waveform(c,r)
    checks['brownout_reset_release_blocks_early_restart']=r['waveform']['brownout_no_reenable_during_release_delay']
    r['qualified_checks_passed']=all(checks.values())
    (OUT/'power-results.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({'qualified_checks_passed':r['qualified_checks_passed'],'failed_checks':[k for k,v in checks.items() if not v],
                      'source_sha256':r['source_sha256'],'low_hold_shutdown':low,'hardware_status':r['hardware_status']},indent=2))
    if not r['qualified_checks_passed']:raise SystemExit(1)

if __name__=='__main__':main()
