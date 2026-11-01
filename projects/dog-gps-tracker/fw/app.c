/* app wiring: one task per module, contexts owned here and handed to each task (no shared globals).
 * wdt is added first: its reset cause event is queued before any module starts a TX sequence. */
#include "ctx.h"
#include "modules.h"

static wdt_ctx_t    wdt_c;
static power_ctx_t  power_c;
static conn_ctx_t   conn_c;
static gnss_ctx_t   gnss_c;
static lost_ctx_t   lost_c;
static ui_ctx_t     ui_c;
static motion_ctx_t motion_c;
static fota_ctx_t   fota_c;

static fwk_task_t t_wdt    = {"wdt", mod_wdt_task, &wdt_c, SUBS_WDT, 0, 0, 0};
static fwk_task_t t_power  = {"power", mod_power_task, &power_c, SUBS_POWER, 0, 0, 0};
static fwk_task_t t_conn   = {"conn", mod_conn_task, &conn_c, SUBS_CONN, 0, 0, 0};
static fwk_task_t t_gnss   = {"gnss", mod_gnss_task, &gnss_c, SUBS_GNSS, 0, 0, 0};
static fwk_task_t t_lost   = {"lost", mod_lost_task, &lost_c, SUBS_LOST, 0, 0, 0};
static fwk_task_t t_ui     = {"ui", mod_ui_task, &ui_c, SUBS_UI, 0, 0, 0};
static fwk_task_t t_fota   = {"fota", mod_fota_task, &fota_c, SUBS_FOTA, 0, 0, 0};
static fwk_task_t t_motion = {"motion", mod_motion_task, &motion_c, SUBS_MOTION, 0, 0, 0};

void app_init(void)
{
    wdt_c = (wdt_ctx_t){0};
    power_c = (power_ctx_t){0};
    conn_c = (conn_ctx_t){0};
    gnss_c = (gnss_ctx_t){0};
    lost_c = (lost_ctx_t){0};
    ui_c = (ui_ctx_t){0};
    motion_c = (motion_ctx_t){0};
    fota_c = (fota_ctx_t){0};
    fwk_add_task(&t_wdt);
    fwk_add_task(&t_power);
    fwk_add_task(&t_conn);
    fwk_add_task(&t_gnss);
    fwk_add_task(&t_lost);
    fwk_add_task(&t_ui);
    fwk_add_task(&t_fota);
    fwk_add_task(&t_motion);
}

const conn_ctx_t *app_conn(void) { return &conn_c; }
const power_ctx_t *app_power(void) { return &power_c; }
const fota_ctx_t *app_fota(void) { return &fota_c; }
const lost_ctx_t *app_lost(void) { return &lost_c; }
