"""Collect processor diagnostics per execution without redirecting global stdout."""
import builtins
from contextvars import ContextVar
import json
import re
from threading import Lock

_current_report = ContextVar("excel_processing_report", default=None)
dashboard_write_lock = Lock()


def report_print(*args, **kwargs):
    builtins.print(*args, **kwargs)
    report = _current_report.get()
    if report is None:
        return
    message = kwargs.get("sep", " ").join(str(arg) for arg in args).strip()
    if not message:
        return
    if re.search(r"❌|\berror\b|\bfailed\b|\bfatal\b", message, re.I):
        level = "error"
    elif re.search(r"⚠|\bmissing\b|\bnot found\b|\bskipp|\bwarning\b|\bno .*found\b", message, re.I):
        level = "warning"
    else:
        level = "info"
    if level in ("error", "warning"):
        report['has_' + level + 's'] = True
    # Large data dumps belong in output previews, not diagnostic messages.
    if len(report["messages"]) < 1000:
        report["messages"].append({"level": level, "message": message[:4000] + ('… [truncated]' if len(message) > 4000 else '')})
    else:
        report["omitted_messages"] = report.get("omitted_messages", 0) + 1


def record_upload(source, bucket, destination, *, error=None, payload=None):
    report = _current_report.get()
    if report is None:
        return
    receipt = {
        "source": str(source), "destination": f"gs://{bucket}/{destination}",
        "status": "failed" if error else "uploaded", "error": str(error or ""),
    }
    if payload is not None:
        receipt["data"] = json.loads(payload)
    report["uploads"].append(receipt)
