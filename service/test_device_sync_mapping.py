import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from . import device_sync_service as device_sync
from . import integration_mapping as mapping
from . import rule_sync_service as rule_sync


class Document(dict):
    def __getattr__(self, name):
        return self.get(name)


class DeviceSyncMappingTests(unittest.TestCase):
    def setUp(self):
        self.device = Document(
            name="NW-IT-DR001",
            device_id="NW-IT-DR001",
            site="NW-LOCAL-SITE-001",
            zone=None,
            device_profile="NW-LOCAL-S6",
            status="Active",
            connectivity_type="WIFI",
            config_version=1,
            custom_data_plane_device_id="DR-001",
        )

        self.site = Document(
            name="NW-LOCAL-SITE-001",
            organization="NW-LOCAL-ORG-001",
            custom_data_plane_site_code="hq",
        )

        self.organization = Document(
            name="NW-LOCAL-ORG-001",
            custom_data_plane_org_code="hainam",
        )

        self.rule = Document(
            name="NW-IT-RULE-S6",
            rule_id="NW-IT-RULE-S6",
            version=2,
            scope_type="Device",
            scope_ref="NW-IT-DR001",
            rule_type="STATE_CHANGE",
            schedule=None,
            data_point=None,
            operator=">",
            threshold=0,
            duration_s=0,
            hysteresis=0,
            clear_duration_s=0,
            quiet_window_s=0,
            event_type="DOOR_OPENED",
            open_limit_s=300,
            severity="Medium",
            auto_close=0,
            active=1,
            custom_data_plane_rule_id="S6_DOOR_OPEN",
        )

        self.profile = Document(
            name="NW-LOCAL-S6",
            version=1,
            offline_after_missed=2,
            profile_data_points=[],
            event_types=[
                Document(
                    event_code="DOOR_OPENED",
                    event_name="Mở cửa",
                ),
                Document(
                    event_code="DOOR_CLOSED",
                    event_name="Đóng cửa",
                ),
            ],
        )

        self.documents = {
            ("Device", self.device.name): self.device,
            ("Site", self.site.name): self.site,
            (
                "Organization",
                self.organization.name,
            ): self.organization,
            (
                "Rule Configuration",
                self.rule.name,
            ): self.rule,
            (
                "Device Profile",
                self.profile.name,
            ): self.profile,
        }

        self.repository = Mock()
        self.repository.get_document.side_effect = (
            self.get_document
        )
        self.repository.get_device_rules.return_value = [
            self.rule
        ]

        self.start_patch(
            mapping,
            "frappe",
            SimpleNamespace(
                db=SimpleNamespace(
                    get_value=self.get_value,
                )
            ),
        )

        self.start_patch(
            device_sync,
            "frappe",
            SimpleNamespace(
                conf={
                    "nextwireless_dp_base_url":
                        "https://data-plane.invalid",
                    "nextwireless_dp_inbound_token":
                        "unit-test-token",
                    "nextwireless_dp_org_code":
                        "NW-LOCAL-ORG-001",
                },
                logger=lambda name: Mock(),
            ),
        )

        self.session = MagicMock()
        self.session.headers = {}
        self.session.__enter__.return_value = self.session
        self.session.put.side_effect = self.put_response

        self.session_factory = self.start_patch(
            device_sync.requests,
            "Session",
            Mock(return_value=self.session),
        )

        self.service = device_sync.DeviceSyncService(
            self.repository
        )

    def start_patch(self, target, name, value):
        patcher = patch.object(target, name, value)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def get_document(self, doctype, name):
        return self.documents[(doctype, name)]

    def get_value(self, doctype, name, fields, as_dict=False):
        return self.documents.get((doctype, name))

    def put_response(self, url, json, **kwargs):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "status": "applied",
            "applied_version": json.get(
                "version",
                json.get("config_version"),
            ),
        }
        return response

    def test_device_payload_uses_remote_identities(self):
        payload = self.service.build_device(self.device)

        self.assertEqual(payload["device_id"], "DR-001")
        self.assertEqual(payload["org_code"], "hainam")
        self.assertEqual(payload["site_code"], "hq")
        self.assertEqual(payload["config_version"], 1)

        self.session_factory.assert_not_called()

    def test_new_device_keeps_parent_mappings(self):
        new_device = Document(self.device)
        new_device.update(
            name="NW-LOCAL-DR002",
            device_id="NW-LOCAL-DR002",
            custom_data_plane_device_id=None,
        )

        payload = self.service.build_device(new_device)

        self.assertEqual(
            payload["device_id"],
            "NW-LOCAL-DR002",
        )
        self.assertEqual(payload["org_code"], "hainam")
        self.assertEqual(payload["site_code"], "hq")

    def test_rule_payload_is_shared_and_preserves_s6_configuration(self):
        expected = {
            "rule_id": "S6_DOOR_OPEN",
            "version": 2,
            "org_code": "hainam",
            "rule_type": "STATE_CHANGE",
            "scope": {
                "type": "DEVICE",
                "ref": "DR-001",
            },
            "data_point": None,
            "operator": None,
            "threshold": None,
            "duration_s": 0,
            "hysteresis": 0.0,
            "clear_duration_s": 0,
            "quiet_window_s": 0,
            "event_type": "DOOR_OPENED",
            "open_limit_s": 300,
            "schedule": None,
            "severity": "Medium",
            "auto_close": False,
            "active": True,
        }

        bundled = self.service.build_rule(
            self.rule,
            self.device.name,
        )

        path, standalone = rule_sync.RuleSyncService(
            self.repository
        ).build_operation(self.rule.name)

        self.assertEqual(bundled, expected)
        self.assertEqual(standalone, expected)
        self.assertEqual(
            path,
            "/api/v1/sync/rules/S6_DOOR_OPEN",
        )

    def test_s1_threshold_configuration_is_preserved(self):
        device = Document(self.device)
        device.update(
            name="NW-IT-TH001",
            device_id="NW-IT-TH001",
            device_profile="NW-LOCAL-S1",
            custom_data_plane_device_id="TH-001",
        )

        rule = Document(self.rule)
        rule.update(
            name="NW-IT-RULE-S1",
            rule_id="NW-IT-RULE-S1",
            scope_ref="NW-IT-TH001",
            rule_type="THRESHOLD",
            data_point="temperature",
            operator=">",
            threshold=30,
            event_type=None,
            open_limit_s=0,
            severity="High",
            auto_close=1,
            custom_data_plane_rule_id="S1_TEMP_HIGH",
        )

        self.documents[("Device", device.name)] = device
        self.documents[
            ("Rule Configuration", rule.name)
        ] = rule

        payload = self.service.build_rule(
            rule,
            device.name,
        )

        self.assertEqual(payload["rule_id"], "S1_TEMP_HIGH")
        self.assertEqual(payload["version"], 2)
        self.assertEqual(
            payload["scope"],
            {"type": "DEVICE", "ref": "TH-001"},
        )
        self.assertEqual(payload["org_code"], "hainam")
        self.assertEqual(payload["data_point"], "temperature")
        self.assertEqual(payload["operator"], ">")
        self.assertEqual(payload["threshold"], 30.0)
        self.assertEqual(payload["severity"], "High")
        self.assertTrue(payload["auto_close"])
        self.assertIsNone(payload["event_type"])

    def test_rule_without_alias_uses_local_id(self):
        self.rule[mapping.RULE_FIELD] = None

        payload = self.service.build_rule(
            self.rule,
            self.device.name,
        )

        self.assertEqual(
            payload["rule_id"],
            "NW-IT-RULE-S6",
        )
        self.assertEqual(
            payload["scope"]["ref"],
            "DR-001",
        )

    def test_bundle_header_and_paths_match_payloads(self):
        self.service.sync_device_bundle(
            self.device.name
        )

        self.assertEqual(
            self.session.headers["X-Org-Code"],
            "hainam",
        )
        self.assertEqual(
            self.session.put.call_count,
            3,
        )

        calls = self.session.put.call_args_list

        self.assertTrue(
            calls[1].args[0].endswith(
                "/api/v1/sync/devices/DR-001"
            )
        )
        self.assertTrue(
            calls[2].args[0].endswith(
                "/api/v1/sync/rules/S6_DOOR_OPEN"
            )
        )

        self.assertEqual(
            calls[1].kwargs["json"]["org_code"],
            "hainam",
        )
        self.assertEqual(
            calls[2].kwargs["json"]["org_code"],
            "hainam",
        )

    def test_wrong_organization_fails_before_http(self):
        self.site["organization"] = "ANOTHER-ORG"

        with self.assertRaises(ValueError):
            self.service.sync_device_bundle(
                self.device.name
            )

        self.session_factory.assert_not_called()

    def test_version_conflict_stops_without_retry(self):
        response = Mock()
        response.status_code = 409

        self.session.put.side_effect = None
        self.session.put.return_value = response

        with self.assertRaises(
            device_sync.DataPlaneVersionConflict
        ):
            self.service.sync_device_bundle(
                self.device.name
            )

        self.session.put.assert_called_once()
        self.assertEqual(self.rule.version, 2)
        self.assertEqual(self.device.config_version, 1)


if __name__ == "__main__":
    unittest.main()