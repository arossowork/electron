import math, sys
from hwflow.libs import Libs
from hwflow.placer import rot
P = 'projects/home-thermostat-3'
L = Libs(extra_dirs=[P + '/lib/symbols', P + '/lib/footprints'])
FP = dict(BT1='BH-AAA-B5AA001', BT2='BH-AAA-B5AA001', K1='Relay_Panasonic_ADW1_1coil_back',
          K2='Relay_Panasonic_ADW1_1coil_back', J1='TerminalBlock_bornier-3_P5.08mm_back',
          F1='TerminalBlock_bornier-2_P5.08mm_back', C1='CP_Radial_D10.0mm_P5.00mm_back',
          C2='CP_Radial_D10.0mm_P5.00mm_back')
A = dict(BT1=(53.3, 6.4, 0), BT2=(53.3, 19.4, 180), C1=(36, 34, 0), C2=(48, 34, 0), K1=(12.5, 5.6, 180),
         K2=(12.5, 40.4, 180), J1=(18.4, 29.2, 270), F1=(11.4, 14.9, 0))
side = dict(BT1='F', BT2='F', C1='B', C2='B', K1='B', K2='B', J1='B', F1='B')
for a in sys.argv[1:]:
    k, v = a.split('=')
    A[k] = tuple(float(x) for x in v.split(','))


def box(r):
    x, y, a = A[r]
    b = L.footprint('thermo:' + FP[r])['bbox']
    pts = [rot(px, py, a) for px in (b[0], b[2]) for py in (b[1], b[3])]
    xs = [x + p[0] for p in pts]
    ys = [y + p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def pads(r):
    x, y, a = A[r]
    out = {}
    for p in L.footprint('thermo:' + FP[r])['pads']:
        dx, dy = rot(p['x'], p['y'], a)
        out[p['number']] = (x + dx, y + dy, p['w'], p['h'], p['shape'])
    return out


def gap(p, q):
    if p[4] == 'rect' and q[4] != 'rect':
        r, c = p, q
    elif q[4] == 'rect' and p[4] != 'rect':
        r, c = q, p
    else:
        return math.hypot(p[0] - q[0], p[1] - q[1]) - p[2] / 2 - q[2] / 2
    ex = max(abs(c[0] - r[0]) - r[2] / 2, 0)
    ey = max(abs(c[1] - r[1]) - r[3] / 2, 0)
    return math.hypot(ex, ey) - c[2] / 2


B = {r: box(r) for r in A}
print('courtyards')
for r in B:
    print(' ', r, side[r], [round(v, 2) for v in B[r]])
for s in 'FB':
    rs = [r for r in B if side[r] == s]
    for i, a in enumerate(rs):
        for b in rs[i + 1:]:
            ox = min(B[a][2], B[b][2]) - max(B[a][0], B[b][0])
            oy = min(B[a][3], B[b][3]) - max(B[a][1], B[b][1])
            if ox > 0 and oy > 0:
                print('OVERLAP', s, a, b, round(ox, 2), round(oy, 2))
            elif ox > -1 and oy > -1:
                print('  near', a, b, 'x-overlap', round(ox, 2), 'y-overlap', round(oy, 2))
for nm, (cx0, cy0) in dict(L=(2.25, 23), R=(85.75, 23)).items():
    for r in B:
        x0, y0, x1, y1 = B[r]
        d = math.hypot(min(max(cx0, x0), x1) - cx0, min(max(cy0, y0), y1) - cy0)
        if d < 8:
            print('notch', nm, r, 'courtyard-to-centre', round(d, 2), 'OK' if d >= 4.3 else 'IN KEEPOUT')
mains = [(r, n, p) for r in ('J1', 'F1') for n, p in pads(r).items()]
coil = [(r, n, p) for r in ('K1', 'K2') for n, p in pads(r).items() if n in ('A1', 'A2')]
m = 99
for r, n, p in mains:
    for r2, n2, q in coil:
        g = gap(p, q)
        m = min(m, g)
        if r == 'J1' and n == '1':
            print(f' J1.1 - {r2}.{n2} edge {g:.2f} centre {math.hypot(p[0] - q[0], p[1] - q[1]):.2f}')
print('J1/F1 pads to coil pads, min edge gap', round(m, 2), '(need 6.3)')
for r in B:
    for n, p in pads(r).items():
        h = max(p[2], p[3]) / 2
        e = min(p[0] - h, p[1] - h, 88 - p[0] - h, 46 - p[1] - h)
        if e < 1.0:
            print('pad edge', r, n, round(e, 2))
