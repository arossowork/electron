/* Private contexts, one per task; app.c owns the instances and hands each to its task. */
#ifndef CTX_H
#define CTX_H
#include <stdint.h>
#include "tracker_lib.h"

typedef struct {
    int pending;          /* a gate check is waiting for the supercap to recharge */
    int dbm;              /* TX power currently requested */
    int idle_mv, loaded_mv;
    int brownout_seen, tries, force_low, pend, sags;
} power_ctx_t;

enum { CS_BOOT_GATE, CS_ATTACH, CS_IDLE, CS_WAIT_FIX, CS_UPLOAD_GATE, CS_UPLOAD, CS_ASSIST_GATE, CS_ASSIST,
       CS_POLL_GATE, CS_BACKOFF };

typedef struct {
    int state, mode, rat, tried_nb, req_queued, fix_pending, poll_due;
    int brownouts, low_batt, idle_mv, loaded_mv, assist_pending;
    int fix_valid, fix_age, req_active, req_seq;
    uint64_t t_req_us, t_upload_us, gate_ok_us;
} conn_ctx_t;

typedef struct { int seq; int have_assist; int assist_asked; int age_s; uint64_t t_last_fix_us, t_assist_ask_us; int has_fix; int phase; int hold, lost_asked; } gnss_ctx_t;

typedef struct {
    int active, seq, fixes, moved, motion_seen;
    uint64_t t_start_us, last_req_us;
    int32_t starts[QUOTA_MAX];
    int n_starts;
} lost_ctx_t;

typedef struct {
    int low_batt, lost_active, link_mode;
    int buzzing, btn_down, long_fired, pulses_left, pulse_on, on_ms;
} ui_ctx_t;

typedef struct { int dones; } wdt_ctx_t;
typedef struct { int armed; } motion_ctx_t;

enum { FS_IDLE, FS_WAIT_GATE, FS_DOWNLOAD, FS_YIELD, FS_PAUSED_LOW, FS_APPLIED };
typedef struct {
    int state, low_batt, link, busy, fails, pending_confirm;
    uint32_t chunk_id;
    uint32_t offset, total_kb;
} fota_ctx_t;
#endif
