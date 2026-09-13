"""Control optional private details in diagnostic summaries."""

import traceback


def exception_details(error: Exception, include_private: bool) -> dict[str, str]:
    """Keep exceptions with paths or server identities out of default summaries.

    This controls diagnostic summaries only. ANSYS can independently write private
    information to its own files and console output.
    """
    details = {"error_type": type(error).__name__}
    if include_private:
        details["error"] = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
    else:
        details["error"] = (
            "Exception details omitted. Use --include-private-diagnostics "
            "for local troubleshooting; see docs/getting-started.md#ansys-executable-and-license."
        )
    return details
