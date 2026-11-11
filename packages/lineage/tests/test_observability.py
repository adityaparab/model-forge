import io
import json

import structlog
from modelforge_lineage.observability import configure_logging, get_logger


def test_logging_is_json_lines_on_given_stream() -> None:
    buf = io.StringIO()
    saved = structlog.get_config()
    try:
        configure_logging("INFO", stream=buf)
        get_logger(run_id="run_x").info("hello", n=1)
    finally:
        # The global configuration must not outlive the test: later tests would log to `buf`.
        structlog.configure(**saved)
    record = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert record["event"] == "hello" and record["run_id"] == "run_x" and record["n"] == 1
