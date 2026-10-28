"""The build planner (hwflow.buildplan, ADR 0001 4.1): stage order, `until`, the hard-failure stop and the
`layout --auto` attempt search, as pure functions of the results so far."""

from __future__ import annotations

import pytest

from hwflow import buildplan as bp

LAY = {"budget_s": 100.0, "auto_max_errors": 40}
OK = {"verdict": "pass", "summary": {}}


def keys(p):
    assert not isinstance(p, bp.Done), p
    return [a.key for a in p]


def drive(options, attempt_summaries=None, now=0.0):
    """Run the plan to the end with every stage passing; attempts get the given summaries (k -> summary)."""
    results, order = {}, []
    for _ in range(50):
        p = bp.plan(options, results, LAY, now, 0.0)
        if isinstance(p, bp.Done):
            return order, p
        for a in p:
            order.append(a.key)
            k = bp.attempt_index(a.key)
            if k is not None:
                s = (attempt_summaries or {}).get(k, {"ok": True})
                results[a.key] = {"verdict": "pass" if s.get("ok") else "fail", "summary": s}
            else:
                results[a.key] = OK
    raise AssertionError("the plan did not end")


def test_stage_order_and_until():
    order, done = drive({"until": "fab"})
    assert order == ["check", "sch", "sim", "layout_attempt.1", "layout_select", "post", "fab"]
    assert done == bp.Done("complete")
    assert drive({"until": "sim"})[0] == ["check", "sch", "sim"]
    assert drive({"stages": ["check", "layout"]})[0] == ["check", "layout_attempt.1", "layout_select"]


def test_upstream_and_params():
    p = bp.plan({"until": "sim", "mc": 50}, {"check": OK, "sch": OK}, LAY, 0, 0)
    assert p == [bp.RunStage("sim", "sim", {"mc": 50}, ("sch",))]
    res = {k: OK for k in ("check", "sch", "sim")}
    (a,) = bp.plan({"until": "fab"}, res, LAY, 0, 0)
    assert a.upstream == bp.ATTEMPT_UPSTREAM and a.params["k"] == 1 and a.params["auto"] is False
    res["layout_attempt.1"] = {"verdict": "pass", "summary": {"ok": True}}
    (s,) = bp.plan({"until": "fab"}, res, LAY, 0, 0)
    assert s.key == "layout_select" and s.upstream == ("sch", "sim", "layout_attempt.1")
    assert set(bp.keyed(s.params, s.stage)) == set(bp.SELECT_PARAMS)


@pytest.mark.parametrize("stage", ["check", "sch"])
def test_hard_failure_stops_unless_force(stage):
    res = {"check": OK, "sch": OK}
    res[stage] = {"verdict": "fail", "summary": {}}
    assert bp.plan({"until": "fab"}, res, LAY, 0, 0) == bp.Done(f"hard_failure:{stage}")
    assert not isinstance(bp.plan({"until": "fab", "force": True}, res, LAY, 0, 0), bp.Done)
    assert bp.remaining({"until": "fab"}, res) == ["sim", "layout_select", "post", "fab"]


def test_a_failed_sim_does_not_stop_but_an_error_does():
    res = {"check": OK, "sch": OK, "sim": {"verdict": "fail", "summary": {}}}
    assert keys(bp.plan({"until": "fab"}, res, LAY, 0, 0)) == ["layout_attempt.1"]
    res["sim"] = {"verdict": "error", "summary": {}}
    assert bp.plan({"until": "fab"}, res, LAY, 0, 0) == bp.Done("hard_failure:sim")


def test_blocked_layout_stands_in_for_the_layout():
    res = {"check": OK, "sch": OK, "sim": OK}
    (a,) = bp.plan({"until": "fab", "layout_blocked": True}, res, LAY, 0, 0)
    assert a.key == "layout_select" and a.params["blocked"] is True and a.upstream == ()
    res["layout_select"] = {"verdict": "blocked", "summary": {}}
    assert bp.plan({"until": "fab"}, res, LAY, 0, 0) == bp.Done("hard_failure:layout")


AUTO = {"until": "layout", "auto": True, "attempts": 4}


def test_auto_attempt_one_alone_then_the_rest_in_parallel():
    res = {k: OK for k in ("check", "sch", "sim")}
    assert keys(bp.plan(AUTO, res, LAY, 0, 0)) == ["layout_attempt.1"]
    res["layout_attempt.1"] = {"verdict": "fail", "summary": {"ok": False, "drc_errors": 3, "unrouted": 0}}
    assert keys(bp.plan(AUTO, res, LAY, 1, 0)) == ["layout_attempt.2", "layout_attempt.3", "layout_attempt.4"]
    a3 = bp.plan(AUTO, res, LAY, 1, 0)[1]
    assert a3.params["deadline"] == 100.0 and "deadline" not in bp.keyed(a3.params, a3.stage)


