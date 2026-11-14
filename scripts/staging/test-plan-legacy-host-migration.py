#!/usr/bin/env python3
"""Focused checks for the pure legacy-host migration planner."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("plan-legacy-host-migration.py")
SPEC = importlib.util.spec_from_file_location("legacy_host_migration_plan", SCRIPT)
planner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(planner)


def observation():
    return {
        "schema": planner.SCHEMA,
        "source_sha": "61237347fc4402139ba575d439218f664d671c93",
        "actual_host": "ubuntu-worker",
        "actual_principal": "hiroyuki",
        "actual_uid": 1000,
        "same_physical_device_confirmed": True,
        "serve_config": {
            "TCP": {"443": {"HTTPS": True}},
            "Web": {
                "fedora-worker.tail63cd2d.ts.net:443": {
                    "Handlers": {
                        "/bri-referee-pilot-comp.png": {
                            "Path": "/home/hiroyuki/tailscale-share/bri-referee-pilot-comp.png"
                        }
                    }
                }
            },
        },
        "cert_domains": ["ubuntu-worker.tail63cd2d.ts.net"],
        "legacy_asset_observation": {
            "path": "/home/hiroyuki/tailscale-share/bri-referee-pilot-comp.png",
            "state": "verified_absent",
        },
    }


class PlanLegacyHostMigrationTests(unittest.TestCase):
    def test_same_physical_device_plan_requires_approval_and_proposes_custom_domain_only(self):
        plan = planner.build_plan(observation())

        self.assertEqual(plan["status"], "APPROVAL_REQUIRED")
        self.assertIs(plan["deployment_authorized"], False)
        self.assertEqual(plan["proposed_config"], {
            "TCP": {"443": {"TCPForward": "127.0.0.1:18443"}},
            "Web": {},
        })
        self.assertEqual([change["op"] for change in plan["config_diff"]], ["replace", "remove"])
        self.assertIn("explicit owner approval", plan["owner_decision"])
        self.assertIn("separate review", plan["readiness_note"])

    def test_rollback_is_exact_original_config_and_current_digest_is_deterministic(self):
        observed = observation()
        plan = planner.build_plan(observed)
        reordered = json.loads(json.dumps(observed, sort_keys=True))
        reordered["serve_config"] = {
            "Web": observed["serve_config"]["Web"],
            "TCP": observed["serve_config"]["TCP"],
        }
        repeat = planner.build_plan(reordered)

        self.assertEqual(plan["rollback_exact_config"], observed["serve_config"])
        self.assertEqual(plan["current_config_sha256"], repeat["current_config_sha256"])
        self.assertEqual(plan["context_sha256"], repeat["context_sha256"])
        changed_source = copy.deepcopy(observed)
        changed_source["source_sha"] = "a" * 40
        changed_plan = planner.build_plan(changed_source)
        self.assertEqual(plan["current_config_sha256"], changed_plan["current_config_sha256"])
        self.assertNotEqual(plan["context_sha256"], changed_plan["context_sha256"])

    def test_unknown_additional_web_routes_are_rejected(self):
        observed = observation()
        observed["serve_config"]["Web"]["other.tailnet.ts.net:443"] = {}
        self.assert_rejected(observed)

    def test_unknown_tcp_route_is_rejected(self):
        observed = observation()
        observed["serve_config"]["TCP"]["8443"] = {"HTTPS": True}
        self.assert_rejected(observed)

    def test_public_funnel_or_forward_state_is_rejected(self):
        for replacement in (
            {"TCP": {"443": {"HTTPS": True, "Funnel": True}}, "Web": observation()["serve_config"]["Web"]},
            {"TCP": {"443": {"TCPForward": "0.0.0.0:18443"}}, "Web": observation()["serve_config"]["Web"]},
        ):
            observed = observation()
            observed["serve_config"] = replacement
            self.assert_rejected(observed)

    def test_missing_observations_and_missing_same_host_confirmation_are_rejected(self):
        for key in ("actual_host", "actual_principal", "serve_config", "cert_domains", "legacy_asset_observation"):
            observed = observation()
            del observed[key]
            self.assert_rejected(observed)
        observed = observation()
        observed["same_physical_device_confirmed"] = False
        self.assert_rejected(observed)

    def test_wrong_host_principal_domains_or_source_pin_are_rejected(self):
        mutations = (
            ("actual_host", "fedora-worker"),
            ("actual_principal", "root"),
            ("actual_uid", 0),
            ("cert_domains", ["fedora-worker.tail63cd2d.ts.net"]),
            ("source_sha", "not-a-git-sha"),
        )
        for key, value in mutations:
            observed = observation()
            observed[key] = value
            self.assert_rejected(observed)

    def test_present_or_unknown_legacy_asset_is_rejected(self):
        for state in ("present", "unknown", None):
            observed = observation()
            observed["legacy_asset_observation"]["state"] = state
            self.assert_rejected(observed)

    def test_malformed_duplicate_and_oversized_inputs_are_rejected(self):
        with self.assertRaises(planner.ObservationError):
            planner.plan_from_bytes(b'{"schema":"x","schema":"y"}')
        with self.assertRaises(planner.ObservationError):
            planner.plan_from_bytes(b" " * (planner.MAX_INPUT_BYTES + 1))

    def assert_rejected(self, observed):
        with self.assertRaises(planner.ObservationError):
            planner.build_plan(observed)


if __name__ == "__main__":
    unittest.main()
