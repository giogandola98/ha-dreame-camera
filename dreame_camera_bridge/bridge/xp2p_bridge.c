/*
 * xp2p_bridge - minimal bridge between a Dreame vacuum camera (Tencent XP2P)
 * and a local HTTP-FLV URL that ffmpeg / go2rtc / Home Assistant can read.
 *
 * Written from scratch against Tencent's public appWrapper.h (iot-p2p-build v2.4.50).
 * License: MIT (this file only; the Tencent SDK is not included and is downloaded at build time).
 *
 * Input (environment, never argv so secrets don't show up in `ps`):
 *   XP2P_PRODUCT_ID, XP2P_DEVICE_NAME, XP2P_INFO
 *   XP2P_QUALITY (optional: high|standard|super, default high)
 *
 * Output on stdout, one event per line (machine-readable):
 *   READY <live_flv_url>
 *   STATUS ok
 *   ERROR <code> <message>
 *   CLOSED <reason>
 *
 * Commands read from stdin, one per line:
 *   status   -> asks the device if the live stream is available
 *   link     -> prints LINK <mode>: 62 direct P2P (LAN), 63 Tencent relay, 0 not yet known
 *   quit     -> shut down cleanly (also happens on EOF / SIGTERM)
 */
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "appWrapper.h"

#define DEVICE_ID "dreame"

static volatile sig_atomic_t g_stop = 0;
static volatile sig_atomic_t g_ready = 0;
static volatile sig_atomic_t g_failed = 0;

static void on_signal(int sig) { (void)sig; g_stop = 1; }

static void emit(const char *fmt, const char *a, const char *b) {
    /* stdout is the protocol channel: always flush. */
    printf(fmt, a ? a : "", b ? b : "");
    putchar('\n');
    fflush(stdout);
}

static const char *on_msg(const char *id, XP2PType type, const char *msg) {
    (void)id;
    switch (type) {
    case XP2PTypeDetectReady:
        g_ready = 1;
        break;
    case XP2PTypeDetectError:
        g_failed = 1;
        emit("ERROR detect %s%s", msg, NULL);
        break;
    case XP2PTypeDisconnect:
        emit("CLOSED disconnect %s%s", msg, NULL);
        g_stop = 1;
        break;
    case XP2PTypeStreamEnd:
    case XP2PTypeStreamRefush:
        emit("CLOSED stream_end %s%s", msg, NULL);
        g_stop = 1;
        break;
    default:
        break;
    }
    return NULL;
}

static void on_av(const char *id, uint8_t *buf, size_t len) { (void)id; (void)buf; (void)len; }

static char *on_device_data(const char *id, uint8_t *buf, size_t len) {
    (void)id; (void)buf; (void)len;
    return NULL;
}

static const char *need_env(const char *name) {
    const char *v = getenv(name);
    if (!v || !*v) {
        emit("ERROR config missing %s%s", name, NULL);
        exit(2);
    }
    return v;
}

int main(void) {
    const char *product_id = need_env("XP2P_PRODUCT_ID");
    const char *device_name = need_env("XP2P_DEVICE_NAME");
    const char *xp2p_info = need_env("XP2P_INFO");
    const char *quality = getenv("XP2P_QUALITY");
    if (!quality || !*quality) quality = "high";

    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);
    signal(SIGPIPE, SIG_IGN);

    setLogEnable(getenv("XP2P_DEBUG") != NULL, false);
    setUserCallbackToXp2p(on_av, on_msg, on_device_data);

    int rc = startService(DEVICE_ID, product_id, device_name, XP2P_PROTOCOL_AUTO);
    if (rc != 0) {
        char code[16];
        snprintf(code, sizeof code, "%d", rc);
        emit("ERROR start %s%s", code, NULL);
        return 1;
    }
    setDeviceXp2pInfo(DEVICE_ID, xp2p_info);

    /* Wait up to 20 s for the P2P link (direct LAN/UDP or Tencent relay). */
    for (int i = 0; i < 200 && !g_ready && !g_failed && !g_stop; i++) usleep(100 * 1000);
    if (!g_ready) {
        if (!g_failed) emit("ERROR detect timeout%s%s", NULL, NULL);
        stopService(DEVICE_ID);
        return 1;
    }

    const char *base = delegateHttpFlv(DEVICE_ID);
    if (!base || !*base) {
        emit("ERROR proxy no_local_url%s%s", NULL, NULL);
        stopService(DEVICE_ID);
        return 1;
    }
    char url[512];
    snprintf(url, sizeof url, "%sipc.flv?action=live&channel=0&quality=%s", base, quality);
    char mode[16];
    snprintf(mode, sizeof mode, "%d", getStreamLinkMode(DEVICE_ID)); /* 62 direct, 63 relay */
    emit("READY %s", url, NULL);
    emit("LINK %s%s", mode, NULL);

    char line[128];
    while (!g_stop && fgets(line, sizeof line, stdin)) {
        if (strncmp(line, "quit", 4) == 0) break;
        if (strncmp(line, "link", 4) == 0) {
            snprintf(mode, sizeof mode, "%d", getStreamLinkMode(DEVICE_ID));
            emit("LINK %s%s", mode, NULL);
            continue;
        }
        if (strncmp(line, "status", 6) == 0) {
            const char *cmd = "action=inner_define&channel=0&cmd=get_device_st&type=live&quality=high";
            unsigned char *reply = NULL;
            size_t reply_len = 0;
            int r = postCommandRequestSync(DEVICE_ID, (const unsigned char *)cmd, strlen(cmd),
                                           &reply, &reply_len, 3000000);
            if (r == 0 && reply) {
                char buf[256];
                size_t n = reply_len < sizeof buf - 1 ? reply_len : sizeof buf - 1;
                memcpy(buf, reply, n);
                buf[n] = 0;
                for (size_t i = 0; i < n; i++) if (buf[i] == '\n') buf[i] = ' ';
                emit("STATUS %s%s", buf, NULL);
                free(reply);
            } else {
                emit("STATUS error%s%s", NULL, NULL);
            }
        }
    }
    stopService(DEVICE_ID);
    emit("CLOSED quit%s%s", NULL, NULL);
    return 0;
}
