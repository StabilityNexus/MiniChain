import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# Temporary diagnostic: opt-in (MINICHAIN_MEMORY_PROBE=1) per-test peak RSS
# logging, added to pin down a CI-only crash where pytest dies partway
# through the suite with no traceback (consistent with an OOM kill).
# Safe to remove once the CI memory issue is understood/fixed.
if os.environ.get("MINICHAIN_MEMORY_PROBE"):
    import resource

    def pytest_runtest_logreport(report):
        if report.when != "call":
            return
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"[MEM] {report.nodeid} peak_rss={rss_mb:.1f}MB", flush=True)