def test_auto_cutoff_at_the_first_final_attempt():
    s = {1: {"ok": False, "drc_errors": 2}, 2: {"ok": False, "drc_errors": 1}, 3: {"ok": True}}
    order, _ = drive(AUTO, s)
    assert order[-1] == "layout_select"
    res = {k: OK for k in ("check", "sch", "sim")}
    res.update({bp.attempt_key(k): {"verdict": "pass", "summary": v} for k, v in s.items()})
    # attempt 4 still runs: it is past the cutoff, so it is no longer wanted and the select ranks 1..3
    search = bp.layout_search(AUTO, bp.attempts_of(res), LAY, 1, 0)
    assert search.cutoff == 3 and search.wanted == () and search.used == (1, 2, 3)
    assert bp.drop_reason(search, 4) == "cutoff"


def test_auto_hopeless_and_fixed_all_run_one_attempt():
    order, _ = drive(AUTO, {1: {"ok": False, "drc_errors": 41, "unrouted": 0}})
    assert [k for k in order if k.startswith("layout_attempt")] == ["layout_attempt.1"]
    order, _ = drive(AUTO, {1: {"ok": False, "drc_errors": 1, "fixed_all": True}})
    assert [k for k in order if k.startswith("layout_attempt")] == ["layout_attempt.1"]


def test_auto_counts_each_open_connection_once():
    # drc_errors already holds the unconnected items (`unrouted`); 41 + 41 must not read as 82 > 40
    s = bp.layout_search(AUTO, {1: {"ok": False, "drc_errors": 40, "unrouted": 40}}, LAY, 1, 0)
    assert not s.hopeless
    # `load`: each open net once (thermo-e2e-6: 41 unconnected items on 6 nets): the other attempts run
    s = bp.layout_search(AUTO, {1: {"ok": False, "drc_errors": 41, "unrouted": 41, "load": 6}}, LAY, 1, 0)
    assert not s.hopeless and s.n > 1
    s = bp.layout_search(AUTO, {1: {"ok": False, "drc_errors": 41, "unrouted": 0, "load": 41}}, LAY, 1, 0)
    assert s.hopeless


def test_auto_budget_ends_the_search():
    res = {k: OK for k in ("check", "sch", "sim")}
    res["layout_attempt.1"] = {"verdict": "fail", "summary": {"ok": False, "drc_errors": 3}}
    (s,) = bp.plan(AUTO, res, LAY, 101.0, 0.0)
    assert s.key == "layout_select" and s.params["over"] is True and s.params["used"] == [1]
    assert bp.drop_reason(bp.layout_search(AUTO, bp.attempts_of(res), LAY, 101.0, 0.0), 2) == "budget"


def test_a_crashed_attempt_counts_as_an_error_not_final():
    a = bp.attempts_of({"layout_attempt.1": {"verdict": "error", "summary": {"ok": True}}})
    assert a[1]["error"] and not bp._final(a[1])


def test_decisions_do_not_depend_on_result_order():
    base = {k: OK for k in ("check", "sch", "sim")}
    att = {bp.attempt_key(k): {"verdict": "fail", "summary": {"ok": False, "drc_errors": k}} for k in (1, 2, 3, 4)}
    one = bp.plan(AUTO, {**base, **att}, LAY, 1, 0)
    two = bp.plan(AUTO, {**base, **dict(reversed(list(att.items())))}, LAY, 1, 0)
    assert one == two


@pytest.mark.parametrize("attempts", [1, 4])
@pytest.mark.parametrize("fixed_only", [False, True])
def test_partial_first_attempt_is_kept_without_final_or_hopeless_verdict(attempts, fixed_only):
    partial = {"ok": False, "drc_errors": 100, "unrouted": 20,
               "fixed_parts_only": fixed_only, "budget_exhausted": True}
    options = {**AUTO, "attempts": attempts}
    search = bp.layout_search(options, {1: partial}, LAY, 1, 0)
    assert search.over and search.used == (1,) and search.wanted == ()
    assert search.cutoff is None and not search.hopeless
    assert search.n == attempts
    results = {k: OK for k in ("check", "sch", "sim")}
    results["layout_attempt.1"] = {"verdict": "fail", "summary": partial}
    (selection,) = bp.plan(options, results, LAY, 1, 0)
    assert selection.key == "layout_select" and selection.params["over"]
    assert selection.params["used"] == [1] and not selection.params["hopeless"]


def test_partial_attempt_cannot_be_final_even_if_other_flags_look_clean():
    assert not bp._final({"ok": True, "fixed_parts_only": True, "budget_exhausted": True})


def test_both_layout_paths_keep_partial_flag_in_planner_summary():
    from hwflow.pipeline import layout, stages
    result = {"ok": False, "drc_errors": 10, "budget_exhausted": True, "attempt": {"k": 1}}
    for summary in (stages.attempt_summary({"r": result}), layout.summarize_attempt(result)):
        assert summary["budget_exhausted"]
        search = bp.layout_search(AUTO, {1: summary}, LAY, 1, 0)
        assert search.over and search.wanted == () and not search.hopeless
