/*
 * Shell command `ptp_diag`: read-only snapshot of the PTP thread and its
 * wakeup path, for catching the stall where the thread stays blocked in
 * zsock_poll() while port timeouts are pending.
 *
 * Every output line starts with "diag:" so a host-side monitor can pick them
 * out of the interleaved log stream. The only side effect is a zero-timeout
 * poll on each descriptor, which reports readiness without consuming data.
 */

#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/net/socket.h>
#include <zephyr/shell/shell.h>

#include "ptp/clock.h"
#include "ptp/port.h"

static struct k_thread *ptp_tid;

static void find_ptp_thread(const struct k_thread *thread, void *user_data)
{
	const char *name = k_thread_name_get((k_tid_t)thread);

	ARG_UNUSED(user_data);

	if (name != NULL && strcmp(name, "PTP") == 0) {
		ptp_tid = (struct k_thread *)thread;
	}
}

static int fd_ready(int fd)
{
	struct zsock_pollfd pfd = { .fd = fd, .events = ZSOCK_POLLIN };
	int ret = zsock_poll(&pfd, 1, 0);

	return ret < 0 ? ret : pfd.revents;
}

static int cmd_ptp_diag(const struct shell *sh, size_t argc, char **argv)
{
	const struct zsock_pollfd *pfds;
	struct ptp_port *port;
	size_t count;
	bool valid;
	int work_busy;
	char state[32];

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	shell_print(sh, "diag: uptime_ms=%lld", k_uptime_get());

	k_thread_foreach_unlocked(find_ptp_thread, NULL);
	if (ptp_tid != NULL) {
		shell_print(sh, "diag: thread tid=%p state=%s pended_on=%p", ptp_tid,
			    k_thread_state_str(ptp_tid, state, sizeof(state)),
			    ptp_tid->base.pended_on);
	} else {
		shell_print(sh, "diag: thread not found");
	}

	pfds = ptp_clock_diag_pollfds(&count, &valid, &work_busy);
	shell_print(sh, "diag: wakeup work_busy=0x%x pollfd_valid=%d", work_busy, valid);
	for (size_t i = 0; i < count; i++) {
		shell_print(sh, "diag: pollfd[%u] fd=%d events=0x%x revents=0x%x ready_now=0x%x",
			    (unsigned int)i, pfds[i].fd, pfds[i].events, pfds[i].revents,
			    fd_ready(pfds[i].fd));
	}

	SYS_SLIST_FOR_EACH_CONTAINER(ptp_clock_ports_list(), port, node) {
		shell_print(sh,
			    "diag: port %u state=%d timeouts=0x%lx "
			    "remaining_ms ann=%u delay=%u sync=%u "
			    "seq ann=%u delay=%u sync=%u",
			    port->port_ds.id.port_number, ptp_port_state(port),
			    (unsigned long)atomic_get(&port->timeouts),
			    k_timer_remaining_get(&port->timers.announce),
			    k_timer_remaining_get(&port->timers.delay),
			    k_timer_remaining_get(&port->timers.sync), port->seq_id.announce,
			    port->seq_id.delay, port->seq_id.sync);
	}

	return 0;
}

SHELL_CMD_REGISTER(ptp_diag, NULL, "Snapshot PTP thread and wakeup state", cmd_ptp_diag);
