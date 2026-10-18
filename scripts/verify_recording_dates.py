"""Compatibility entry point for the current automatic uploading-date workflow.

The editable historical-date/interpretation-approval flow has been replaced by
upload timestamps and optional topic corrections. See verify_upload_flow.py.
"""
from verify_upload_flow import main

if __name__ == "__main__":
    raise SystemExit(main())
