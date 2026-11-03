"""Shared connection settings and HTTP acknowledgement handling.

This module does not access Frappe, the database, or background jobs.
"""

from dataclasses import dataclass, field
from urllib.parse import urlsplit


class DataPlaneSyncError(RuntimeError):
    pass


class DataPlaneVersionConflict(DataPlaneSyncError):
    pass


def positive_version(value, label):
    try:
        version = int(str(value))
    except (TypeError, ValueError):
        raise ValueError(
            f"{label} must be a positive integer"
        ) from None

    if version < 1:
        raise ValueError(
            f"{label} must be a positive integer"
        )

    return version


def _valid_org_code(value):
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= 140
        and not any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    )


@dataclass(frozen=True)
class SyncConnection:
    base_url: str
    local_org: str
    remote_org: str
    token: str = field(repr=False)

    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Org-Code": self.remote_org,
            "Accept": "application/json",
            "ngrok-skip-browser-warning": "true",
        }


def load_sync_connection(config, *, local_org, remote_org):
    """Validate configuration without making a network request."""

    base = config.get("nextwireless_dp_base_url")
    token = config.get("nextwireless_dp_inbound_token")
    configured_org = config.get("nextwireless_dp_org_code")

    if (
        not isinstance(base, str)
        or not base
        or base != base.strip()
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in base
        )
        or not isinstance(token, str)
        or not token.strip()
    ):
        raise DataPlaneSyncError(
            "Data Plane site configuration missing or invalid"
        )

    if not _valid_org_code(local_org) or not _valid_org_code(remote_org):
        raise DataPlaneSyncError(
            "Data Plane Organization mapping missing or invalid"
        )

    if configured_org != local_org:
        raise DataPlaneSyncError(
            "Local Organization is outside the configured connection"
        )

    base = base.rstrip("/")

    try:
        parsed = urlsplit(base)
        # Accessing port also validates malformed/out-of-range ports.
        parsed.port
    except ValueError:
        raise DataPlaneSyncError(
            "Data Plane base URL is invalid"
        ) from None

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise DataPlaneSyncError(
            "Data Plane base URL is invalid"
        )

    return SyncConnection(
        base_url=base,
        local_org=local_org,
        remote_org=remote_org,
        token=token.strip(),
    )


def send_put(
    session,
    base,
    path,
    payload,
    *,
    require_applied_version=False,
    logger=None,
):
    """Send once and validate the ACK without changing the payload."""

    response = session.put(
        base + path,
        json=payload,
        timeout=(5, 15),
        allow_redirects=False,
    )

    if response.status_code == 409:
        raise DataPlaneVersionConflict(
            f"Data Plane version conflict: {path} (HTTP 409)"
        )

    if not 200 <= response.status_code < 300:
        raise DataPlaneSyncError(
            f"Data Plane HTTP {response.status_code}: {path}"
        )

    try:
        body = response.json()
    except ValueError:
        raise DataPlaneSyncError(
            f"Invalid Data Plane JSON ACK: {path}"
        ) from None

    if (
        not isinstance(body, dict)
        or body.get("status") not in ("applied", "duplicate")
    ):
        raise DataPlaneSyncError(
            f"Unexpected Data Plane ACK: {path}"
        )

    expected_version = payload.get(
        "version",
        payload.get("config_version"),
    )
    received_version = body.get("applied_version")

    if received_version is None:
        if require_applied_version:
            raise DataPlaneSyncError(
                f"Data Plane ACK missing applied_version: {path}"
            )
    else:
        try:
            applied_version = positive_version(
                received_version,
                "applied_version",
            )
        except ValueError:
            raise DataPlaneSyncError(
                f"Invalid Data Plane applied_version: {path}"
            ) from None

        if applied_version != expected_version:
            raise DataPlaneSyncError(
                f"Unexpected Data Plane applied_version: {path}"
            )

    if logger is not None:
        logger.info(
            "Sync %s => %s version=%s",
            path,
            body["status"],
            expected_version,
        )

    return body