/* fwk: a modular-monolith firmware framework for small low-power MCUs (docs/hwsw-codesign.md, "Firmware framework").
 *
 * One binary. Modules are stackless coroutines (protothread style) that talk only through typed events on one queue.
 * Interrupt handlers only post an event (fwk_post_isr). Timers are events. When the queue is empty the scheduler
 * calls the idle hook with the next timer deadline, and the port sleeps (light or deep). The scheduler feeds the
 * watchdog on every pass: a module that blocks instead of yielding starves it and the chip resets.
 *
 * Portable C11; the only platform calls are in fwk_hal.h. No global state outside this file's scheduler instance,
 * and modules keep their state in the context they are given (no shared globals). */
#ifndef FWK_H
#define FWK_H
#include <stdint.h>

#ifndef FWK_QUEUE_LEN
#define FWK_QUEUE_LEN 32
#endif
#ifndef FWK_MAX_TASKS
#define FWK_MAX_TASKS 12
#endif
#ifndef FWK_MAX_TIMERS
#define FWK_MAX_TIMERS 16
#endif
#ifndef FWK_DEEP_MIN_US
#define FWK_DEEP_MIN_US 100000u     /* idle shorter than this stays in light sleep */
#endif

#define FWK_BROADCAST 0xFF

/* Event types below FWK_EV_USER are the framework's; generated events.h numbers the project's from FWK_EV_USER. */
enum { FWK_EV_NONE = 0, FWK_EV_INIT = 1, FWK_EV_TIMER = 2, FWK_EV_USER = 3 };

typedef struct {
    uint16_t type;
    uint8_t  src;    /* task id of the sender, FWK_BROADCAST for an ISR or the system */
    uint8_t  dst;    /* task id, or FWK_BROADCAST: every task subscribed to the type */
    int32_t  a, b;   /* payload; the meaning of a and b per type is in the generated events.h */
} fwk_event_t;

typedef struct fwk_task fwk_task_t;
typedef int (*fwk_fn)(fwk_task_t *t, const fwk_event_t *e);   /* returns FWK_WAIT or FWK_DONE */
enum { FWK_WAIT = 0, FWK_DONE = 1 };

struct fwk_task {
    const char *name;
    fwk_fn      fn;
    void       *ctx;     /* the module's own state */
    uint64_t    subs;    /* bit n set: deliver broadcast events of type n (n < 64) */
    uint16_t    lc;      /* coroutine resume point */
    uint8_t     id;
    uint8_t     awake;   /* 1: this task needs the CPU or its peripherals between events (no deep sleep) */
};

/* ---- stackless coroutines: locals do not survive a wait; keep state in t->ctx */
#define CO_BEGIN(t)         switch ((t)->lc) { case 0:
/* CO_WAIT_UNTIL tests a state condition now and after each later event. CO_AWAIT gives up the current event first
 * and tests c on later events only: use it for conditions on the event itself, or the event that woke the task
 * would satisfy the next wait too. */
#define CO_WAIT_UNTIL(t, c) do { (t)->lc = __LINE__; /* fallthrough */ case __LINE__: if (!(c)) return FWK_WAIT; } while (0)
#define CO_AWAIT(t, c)      do { (t)->lc = __LINE__; return FWK_WAIT; case __LINE__: if (!(c)) return FWK_WAIT; } while (0)
#define CO_WAIT_EVENT(t, e, ty) CO_AWAIT(t, (e)->type == (ty))
#define CO_YIELD(t)         do { (t)->lc = __LINE__; return FWK_WAIT; case __LINE__:; } while (0)
#define CO_RESTART(t)       do { (t)->lc = 0; return FWK_WAIT; } while (0)
#define CO_END(t)           } (t)->lc = 0; return FWK_DONE

/* ---- scheduler */
void     fwk_init(void);
int      fwk_add_task(fwk_task_t *t);          /* returns the task id; delivers FWK_EV_INIT on the first pass */
int      fwk_post(fwk_event_t e);              /* 0 = queued, -1 = full (counted) */
int      fwk_post_isr(fwk_event_t e);          /* the only call an interrupt handler makes */
int      fwk_run_once(void);                   /* fire due timers, deliver one event; 1 if it did work */
void     fwk_run(void);                        /* forever: run_once, else idle until the next deadline */
void     fwk_idle(void);                       /* one idle step (the host harness calls it) */
void     fwk_stay_awake(fwk_task_t *t, int on);

/* ---- timers: on expiry the owner gets FWK_EV_TIMER with a = timer id */
int      fwk_timer_start(fwk_task_t *owner, int32_t id, uint64_t delay_us, uint64_t period_us);
void     fwk_timer_stop(fwk_task_t *owner, int32_t id);
uint64_t fwk_next_deadline(void);              /* UINT64_MAX when no timer runs */

/* ---- diagnostics */
typedef void (*fwk_trace_fn)(const fwk_event_t *e, uint64_t now_us);
void     fwk_set_trace(fwk_trace_fn fn);       /* called for every posted event (host tests, logging) */
uint32_t fwk_dropped(void);                    /* events lost to a full queue */

static inline fwk_event_t fwk_ev(uint16_t type, uint8_t dst, int32_t a, int32_t b)
{
    fwk_event_t e = {type, FWK_BROADCAST, dst, a, b};
    return e;
}

#endif
