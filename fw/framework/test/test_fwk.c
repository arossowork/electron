/* Framework self-tests: coroutines, timers as events, ISR posting, routing, idle mode, watchdog. */
#include <stdio.h>
#include "fwk.h"
#include "fwk_sim.h"

enum { EV_PING = FWK_EV_USER, EV_PONG, EV_IRQ };

typedef struct { int pings, steps; } blink_ctx_t;

/* waits for a timer twice, then emits PONG: the coroutine pattern every module uses */
static int blink(fwk_task_t *t, const fwk_event_t *e)
{
    blink_ctx_t *c = t->ctx;
    CO_BEGIN(t);
    for (;;) {
        CO_WAIT_EVENT(t, e, EV_PING);
        c->pings++;
        fwk_timer_start(t, 1, 10000, 0);
        CO_WAIT_EVENT(t, e, FWK_EV_TIMER);
        c->steps++;
        fwk_stay_awake(t, 1);
        fwk_timer_start(t, 2, 500, 0);
        CO_AWAIT(t, e->type == FWK_EV_TIMER && e->a == 2);
        fwk_stay_awake(t, 0);
        c->steps++;
        fwk_post(fwk_ev(EV_PONG, FWK_BROADCAST, c->pings, 0));
    }
    CO_END(t);
}

typedef struct { int irqs, pongs, ticks; } obs_ctx_t;

static int observer(fwk_task_t *t, const fwk_event_t *e)
{
    obs_ctx_t *c = t->ctx;
    if (e->type == FWK_EV_INIT)
        fwk_timer_start(t, 7, 1000000, 1000000);   /* periodic 1 s tick */
    else if (e->type == EV_IRQ)
        c->irqs++;
    else if (e->type == EV_PONG)
        c->pongs++;
    else if (e->type == FWK_EV_TIMER && e->a == 7)
        c->ticks++;
    return FWK_WAIT;
}

static int hog(fwk_task_t *t, const fwk_event_t *e)
{
    (void)t;
    if (e->type == EV_PING)
        fwk_sim_advance(3000000);                   /* blocks 3 s instead of yielding */
    return FWK_WAIT;
}

int main(void)
{
    blink_ctx_t bc = {0};
    obs_ctx_t oc = {0};
    fwk_task_t tb = {"blink", blink, &bc, 1ull << EV_PING, 0, 0, 0};
    fwk_task_t to = {"obs", observer, &oc, (1ull << EV_IRQ) | (1ull << EV_PONG), 0, 0, 0};

    fwk_sim_reset(2000000);
    fwk_add_task(&tb);
    fwk_add_task(&to);
    fwk_sim_inject_at(100000, fwk_ev(EV_PING, FWK_BROADCAST, 0, 0));
    fwk_sim_inject_at(200000, fwk_ev(EV_IRQ, FWK_BROADCAST, 0, 0));
    fwk_sim_inject_at(5000000, fwk_ev(EV_PING, FWK_BROADCAST, 0, 0));
    fwk_sim_run_until(10000000);

    CHECK(bc.pings == 2 && bc.steps == 4, "coroutine ran both waits twice (%d, %d)", bc.pings, bc.steps);
    const fwk_sim_rec_t *p = fwk_sim_find(EV_PONG, 0);
    CHECK(p && p->t == 100000 + 10000 + 500, "PONG after 10 ms + 0.5 ms timers: %llu", p ? (unsigned long long)p->t : 0);
    CHECK(oc.pongs == 2 && oc.irqs == 1, "broadcast by subscription");
    CHECK(oc.ticks == 10, "periodic timer: 10 ticks in 10 s (%d)", oc.ticks);
    CHECK(fsim.idle_deep_calls > 0 && fsim.idle_light_calls > 0, "deep idle when free, light while a task is awake");
    CHECK(fsim.idle_light_us == 2 * (10000 + 500), "light for waits under FWK_DEEP_MIN_US or while awake: %llu us",
          (unsigned long long)fsim.idle_light_us);
    CHECK(!fsim.wdt_reset && fsim.max_feed_gap < 1000, "scheduler feeds the watchdog on every pass");

    /* queue overflow is counted, not silent */
    fwk_sim_reset(0);
    for (int i = 0; i < FWK_QUEUE_LEN + 3; i++)
        fwk_post(fwk_ev(EV_IRQ, FWK_BROADCAST, i, 0));
    CHECK(fwk_dropped() == 3, "3 events dropped");

    /* a module that blocks starves the watchdog */
    fwk_task_t th = {"hog", hog, 0, 1ull << EV_PING, 0, 0, 0};
    fwk_sim_reset(2000000);
    fwk_add_task(&th);
    fwk_sim_inject_at(1000, fwk_ev(EV_PING, FWK_BROADCAST, 0, 0));
    fwk_sim_run_until(5000000);
    CHECK(fsim.wdt_reset, "blocking 3 s against a 2 s watchdog resets");

    printf("fwk self-tests: %d checks, %d failed\n", fwk_checks, fwk_fails);
    return fwk_fails ? 1 : 0;
}
