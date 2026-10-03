"""Board number -> console serial port, shared by the Python tools.

Board N also gets IPv4 192.168.40.10N (see build.sh, which keeps its own
copy of this table). B1-B4 are on the TL-SG108PE, B5-B8 on the GS308EP.
B8 is excluded from default board sets (see EXCLUDED).
"""

_BY_ID = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_{}-if00"

SERIALS = {
    1: "5B90094322",  # e8:f6:0a:e4:44:e1
    2: "5B90094925",  # e8:f6:0a:e7:1f:39
    3: "5B90094401",  # e8:f6:0a:e4:45:31
    4: "5B90038724",  # e8:f6:0a:e4:28:86
    5: "5B90038769",  # e8:f6:0a:e4:28:b2
    6: "5B90094190",  # e8:f6:0a:e4:45:b4
    7: "5B90157899",  # e8:f6:0a:e0:cf:20
    8: "5B90158720",  # e8:f6:0a:e0:cc:9f
}

# Left out of default board sets; still selectable explicitly with --boards.
EXCLUDED = {
    8: "unknown defect: bursts of power-on resets (rst:0x1 POWERON), 2026-10-03",
}

ALL_PORTS = {n: _BY_ID.format(s) for n, s in SERIALS.items()}
PORTS = {n: p for n, p in ALL_PORTS.items() if n not in EXCLUDED}


def select(spec=None):
    """Return {n: port} for a comma list like "1,2,5" (default: all but
    EXCLUDED)."""
    if not spec:
        return dict(PORTS)
    return {int(n): ALL_PORTS[int(n)] for n in str(spec).split(",")}
