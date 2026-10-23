/* Host harness for fwk: simulated time, event injection (as interrupts), a trace of every posted event, idle and
 * watchdog observations, and small assertion helpers. Projects link it with their own simulated board HAL. */
#ifndef FWK_SIM_H
#define FWK_SIM_H
#include <stdint.h>
#include <stdio.h>
#include "fwk.h"

#ifndef FWK_SIM_TRACE
#define FWK_SIM_TRACE 4096   /* records kept; fwk_sim_count counts every event regardless */
#endif

typedef struct { uint64_t t; fwk_event_t e; } fwk_sim_rec_t;

typedef struct {
    uint64_t now;
    fwk_sim_rec_t trace[FWK_SIM_TRACE];
    int ntrace;
    uint32_t count[64];           /* posted events per type, never capped */
    uint64_t idle_light_us, idle_deep_us;
    int idle_deep_calls, idle_light_calls;
    uint64_t last_feed, max_feed_gap;
    uint64_t wdt_timeout_us;      /* 0 = no watchdog model */
    int wdt_reset;                /* set when a feed gap exceeds the timeout */
} fwk_sim_t;

extern fwk_sim_t fsim;

void fwk_sim_reset(uint64_t wdt_timeout_us);
void fwk_sim_inject_at(uint64_t t_us, fwk_event_t e);   /* delivered with fwk_post_isr at t_us */
void fwk_sim_run_until(uint64_t t_us);                   /* scheduler + idle with simulated time */
void fwk_sim_advance(uint64_t us);                       /* busy time inside a module, or a hang */
int  fwk_sim_count(uint16_t type);
const fwk_sim_rec_t *fwk_sim_find(uint16_t type, int nth);   /* nth posted event of a type, 0-based */

extern int fwk_checks, fwk_fails;
#define CHECK(c, ...) do { fwk_checks++; if (!(c)) { fwk_fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); \
                           printf(__VA_ARGS__); printf("\n"); } } while (0)

#endif
