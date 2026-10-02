/*
 * Shell command `ptp_prio`: show or change this clock's priority1/priority2
 * at runtime, then re-run the best-master decision so a grandmaster change
 * takes effect immediately instead of at the next Announce.
 *
 *   ptp_prio              show priority1/priority2 and the port state
 *   ptp_prio <p1> [<p2>]  set them (0-255; lower wins)
 *
 * Not persistent: a reboot restores CONFIG_PTP_PRIORITY1/2 (see GM= in
 * build.sh).
 */

#include <stdlib.h>

#include <zephyr/shell/shell.h>

#include "ptp/clock.h"
#include "ptp/port.h"

static int parse_prio(const struct shell *sh, const char *arg, uint8_t *out)
{
	char *end;
	long v = strtol(arg, &end, 0);

	if (*arg == '\0' || *end != '\0' || v < 0 || v > 255) {
		shell_error(sh, "invalid priority '%s' (0-255)", arg);
		return -EINVAL;
	}
	*out = (uint8_t)v;
	return 0;
}

static int cmd_ptp_prio(const struct shell *sh, size_t argc, char **argv)
{
	/* The stack exposes the default dataset read-only; the object itself is
	 * writable and the management SET PRIORITY1 handler writes it the same way.
	 */
	struct ptp_default_ds *dds = (struct ptp_default_ds *)ptp_clock_default_ds();
	struct ptp_port *port;
	uint8_t p1 = dds->priority1;
	uint8_t p2 = dds->priority2;

	if (argc > 1) {
		if (parse_prio(sh, argv[1], &p1) < 0 ||
		    (argc > 2 && parse_prio(sh, argv[2], &p2) < 0)) {
			return -EINVAL;
		}
		dds->priority1 = p1;
		dds->priority2 = p2;
		ptp_clock_state_decision_req();
	}

	shell_print(sh, "priority1 %u priority2 %u", dds->priority1, dds->priority2);
	SYS_SLIST_FOR_EACH_CONTAINER(ptp_clock_ports_list(), port, node) {
		shell_print(sh, "port %u state %d (re-evaluated%s)", port->port_ds.id.port_number,
			    ptp_port_state(port), argc > 1 ? "" : " at next Announce");
	}

	return 0;
}

SHELL_CMD_ARG_REGISTER(ptp_prio, NULL,
		       "Show or set PTP priority1 [priority2] (0-255, lower wins); not persistent",
		       cmd_ptp_prio, 1, 2);
