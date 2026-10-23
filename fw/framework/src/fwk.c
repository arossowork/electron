#include <string.h>
#include "fwk.h"
#include "fwk_hal.h"

typedef struct {
    uint64_t due, period;
    int32_t  id;
    uint8_t  owner, used;
} fwk_timer_t;

/* The one scheduler instance. Modules never see it: they hold their own context and talk through events. */
static struct {
    fwk_event_t   q[FWK_QUEUE_LEN];
    uint16_t      head, tail, count;
    fwk_task_t   *task[FWK_MAX_TASKS];
    uint8_t       ntask, inited;
    fwk_timer_t   tm[FWK_MAX_TIMERS];
    uint32_t      dropped;
    fwk_trace_fn  trace;
} S;

void fwk_init(void)
{
    memset(&S, 0, sizeof S);
}

int fwk_add_task(fwk_task_t *t)
{
    if (S.ntask >= FWK_MAX_TASKS)
        return -1;
    t->id = S.ntask;
    t->lc = 0;
    S.task[S.ntask++] = t;
    S.inited = 0;
    return t->id;
}

int fwk_post(fwk_event_t e)
{
    uint32_t s = fwk_hal_irq_save();
    int r = -1;
    if (S.count < FWK_QUEUE_LEN) {
        S.q[S.tail] = e;
        S.tail = (uint16_t)((S.tail + 1) % FWK_QUEUE_LEN);
        S.count++;
        r = 0;
    } else {
        S.dropped++;
    }
    fwk_hal_irq_restore(s);
    if (r == 0 && S.trace)
        S.trace(&e, fwk_hal_now_us());
    return r;
}

int fwk_post_isr(fwk_event_t e)
{
    e.src = FWK_BROADCAST;
    return fwk_post(e);
}

static int pop(fwk_event_t *e)
{
    uint32_t s = fwk_hal_irq_save();
    int ok = S.count > 0;
    if (ok) {
        *e = S.q[S.head];
        S.head = (uint16_t)((S.head + 1) % FWK_QUEUE_LEN);
        S.count--;
    }
    fwk_hal_irq_restore(s);
    return ok;
}

static void deliver(fwk_task_t *t, const fwk_event_t *e)
{
    t->fn(t, e);
}

static void fire_timers(uint64_t now)
{
    for (int i = 0; i < FWK_MAX_TIMERS; i++) {
        fwk_timer_t *m = &S.tm[i];
        if (!m->used || m->due > now)
            continue;
        fwk_event_t e = fwk_ev(FWK_EV_TIMER, m->owner, m->id, 0);
        if (m->period) {
            m->due += m->period;
            if (m->due <= now)
                m->due = now + m->period;   /* missed periods are not replayed */
        } else {
            m->used = 0;
        }
        fwk_post(e);
    }
}

int fwk_run_once(void)
{
    fwk_hal_wdt_feed();
    if (!S.inited) {                            /* FWK_EV_INIT once to every task added since the last pass */
        S.inited = 1;
        fwk_event_t e = fwk_ev(FWK_EV_INIT, FWK_BROADCAST, 0, 0);
        for (int i = 0; i < S.ntask; i++)
            if (S.task[i]->lc == 0)
                deliver(S.task[i], &e);
        return 1;
    }
    fire_timers(fwk_hal_now_us());
    fwk_event_t e;
    if (!pop(&e))
        return 0;
    if (e.dst != FWK_BROADCAST) {
        if (e.dst < S.ntask)
            deliver(S.task[e.dst], &e);
    } else {
        for (int i = 0; i < S.ntask; i++)
            if (e.type < 64 && (S.task[i]->subs >> e.type) & 1u)
                deliver(S.task[i], &e);
    }
    return 1;
}

uint64_t fwk_next_deadline(void)
{
    uint64_t d = UINT64_MAX;
    for (int i = 0; i < FWK_MAX_TIMERS; i++)
        if (S.tm[i].used && S.tm[i].due < d)
            d = S.tm[i].due;
    return d;
}

void fwk_idle(void)
{
    uint64_t now = fwk_hal_now_us(), until = fwk_next_deadline();
    if (S.count || until <= now)
        return;
    int awake = 0;
    for (int i = 0; i < S.ntask; i++)
        awake |= S.task[i]->awake;
    int mode = (!awake && (until == UINT64_MAX || until - now >= FWK_DEEP_MIN_US)) ? FWK_IDLE_DEEP : FWK_IDLE_LIGHT;
    fwk_hal_idle(until, mode);
}

void fwk_run(void)
{
    for (;;)
        if (!fwk_run_once())
            fwk_idle();
}

void fwk_stay_awake(fwk_task_t *t, int on)
{
    t->awake = (uint8_t)(on != 0);
}

int fwk_timer_start(fwk_task_t *owner, int32_t id, uint64_t delay_us, uint64_t period_us)
{
    fwk_timer_stop(owner, id);
    for (int i = 0; i < FWK_MAX_TIMERS; i++)
        if (!S.tm[i].used) {
            S.tm[i] = (fwk_timer_t){fwk_hal_now_us() + delay_us, period_us, id, owner->id, 1};
            return 0;
        }
    return -1;
}

void fwk_timer_stop(fwk_task_t *owner, int32_t id)
{
    for (int i = 0; i < FWK_MAX_TIMERS; i++)
        if (S.tm[i].used && S.tm[i].owner == owner->id && S.tm[i].id == id)
            S.tm[i].used = 0;
}

void fwk_set_trace(fwk_trace_fn fn) { S.trace = fn; }
uint32_t fwk_dropped(void) { return S.dropped; }
