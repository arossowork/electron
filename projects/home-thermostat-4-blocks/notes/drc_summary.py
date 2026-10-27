import json, collections, sys
d = json.load(open('projects/home-thermostat-3/build/home_thermostat_3.drc.json'))
v = [x for x in d['violations'] if x.get('severity') == 'error']
print(dict(collections.Counter(x['type'] for x in v)))
full = len(sys.argv) > 1
for x in v:
    if x['type'] in ('clearance', 'creepage', 'items_not_allowed', 'copper_edge_clearance', 'courtyards_overlap', 'hole_clearance'):
        if full:
            print(x['type'], x['description'][-45:], [(i['description'][:48], round(i['pos']['x'], 1), round(i['pos']['y'], 1)) for i in x['items']])
un = collections.Counter()
for u in d.get('unconnected_items', []):
    for i in u['items'][:1]:
        un[i['description'][:70]] += 1
print('unconnected', sum(un.values()))
for k, c in un.most_common(15):
    print(c, k)
