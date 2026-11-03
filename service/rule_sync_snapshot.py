"""Immutable Rule configuration snapshots.

Contains no Frappe access, database operations, or HTTP requests.
"""

from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
from urllib.parse import quote


def _require_identity(value, label):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 140
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise ValueError(f"{label} is invalid")

    return value


def _reject_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _decode_payload(payload_json):
    if not isinstance(payload_json, str):
        raise ValueError("Snapshot payload must be a JSON string")

    try:
        payload = json.loads(
            payload_json,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, TypeError):
        raise ValueError(
            "Snapshot payload is not valid JSON"
        ) from None

    if not isinstance(payload, dict):
        raise ValueError("Snapshot payload must be an object")

    _require_identity(payload.get("rule_id"), "Remote Rule ID")
    _require_identity(payload.get("org_code"), "Remote Organization")

    version = payload.get("version")
    if type(version) is not int or version < 1:
        raise ValueError(
            "Snapshot Rule version must be a positive integer"
        )

    scope = payload.get("scope")
    if not isinstance(scope, dict) or scope.get("type") != "DEVICE":
        raise ValueError(
            "Snapshot requires a Device-scoped Rule"
        )

    _require_identity(scope.get("ref"), "Remote Device ID")

    return payload


def _calculate_checksum(
    local_rule_id,
    local_org_code,
    path,
    payload_json,
):
    # Protect local routing metadata as well as the payload.
    # This is an internal integrity checksum, not the IF-03 payload hash.
    content = json.dumps(
        [
            local_rule_id,
            local_org_code,
            path,
            payload_json,
        ],
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RuleSyncSnapshot:
    local_rule_id: str
    local_org_code: str
    path: str
    payload_json: str
    checksum: str

    def __post_init__(self):
        self.validate()

    @classmethod
    def capture(
        cls,
        *,
        local_rule_id,
        local_org_code,
        path,
        payload,
    ):
        """Detach a snapshot from the caller's mutable payload."""

        try:
            payload_json = json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise ValueError(
                "Rule payload cannot be serialized as valid JSON"
            ) from None

        return cls(
            local_rule_id=local_rule_id,
            local_org_code=local_org_code,
            path=path,
            payload_json=payload_json,
            checksum=_calculate_checksum(
                local_rule_id,
                local_org_code,
                path,
                payload_json,
            ),
        )

    def validate(self):
        _require_identity(self.local_rule_id, "Local Rule ID")
        _require_identity(self.local_org_code, "Local Organization")

        payload = _decode_payload(self.payload_json)
        expected_path = (
            "/api/v1/sync/rules/"
            + quote(payload["rule_id"], safe="")
        )

        if self.path != expected_path:
            raise ValueError(
                "Snapshot endpoint does not match its Rule ID"
            )

        if (
            not isinstance(self.checksum, str)
            or len(self.checksum) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.checksum
            )
        ):
            raise ValueError("Snapshot checksum is invalid")

        expected_checksum = _calculate_checksum(
            self.local_rule_id,
            self.local_org_code,
            self.path,
            self.payload_json,
        )

        if not hmac.compare_digest(
            self.checksum,
            expected_checksum,
        ):
            raise ValueError("Snapshot integrity check failed")

    def to_payload(self):
        """Return an independent dictionary for each send attempt."""

        self.validate()
        return _decode_payload(self.payload_json)

    def to_record(self):
        """Return serializable fields for a future persistence layer."""

        self.validate()
        return asdict(self)

    @classmethod
    def from_record(cls, record):
        """Restore and verify a previously serialized snapshot."""

        fields = {
            "local_rule_id",
            "local_org_code",
            "path",
            "payload_json",
            "checksum",
        }

        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError("Invalid snapshot record structure")

        return cls(**record)