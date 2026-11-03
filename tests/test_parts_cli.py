"""`hwflow parts`: an unknown category is a clear error (exit 2), not an HTTP 404 traceback from the index."""
import subprocess
import sys


def test_unknown_category_is_a_clear_error():
    r = subprocess.run([sys.executable, "-m", "hwflow.cli", "parts", "memory", "--text", "NAND"],
                       capture_output=True, text=True)
    assert r.returncode == 2, r.stderr
    assert "unknown category 'memory'" in r.stderr and "Traceback" not in r.stderr
