/*
 * PTP fault injection (debug). Overrides the stack's ptp_fault_rx() hook
 * (patches/zephyr/0003) to drop or skew received PTP messages on this board.
 *
 *   ptp_fault                              show rules and counters
 *   ptp_fault drop <type> <percent>        drop that share of received <type>
 *   ptp_fault delay sync <ns> [count]      Sync RX timestamp t2 += ns
 *                                          (a Sync queued in the network:
 *                                          offset reads +ns)
 *   ptp_fault delay dresp <ns> [count]     Delay_Resp receiveTimestamp t4 += ns
 *                                          (a queued Delay_Req: delay +ns/2,
 *                                          offset -ns/2)
 *   ptp_fault off                          clear all rules and counters
 *
 * <type>: sync fup dreq dresp announce. count: messages to skew, default 1
 * (a single bad sample); 0 means every message until cleared.
 *
 * Rules apply to messages this board receives. Delay_Resp rules only touch
 * responses to this board's own Delay_Req (every board receives all of them
 * in multicast mode), so "drop dresp" on one slave hides its responses only;
 * "drop dreq" on the GM affects every slave. Counters for dreq on a slave
 * include other slaves' requests.
 */

#include <stdlib.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/random/random.h>
#include <zephyr/shell/shell.h>

#include "ptp/msg.h"
#include "ptp/port.h"

enum fault_type { F_SYNC, F_FUP, F_DREQ, F_DRESP, F_ANNOUNCE, F_COUNT };

static const struct {
	const char *name;
	enum ptp_msg_type type;
} types[F_COUNT] = {
	[F_SYNC] = {"sync", PTP_MSG_SYNC},
	[F_FUP] = {"fup", PTP_MSG_FOLLOW_UP},
	[F_DREQ] = {"dreq", PTP_MSG_DELAY_REQ},
	[F_DRESP] = {"dresp", PTP_MSG_DELAY_RESP},
	[F_ANNOUNCE] = {"announce", PTP_MSG_ANNOUNCE},
};

static struct {
	uint8_t drop_pct;
	int32_t delay_ns;
	int32_t delay_left;	/* <0: unlimited */
	uint32_t seen, dropped, delayed;
} rule[F_COUNT];

static void ts_add(struct net_ptp_time *ts, int64_t ns)
{
	int64_t total = (int64_t)ts->nanosecond + ns;
	int64_t sec = (int64_t)ts->second + total / NSEC_PER_SEC;

	total %= NSEC_PER_SEC;
	if (total < 0) {
		total += NSEC_PER_SEC;
		sec--;
	}
	ts->second = (uint64_t)sec;
	ts->nanosecond = (uint32_t)total;
}

bool ptp_fault_rx(struct ptp_port *port, struct ptp_msg *msg)
{
	enum ptp_msg_type t = ptp_msg_type(msg);
	int f;

	for (f = 0; f < F_COUNT && types[f].type != t; f++) {
	}
	if (f == F_COUNT) {
		return false;
	}

	/* In multicast mode every board receives every slave's Delay_Resp; only
	 * the ones answering this port's Delay_Req matter (the stack ignores the
	 * rest), so leave the others alone and uncounted.
	 */
	if (f == F_DRESP && !ptp_port_id_eq(&msg->delay_resp.req_port_id, &port->port_ds.id)) {
		return false;
	}

	rule[f].seen++;

	if (rule[f].drop_pct && (sys_rand32_get() % 100) < rule[f].drop_pct) {
		rule[f].dropped++;
		return true;
	}

	if (rule[f].delay_ns && rule[f].delay_left != 0) {
		if (f == F_SYNC) {
			ts_add(&msg->timestamp.host, rule[f].delay_ns);
		} else if (f == F_DRESP) {
			ts_add(&msg->timestamp.protocol, rule[f].delay_ns);
		}
		rule[f].delayed++;
		if (rule[f].delay_left > 0) {
			rule[f].delay_left--;
		}
	}

	return false;
}

static int find_type(const struct shell *sh, const char *name)
{
	for (int f = 0; f < F_COUNT; f++) {
		if (strcmp(name, types[f].name) == 0) {
			return f;
		}
	}
	shell_error(sh, "unknown type '%s' (sync fup dreq dresp announce)", name);
	return -EINVAL;
}

static int cmd_show(const struct shell *sh)
{
	for (int f = 0; f < F_COUNT; f++) {
		shell_print(sh, "%-8s drop %3u%%  delay %+d ns (left %s%d)  seen %u dropped %u "
			    "delayed %u",
			    types[f].name, rule[f].drop_pct, rule[f].delay_ns,
			    rule[f].delay_left < 0 ? "inf " : "", rule[f].delay_left < 0 ? 0
			    : rule[f].delay_left, rule[f].seen, rule[f].dropped, rule[f].delayed);
	}
	return 0;
}

static int cmd_ptp_fault(const struct shell *sh, size_t argc, char **argv)
{
	int f;

	if (argc == 1) {
		return cmd_show(sh);
	}

	if (strcmp(argv[1], "off") == 0) {
		memset(rule, 0, sizeof(rule));
		shell_print(sh, "all faults cleared");
		return 0;
	}

	if (strcmp(argv[1], "drop") == 0 && argc == 4) {
		long pct = strtol(argv[3], NULL, 0);

		f = find_type(sh, argv[2]);
		if (f < 0) {
			return f;
		}
		if (pct < 0 || pct > 100) {
			shell_error(sh, "percent must be 0-100");
			return -EINVAL;
		}
		rule[f].drop_pct = (uint8_t)pct;
		shell_print(sh, "dropping %ld%% of received %s", pct, types[f].name);
		return 0;
	}

	if (strcmp(argv[1], "delay") == 0 && (argc == 4 || argc == 5)) {
		long ns = strtol(argv[3], NULL, 0);
		long count = argc == 5 ? strtol(argv[4], NULL, 0) : 1;

		f = find_type(sh, argv[2]);
		if (f < 0) {
			return f;
		}
		if (f != F_SYNC && f != F_DRESP) {
			shell_error(sh, "delay supports sync and dresp only");
			return -EINVAL;
		}
		rule[f].delay_ns = (int32_t)ns;
		rule[f].delay_left = count == 0 ? -1 : (int32_t)count;
		if (count == 0) {
			shell_print(sh, "skewing every %s by %+ld ns", types[f].name, ns);
		} else {
			shell_print(sh, "skewing the next %ld %s by %+ld ns", count, types[f].name,
				    ns);
		}
		return 0;
	}

	shell_error(sh, "usage: ptp_fault [off | drop <type> <pct> | delay sync|dresp <ns> "
			"[count]]");
	return -EINVAL;
}

SHELL_CMD_ARG_REGISTER(ptp_fault, NULL,
		       "PTP fault injection: [off | drop <type> <pct> | delay sync|dresp <ns> "
		       "[count]]",
		       cmd_ptp_fault, 1, 4);
