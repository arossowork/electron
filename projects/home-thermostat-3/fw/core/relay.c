#include "relay.h"
#include "board.h"
#include "hal.h"

#if BOARD_RELAY_PULSE_US < BOARD_RELAY_PULSE_MIN_US || BOARD_RELAY_PULSE_US > BOARD_RELAY_PULSE_MAX_US
#error "relay pulse outside the R9 window derived by hwflow fw"
#endif

static const int IN1[RELAY_COUNT] = {BOARD_GPIO_K1_IN1, BOARD_GPIO_K2_IN1};
static const int IN2[RELAY_COUNT] = {BOARD_GPIO_K1_IN2, BOARD_GPIO_K2_IN2};
static const int SET_IN[RELAY_COUNT] = {BOARD_K1_SET_IN, BOARD_K2_SET_IN};

void relays_init(relays_t *r)
{
    for (int i = 0; i < RELAY_COUNT; i++) {
        r->state[i] = 0;
        r->want[i] = -1;
    }
    r->phase = RLY_IDLE;
    r->active = -1;
    r->t_end = 0;
    r->pulses_today = 0;
}

void relays_request(relays_t *r, int idx, int on)
{
    if (idx >= 0 && idx < RELAY_COUNT)
        r->want[idx] = (int8_t)(on ? 1 : 0);
}

int relays_busy(const relays_t *r)
{
    if (r->phase != RLY_IDLE)
        return 1;
    for (int i = 0; i < RELAY_COUNT; i++)
        if (r->want[i] >= 0)
            return 1;
    return 0;
}

static int drive_lo = -1, drive_hi = -1;          /* pins of the pulse in progress */

static void drive(int i, int dir_set)
{
    /* DRV8837: IN1 1 / IN2 0 = OUT1 high (forward), 0 / 1 = reverse, 1 / 1 = brake, 0 / 0 = coast */
    int set_on_in1 = (SET_IN[i] == 1);
    int in1_high = dir_set ? set_on_in1 : !set_on_in1;
    drive_hi = in1_high ? IN1[i] : IN2[i];
    drive_lo = in1_high ? IN2[i] : IN1[i];
    hal_gpio_out(drive_lo, 0);
    hal_gpio_out(drive_hi, 1);
}

uint64_t relays_poll(relays_t *r, uint64_t now)
{
    switch (r->phase) {
    case RLY_IDLE:
        if (hal_wifi_tx_busy())                  /* rule 4: no pulse during TX (R3) */
            return now + 1000;
        for (int i = 0; i < RELAY_COUNT; i++) {
            if (r->want[i] < 0)
                continue;
            r->active = (int8_t)i;
            drive(i, r->want[i]);
            r->phase = RLY_DRIVE;
            r->t_end = now + BOARD_RELAY_PULSE_US;
            return r->t_end;
        }
        return 0;
    case RLY_DRIVE:
        if (now < r->t_end)
            return r->t_end;
        hal_gpio_out(drive_lo, 1);               /* brake: both high (rule 3, back-EMF) */
        r->phase = RLY_BRAKE;
        r->t_end = now + BOARD_RELAY_BRAKE_US;
        return r->t_end;
    case RLY_BRAKE:
        if (now < r->t_end)
            return r->t_end;
        hal_gpio_out(drive_lo, 0);               /* coast; this order never passes through the reverse drive */
        hal_gpio_out(drive_hi, 0);
        r->state[r->active] = (uint8_t)r->want[r->active];
        r->want[r->active] = -1;
        r->active = -1;
        r->phase = RLY_IDLE;
        r->pulses_today++;
        return relays_poll(r, now);
    }
    return 0;
}

void relays_run(relays_t *r)
{
    uint64_t next;
    while ((next = relays_poll(r, hal_now_us())) != 0) {
        uint64_t now = hal_now_us();
        if (next > now)
            hal_wait_us((uint32_t)(next - now));  /* GPIOs keep driving: busy wait, not light sleep */
        hal_wdt_feed();
    }
}

void relays_pins_low(void)
{
    for (int i = 0; i < RELAY_COUNT; i++) {
        hal_gpio_out(IN1[i], 0);
        hal_gpio_out(IN2[i], 0);
    }
}

void relays_pins_hiz(void)
{
    for (int i = 0; i < RELAY_COUNT; i++) {
        hal_gpio_hiz(IN1[i]);
        hal_gpio_hiz(IN2[i]);
    }
}
