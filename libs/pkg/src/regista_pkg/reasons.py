"""Why a package is refused. A closed list: the server validates it and the panel shows a fixed
text per reason (design-system.md section 15). Free text from the agent never reaches the panel."""

REASONS = (
    "hash_mismatch",
    "signature_invalid",
    "unknown_key",
    "wrong_client",
    "wrong_package",
    "wrong_version",
    "unsafe_archive",
    "too_large",
    "malformed_package",
)


class PackageError(Exception):
    """A package that must not be used. `reason` is one of `REASONS`; `detail` is for logs only."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in REASONS:  # a bug in the caller, not a property of the package
            raise ValueError(f"unknown reason: {reason}")
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
