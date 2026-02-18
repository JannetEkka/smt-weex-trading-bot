# Cross-import compatibility shim — DO NOT RUN THIS FILE DIRECTLY.
#
# smt_nightly_trade_v3_1.py contains lazy imports like:
#   from smt_daemon_v3_1 import get_trade_history_summary
#   from smt_daemon_v3_1 import get_rl_performance_summary
#   from smt_daemon_v3_1 import get_pair_sizing_multiplier
#
# Since the real daemon lives in smt_daemon_v2_1.py (and will be v2_2, v2_3, ...),
# this shim re-exports everything so the nightly's imports keep working unchanged.
#
# ON EACH VERSION BUMP: update the single line below to point to the new file.
#   e.g. when v2_2 is released: change "smt_daemon_v2_1" -> "smt_daemon_v2_2"
#
from smt_daemon_v2_1 import *  # noqa: F401,F403
