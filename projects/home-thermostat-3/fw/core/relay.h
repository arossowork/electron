/* Latching relay pulse state machine (R9, rules 3 and 4): drive BOARD_RELAY_PULSE_US, brake BOARD_RELAY_BRAKE_US,
 * then coast. One pulse at a time, none while Wi-Fi may transmit. Non-blocking: relays_poll() returns the next
 * deadline, the caller waits on a us timer (the window is 1.2 ms wide: an OS tick is not good enough). */
#ifndef RELAY_H
#define RELAY_H
#include <stdint.h>

#define RELAY_COUNT 2

typedef enum { RLY_IDLE = 0, RLY_DRIVE, RLY_BRAKE } rly_phase_t;

typedef struct {
    uint8_t state[RELAY_COUNT];   /* believed contact state, 1 = ON (kept in RTC memory) */
    int8_t  want[RELAY_COUNT];    /* -1 nothing pending, 0 reset, 1 set */
    rly_phase_t phase;
    int8_t  active;               /* relay being pulsed, -1 none */
    uint64_t t_end;               /* end of the current phase, us */
    uint16_t pulses_today;
} relays_t;

void     relays_init(relays_t *r);
void     relays_request(relays_t *r, int idx, int on);
/* advance the machine at time now; returns the next deadline in us, 0 when idle with nothing pending */
uint64_t relays_poll(relays_t *r, uint64_t now);
int      relays_busy(const relays_t *r);
/* run every pending pulse to completion (control wake); feeds the watchdog */
void     relays_run(relays_t *r);
/* IN1/IN2 low, the state before SENS_EN (nSLEEP) goes high (rule 1) */
void     relays_pins_low(void);
void     relays_pins_hiz(void);

#endif
