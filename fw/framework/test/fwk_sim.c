#include "fwk_sim.h"
#include <string.h>
#include "fwk_hal.h"

fwk_sim_t fsim;
int fwk_checks, fwk_fails;

#define MAX_INJ 64
static struct { uint64_t t; fwk_event_t e; int used; } inj[MAX_INJ];

static void rec(const fwk_event_t *e, uint64_t now)
{
    if (e->type < 64)
        fsim.count[e->type]++;
    if (fsim.ntrace < FWK_SIM_TRACE)
        fsim.trace[fsim.ntrace++] = (fwk_sim_rec_t){now, *e};
}

void fwk_sim_reset(uint64_t wdt_timeout_us)
{
    memset(&fsim, 0, sizeof fsim);
    memset(inj, 0, sizeof inj);
    fsim.wdt_timeout_us = wdt_timeout_us;
    fwk_init();
    fwk_set_trace(rec);
}

/* ---- fwk_hal on the host */
uint64_t fwk_hal_now_us(void) { return fsim.now; }
uint32_t fwk_hal_irq_save(void) { return 0; }
void fwk_hal_irq_restore(uint32_t s) { (void)s; }

static void check_wdt(void)
{
    uint64_t gap = fsim.now - fsim.last_feed;
    if (gap > fsim.max_feed_gap)
        fsim.max_feed_gap = gap;
    if (fsim.wdt_timeout_us && gap > fsim.wdt_timeout_us)
        fsim.wdt_reset = 1;
}

void fwk_hal_wdt_feed(void)
{
    check_wdt();
    fsim.last_feed = fsim.now;
}

static uint64_t next_inj(void)
{
    uint64_t t = UINT64_MAX;
    for (int i = 0; i < MAX_INJ; i++)
        if (inj[i].used && inj[i].t < t)
            t = inj[i].t;
    return t;
}

static uint64_t idle_limit = UINT64_MAX;

void fwk_hal_idle(uint64_t until, int mode)
{
    uint64_t t = until, ti = next_inj();
    if (ti < t)
        t = ti;                                   /* an interrupt wakes the CPU early */
    if (t > idle_limit)
        t = idle_limit;
    if (t <= fsim.now)
        return;
    uint64_t d = t - fsim.now;
    if (mode == FWK_IDLE_DEEP) {
        fsim.idle_deep_us += d;
        fsim.idle_deep_calls++;
    } else {
        fsim.idle_light_us += d;
        fsim.idle_light_calls++;
    }
    fsim.now = t;
    fsim.last_feed = fsim.now;                    /* the watchdog does not run in sleep (port: RTC WDT paused) */
}

void fwk_sim_inject_at(uint64_t t, fwk_event_t e)
{
    for (int i = 0; i < MAX_INJ; i++)
        if (!inj[i].used) {
            inj[i].t = t;
            inj[i].e = e;
            inj[i].used = 1;
            return;
        }
}

static void post_due(void)
{
    for (int i = 0; i < MAX_INJ; i++)
        if (inj[i].used && inj[i].t <= fsim.now) {
            inj[i].used = 0;
            fwk_post_isr(inj[i].e);
        }
}

void fwk_sim_run_until(uint64_t t_end)
{
    idle_limit = t_end;
    for (int guard = 0; guard < 10000000; guard++) {
        post_due();
        if (fwk_run_once())
            continue;
        if (fsim.now >= t_end)
            break;
        uint64_t before = fsim.now;
        fwk_idle();
        if (fsim.now == before) {                 /* nothing scheduled before t_end */
            uint64_t ti = next_inj();
            fsim.now = ti < t_end ? ti : t_end;
            fsim.last_feed = fsim.now;
        }
    }
    idle_limit = UINT64_MAX;
}

void fwk_sim_advance(uint64_t us)
{
    fsim.now += us;
    check_wdt();
}

int fwk_sim_count(uint16_t type)
{
    return type < 64 ? (int)fsim.count[type] : 0;
}

const fwk_sim_rec_t *fwk_sim_find(uint16_t type, int nth)
{
    for (int i = 0; i < fsim.ntrace; i++)
        if (fsim.trace[i].e.type == type && nth-- == 0)
            return &fsim.trace[i];
    return 0;
}
