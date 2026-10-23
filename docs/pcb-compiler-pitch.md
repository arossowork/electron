# A compiler for PCB design

Proposal / pitch — 23 October 2026

We want to go from code to a PCB quickly, with predictable results. The design should be easy for an AI agent to work on and clear enough for a human to read, review and change. Our focus is the pipeline from a design language to a placed, routed and checked board.

The input describes the circuit and its physical constraints: components, footprints, connections, board outline, electrical domains and placement rules. The output is an editable PCB, with a report showing what was checked, what passed and what still needs attention. A failed compilation should identify the obstruction or exhausted search budget and the choices available to resolve it.

The designer controls what matters. Fix a connector to an edge, reserve a region for the power supply, keep mains copper away from low-voltage circuitry, or prescribe a critical route. Leave the remaining choices to the compiler. Control can become more specific as needed, down to individual parts and tracks. Hard requirements and preferences must be distinct; the compiler must never relax a requirement just to finish.

A small, readable DSL would express this intent. A programming API could construct the same underlying design model. The important foundation is a consistent meaning for connections, geometry and constraints across placement, routing and verification. Stable identities and useful diagnostics would let both people and agents make small changes without rebuilding their understanding of the whole board.

OpenROAD offers useful ideas: hierarchical placement, congestion estimates that guide placement, coarse routing followed by detailed routing, and incremental repair. We should adapt those ideas to PCB geometry and electrical rules. We can use KiCad for libraries, editable output, inspection and design-rule checking, and start with an existing routing backend. We should own the design model and the compilation workflow, improving or replacing placement and routing algorithms where measured results justify it. Backend choice remains open.

The thermostat work gives us a concrete starting point. Development branches already contain isolation rules, floorplanning, feasibility checks and routing reuse. The opportunity is to turn those pieces into a dependable pipeline that avoids repeated blind attempts and preserves valid work after an edit.

The first milestone should cover selected small reference boards with supplied circuits and footprints, chosen to exercise constraints found in the thermostat. Some components are fixed by mechanical or electrical needs; others must be placed by the tool. It should support regions, isolation distances, track widths and permitted layers, then produce a fully connected layout that passes the encoded checks on those boards. Deliberately conflicting inputs should produce useful failures. A timeout must remain distinguishable from proven impossibility.

We would measure completion rate, runtime, manual interventions and how much valid work survives a small edit. The first scope is compilation of an explicit circuit. Automatic circuit invention, arbitrary board complexity and a complete CAD editor can come later. Passing the encoded checks is one milestone; simulation coverage, physical testing and product approval remain explicit parts of the wider process.

The goal is a complete pipeline for a manageable class of boards, with precise controls and fast feedback. That gives us something useful early and a foundation that can grow with harder designs.

References: [thermostat project](https://github.com/arossowork/electron/tree/3f42ce71d86bfbc391e3c93f64c43cbcc1b8df24/projects/home-thermostat-3), [existing layout diagnosis](https://github.com/arossowork/electron/blob/3f42ce71d86bfbc391e3c93f64c43cbcc1b8df24/docs/layout-diagnosis.md), [OpenROAD placement](https://openroad.readthedocs.io/en/latest/main/src/gpl/README.html), [OpenROAD global routing](https://openroad.readthedocs.io/en/latest/main/src/grt/README.html).
