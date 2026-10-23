/* app wiring: one task per module, contexts owned here and handed to each task (no shared globals). */
#include "ctx.h"
#include "modules.h"

static power_ctx_t     power_c;
static sensors_ctx_t   sensors_c;
static control_ctx_t   control_c;
static actuators_ctx_t actuators_c;
static comms_ctx_t     comms_c;

static fwk_task_t t_power     = {"power", mod_power_task, &power_c, SUBS_POWER, 0, 0, 0};
static fwk_task_t t_sensors   = {"sensors", mod_sensors_task, &sensors_c, SUBS_SENSORS, 0, 0, 0};
static fwk_task_t t_control   = {"control", mod_control_task, &control_c, SUBS_CONTROL, 0, 0, 0};
static fwk_task_t t_actuators = {"actuators", mod_actuators_task, &actuators_c, SUBS_ACTUATORS, 0, 0, 0};
static fwk_task_t t_comms     = {"comms", mod_comms_task, &comms_c, SUBS_COMMS, 0, 0, 0};

void app_init(void)
{
    power_c = (power_ctx_t){0};
    sensors_c = (sensors_ctx_t){0};
    control_c = (control_ctx_t){0};
    actuators_c = (actuators_ctx_t){0};
    comms_c = (comms_ctx_t){0};
    fwk_add_task(&t_power);
    fwk_add_task(&t_sensors);
    fwk_add_task(&t_control);
    fwk_add_task(&t_actuators);
    fwk_add_task(&t_comms);
}

/* read-only views for the host tests and the port's diagnostics */
const comms_ctx_t *app_comms(void) { return &comms_c; }
const actuators_ctx_t *app_actuators(void) { return &actuators_c; }
