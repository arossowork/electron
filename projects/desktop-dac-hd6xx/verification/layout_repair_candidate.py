#!/usr/bin/env python3
"""Rebuild and selectively repair the preserved cold candidate through hwflow APIs.

Run from the repository in the pinned offline container with PYTHONPATH=/workspace/src,
HWFLOW_REPAIR_BUDGET_S=300 and an outer timeout of 450 seconds. This is diagnostic;
it does not mark the CLI layout stage passed or publish a route-cache entry.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import traceback
import zipfile
from collections import Counter

os.environ.setdefault('HWFLOW_REPAIR_BUDGET_S', '300')
from hwflow import budgets, floorplan, kicad, pcb, postlayout, prune, repair, sch, sexpr
from hwflow.design import Design
from hwflow.libs import Libs
from hwflow.placer import Placer, build_parts

PROJECT = Path(__file__).resolve().parents[1]
EVIDENCE = PROJECT / 'verification'
PARSER = argparse.ArgumentParser(description=__doc__)
PARSER.add_argument('--checkpoint', type=Path)
PARSER.add_argument('--label', default='layout-repair')
OPTIONS = PARSER.parse_args()
assert OPTIONS.label.replace('-', '').isalnum(), 'label must be alphanumeric/hyphen'
LABEL = OPTIONS.label
CHECKPOINT = OPTIONS.checkpoint.resolve() if OPTIONS.checkpoint else None
OUT = PROJECT / 'build' / LABEL
EXPECTED_SOURCE = '8d99069b290cd461d95c22192f326537330d9ada447b19f2e4fa2150d384b96f'
EXPECTED_INPUT = 'de6d75a13b382899a3d9f2174a472d69d375cd0b34f45289457206c84eff81b8'
EXPECTED_CONNECTIONS = 6
if CHECKPOINT:
    with zipfile.ZipFile(CHECKPOINT) as checkpoint_archive:
        CHECKPOINT_MANIFEST = json.loads(checkpoint_archive.read('manifest.json'))
    assert CHECKPOINT_MANIFEST['source_sha256'] == EXPECTED_SOURCE
    EXPECTED_INPUT = CHECKPOINT_MANIFEST['board_sha256']
    EXPECTED_CONNECTIONS = CHECKPOINT_MANIFEST['expected_unconnected']
    assert CHECKPOINT_MANIFEST['non_connectivity_errors'] == 0
RESULT = EVIDENCE / f'{LABEL}-results.json'
T0 = time.monotonic()
report = {'run_label': LABEL, 'checkpoint': str(CHECKPOINT) if CHECKPOINT else None,
          'kind': 'selective_repair_diagnostic', 'cli_layout_acceptance': 'not_run',
          'route_cache_written': False, 'repair_budget_s': 300, 'hard_outer_timeout_s': 450}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save():
    report['elapsed_s'] = round(time.monotonic() - T0, 3)
    RESULT.write_text(json.dumps(report, indent=2) + '\n')


def counts(drc):
    return {'errors': sum(x['sev'] == 'error' for x in drc),
            'unconnected': sum(x['type'] == 'unconnected' for x in drc),
            'non_connectivity_errors': sum(x['sev'] == 'error' and x['type'] != 'unconnected' for x in drc),
            'by_severity': dict(Counter(x['sev'] for x in drc)),
            'by_type': dict(Counter(x['type'] for x in drc)),
            'errors_with_locations': [x for x in drc if x['sev'] == 'error']}


def require_candidate():
    if CHECKPOINT:
        folder = EVIDENCE / f'{LABEL}-input'
        folder.mkdir(exist_ok=True)
        name = CHECKPOINT_MANIFEST['board']
        names = [name, Path(name).with_suffix('.kicad_pro').name, Path(name).with_suffix('.kicad_dru').name, 'placement.json']
        with zipfile.ZipFile(CHECKPOINT) as archive:
            for filename in names:
                assert Path(filename).name == filename
                content = archive.read(filename)
                assert hashlib.sha256(content).hexdigest() == CHECKPOINT_MANIFEST['files_sha256'][filename]
                dest = folder / filename
                if not dest.exists():
                    dest.write_bytes(content)
                assert sha(dest) == CHECKPOINT_MANIFEST['files_sha256'][filename]
        path = folder / name
        assert sha(path) == EXPECTED_INPUT
        return path
    folder = EVIDENCE / 'layout-cold-preserved'
    folder.mkdir(exist_ok=True)
    names = ['desktop_dac_hd6xx_krt2n0.kicad_pcb', 'desktop_dac_hd6xx_krt2n0.kicad_pro', 'placement.json']
    with zipfile.ZipFile(EVIDENCE / 'layout-cold-candidates.zip') as archive:
        for name in names:
            dest = folder / name
            if not dest.exists():
                dest.write_bytes(archive.read(name))
    path = folder / names[0]
    assert sha(path) == EXPECTED_INPUT, 'preserved input hash differs'
    return path


def source_parity(design, data):
    assert set(data['footprints']) == {r for r,c in design.components.items() if not c.get('virtual')}
    n = 0
    mismatches = []
    for ref,c in design.components.items():
        if c.get('virtual'):
            continue
        fp = data['footprints'][ref]
        if fp['footprint'] != c.get('footprint'):
            mismatches.append([ref, 'footprint', fp['footprint'], c.get('footprint')])
        for pin,net in c.pins.items():
            hits = [p['net'] for p in fp['pads'] if p['number'] == str(pin)]
            n += 1
            if not hits or any(x != net for x in hits):
                mismatches.append([ref, pin, net, hits])
    assert n == 703 and not mismatches, mismatches
    return {'components': len(data['footprints']), 'source_assigned_pins': n, 'mismatches': mismatches}


def geometry(design, data, parts):
    for ref, part in parts.items():
        fp = data['footprints'][ref]
        part.x, part.y, part.r = fp['x'], fp['y'], fp['r']
    placer = Placer(design, parts, design.board['width_mm'], design.board['height_mm'])
    fixed = {}
    for ref,part in parts.items():
        if not part.fixed:
            continue
        pl = design.components[ref].get('placement')
        actual = [part.x,part.y,part.r % 360]
        expected = [float(pl['x']),float(pl['y']),float(pl.get('rot',0)) % 360]
        assert all(abs(a-b) < 1e-6 for a,b in zip(actual,expected)), (ref,actual,expected)
        fixed[ref] = actual
    return {'placement_violations': placer.violations(), 'fixed_rule_violations': placer.fixed_violations(),
            'fixed_anchors': fixed, 'dimensions_mm': [design.board['width_mm'], design.board['height_mm']],
            'copper_layers': design.board['layers']}


def board_geometry(path):
    tree = sexpr.loads(path.read_text())
    edges = [g for key in ('gr_line','gr_rect') for g in sexpr.find_all(tree,key)
             if str(sexpr.value(g,'layer')) == 'Edge.Cuts']
    pts = [sexpr.find(g,key)[1:3] for g in edges for key in ('start','end') if sexpr.find(g,key)]
    bounds = [min(float(p[0]) for p in pts), min(float(p[1]) for p in pts),
              max(float(p[0]) for p in pts), max(float(p[1]) for p in pts)]
    layers = [str(x[1]) for x in sexpr.find(tree,'layers')[1:] if isinstance(x,list) and str(x[1]).endswith('.Cu')]
    assert bounds == [0.,0.,140.,80.], bounds
    assert layers == ['F.Cu','In1.Cu','In2.Cu','B.Cu'], layers
    sizes = sorted({(float(sexpr.value(v,'size')),float(sexpr.value(v,'drill')),
                     tuple(str(x) for x in sexpr.find(v,'layers')[1:])) for v in sexpr.find_all(tree,'via')})
    return {'edge_bounds_mm': bounds, 'layers': layers, 'via_size_drill_layers': sizes}


def static_inspection(design, board):
    extraction = postlayout.Extraction(design, board)
    findings = postlayout.layout_checks(design, extraction)
    fps = extraction.board['footprints']
    decaps = postlayout.decap_paths(design, extraction)
    for row in decaps:
        row['cap_at_mm'] = [fps[row['cap']]['x'], fps[row['cap']]['y']]
        row['ic_at_mm'] = [fps[row['ic']]['x'], fps[row['ic']]['y']]
        row['over_limit'] = row['mm'] > row['limit']
    current = []
    for net in design.nets():
        info = design.net_info(net)
        if not info.get('current_a'):
            continue
        need = pcb.ipc2221_width_mm(float(info['current_a']), design.board['copper_oz'], float(info.get('max_rise_c',10)))
        segments = [s for s in extraction.segs if s['net'] == net and s['w'] + 1e-6 < need]
        current.append({'net': net, 'current_a': info['current_a'], 'required_width_mm': need,
                        'thin_segments': [{k:s[k] for k in ('x1','y1','x2','y2','layer','w','len')} for s in segments]})
    for f in findings:
        if f['code'] in ('via-in-pad','decap-distance'):
            refs = [ref for ref in fps if __import__('re').search(r'\b'+__import__('re').escape(ref)+r'\b',f['msg'])]
            f['referenced_footprint_locations_mm'] = {ref:[fps[ref]['x'],fps[ref]['y']] for ref in refs}
    edge_nets = [n for n in design.nets() if design.net_info(n).get('edge_ns') is not None]
    edge_parts = [r for r,c in design.components.items() if c.get('edge_ns') is not None]
    result = {'findings': findings, 'by_severity': dict(Counter(f['sev'] for f in findings)),
              'by_code': dict(Counter(f['code'] for f in findings)), 'decoupling_paths': decaps,
              'current_capacity': current, 'region_excursions': postlayout.region_excursions(design, extraction.board),
              'si_screen': postlayout.si_screen(design, extraction),
              'coverage': {'spice_or_ir_drop': 'not_run', 'declared_edge_rate_nets': edge_nets,
                           'declared_edge_rate_parts': edge_parts,
                           'return_path_and_si': 'not_checked_no_edge_rates' if not edge_nets and not edge_parts else 'screened',
                           'two_layer_return_path_check': 'not_applicable_four_layer_board',
                           'region_net_containment': 'screened' if floorplan.net_regions(design) else 'not_checked_no_regions_with_nets',
                           'current_nets_screened': len(current), 'functional_layout_signoff': False}}
    path = EVIDENCE / f'{LABEL}-inspection.json'
    path.write_text(json.dumps(result, indent=2) + '\n')
    return {'path': str(path), 'by_severity': result['by_severity'], 'by_code': result['by_code'],
            'decoupling_over_limit': sum(r['over_limit'] for r in decaps), 'coverage': result['coverage']}


def main():
    source = PROJECT / 'design.yaml'
    report['source_sha256_before'] = sha(source)
    report['harness_sha256'] = sha(__file__)
    if CHECKPOINT:
        report['checkpoint_sha256'] = sha(CHECKPOINT)
    assert report['source_sha256_before'] == EXPECTED_SOURCE
    report['engine_head'] = os.environ.get('HWFLOW_ENGINE_HEAD', 'unavailable_inside_isolated_worktree_mount')
    report['engine_source_sha256'] = {name: sha(Path('/workspace/src/hwflow') / name) for name in ('pcb.py','cli.py','repair.py','placer.py','postlayout.py','floorplan.py','regionroute.py')}
    candidate = require_candidate()
    report['input_board'] = str(candidate)
    report['input_sha256'] = sha(candidate)
    design = Design.load(PROJECT)
    libs = Libs(design=design)
    parts = build_parts(design, libs)
    original = pcb.read_board(candidate)
    report['input_parity'] = source_parity(design, original)
    report['input_geometry'] = board_geometry(candidate)
    expected_via = (float(design.board['via_dia_mm']), float(design.board['via_drill_mm']), ('F.Cu','B.Cu'))
    assert report['input_geometry']['via_size_drill_layers'] == [expected_via], report['input_geometry']
    report['input_placement'] = geometry(design, original, parts)
    assert not report['input_placement']['placement_violations'], report['input_placement']
    assert not report['input_placement']['fixed_rule_violations'], report['input_placement']
    design.budget_widths, budget_warnings = budgets.widths(design, parts)
    report['budget_width_warnings'] = budget_warnings
    thermal = {(round(v['x'],2),round(v['y'],2)) for v in pcb.thermal_vias(design,libs,parts)}
    tracks = [dict(s) for s in original['segments'] if s['net']]
    vias = [dict(v) for v in original['vias'] if v['net'] and (round(v['x'],2),round(v['y'],2)) not in thermal]
    OUT.mkdir(parents=True, exist_ok=True)
    board = OUT / f'{design.name}.kicad_pcb'
    pcb.write_project(OUT, design.name, design)
    kicad.write_lib_tables(design, libs, OUT)
    sch.write_schematic(design, libs, OUT / f'{design.name}.kicad_sch')
    def check(tr,vi,island=0,full=False):
        pcb.write_board(board, pcb.build_board(design,libs,parts,tr,vi,island_removal=island))
        return kicad.drc(board,refill=True,parity=True) if full else kicad.drc_fast(board)
    drc = check(tracks,vias,full=True)
    report['initial_drc'] = counts(drc)
    report['rebuilt_parity'] = source_parity(design, pcb.read_board(board))
    report['initial_plan'] = repair.plan(design,tracks,vias,drc)
    report['output_board'] = str(board)
    save()
    print(json.dumps({'initial_drc': report['initial_drc'], 'initial_plan': report['initial_plan']}), flush=True)
    assert report['initial_drc']['unconnected'] == EXPECTED_CONNECTIONS and report['initial_drc']['non_connectivity_errors'] == 0, report['initial_drc']
    initial_tracks, initial_vias = [dict(t) for t in tracks], [dict(v) for v in vias]
    initial_board_bytes, initial_full_drc = board.read_bytes(), [dict(v) for v in drc]
    initial_drc_bytes = board.with_suffix('.drc.json').read_bytes()
    initial_positions = {r:[f['x'],f['y'],f['r']] for r,f in pcb.read_board(board)['footprints'].items()}
    report['initial_board_sha256'] = hashlib.sha256(initial_board_bytes).hexdigest()
    report['initial_drc_sha256'] = hashlib.sha256(initial_drc_bytes).hexdigest()
    result = repair.repair(design,libs,parts,tracks,vias,drc,board,check,rounds=3,deadline=time.time()+300)
    report['repair'] = result['report']
    island = result['island']
    tracks,vias,drc,removed = prune.prune(result['tracks'],result['vias'],result['drc'],
        lambda tr,vi:check(tr,vi,island,full=True))
    drc = check(tracks,vias,island,full=True)
    report['pruned_dangling_items'] = removed
    candidate_counts = counts(drc)
    initial_counts = counts(initial_full_drc)
    worse = any(candidate_counts[k] > initial_counts[k] for k in ('errors','unconnected','non_connectivity_errors'))
    report['full_drc_acceptance'] = {'candidate':candidate_counts, 'initial':initial_counts, 'rejected':worse}
    if worse:
        tracks, vias, island = initial_tracks, initial_vias, 0
        drc = check(tracks,vias,island,full=True)
        report['full_drc_acceptance']['restored_initial_copper'] = True
    report['final_drc'] = counts(drc)
    data = pcb.read_board(board)
    report['final_parity'] = source_parity(design,data)
    final_positions = {r:[f['x'],f['y'],f['r']] for r,f in data['footprints'].items()}
    report['all_placements_unchanged'] = final_positions == initial_positions
    assert report['all_placements_unchanged'], 'repair moved a footprint'
    report['final_geometry'] = board_geometry(board)
    report['final_placement'] = geometry(design,data,parts)
    report['final_copper'] = {'segments':len(data['segments']),'vias':len(data['vias'])}
    report['final_sha256'] = sha(board)
    report['static_inspection'] = static_inspection(design,board)
    preview = OUT / 'pcb.svg'
    exported = kicad._run(['pcb','export','svg','--mode-single','--layers','F.Cu,F.SilkS,F.Mask,Edge.Cuts',
                          '--page-size-mode','2','--exclude-drawing-sheet','-o',str(preview),str(board)], timeout=30)
    report['native_svg'] = {'path':str(preview),'exit_code':exported.returncode,
                            'stdout':exported.stdout,'stderr':exported.stderr}
    report['source_sha256_after'] = sha(source)
    report['input_sha256_after'] = sha(candidate)
    assert report['source_sha256_after'] == report['source_sha256_before']
    assert report['input_sha256_after'] == report['input_sha256']
    report['status'] = 'copper_drc_clean_diagnostic' if not report['final_drc']['errors'] else 'partial_candidate_retained'
    save()
    print(json.dumps({k:report[k] for k in ('status','repair','final_drc','final_placement','static_inspection','output_board','elapsed_s')}),flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        report['status'] = 'failed_prerequisite_or_harness'
        report['exception'] = traceback.format_exc()
        save()
        raise
