#!/bin/bash
# Stub RAUC custom-bootloader backend. RAUC has no built-in Raspberry Pi
# tryboot support, so `bootloader=custom` in system.conf needs a real
# handler here implementing get-primary/set-primary against tryboot.txt/
# autoboot.txt on the boot partition plus `reboot "0 tryboot"` — none of
# that exists yet (see SLIDE_ANNOUNCER.md, Tier 1, "Bundle format &
# partitioning," and its open questions list).
#
# Failing loudly here (rather than silently no-op'ing) means `rauc install`
# can still be exercised end-to-end up through signature verification and
# slot content write, and only fails at the activation step it's honestly
# not yet able to perform.
echo "rpi-tryboot-backend.sh: tryboot A/B switching not implemented yet — see SLIDE_ANNOUNCER.md, Tier 1" >&2
exit 1
