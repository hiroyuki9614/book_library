#!/usr/bin/env python3
"""Keep only Moby's start-resolved OOM defaults out of the container CAS digest."""

import copy
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "retained_cutover", Path(__file__).with_name("retained-cutover.py")
)
cutover = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(cutover)


def created_container():
    return {
        "Id": "a" * 64,
        "Image": "sha256:" + "b" * 64,
        "Config": {"Env": ["APP_MODE=staging"], "Labels": {"service": "backend"}},
        "HostConfig": {
            "OomScoreAdj": 0,
            "OomKillDisable": False,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "PortBindings": {},
        },
        "Mounts": [{"Type": "volume", "Name": "candidate-books", "Destination": "/home/node"}],
        "NetworkSettings": {"Networks": {"candidate_isolated": {}}},
    }


class RetainedCutoverDigestTests(unittest.TestCase):
    def test_moby_start_resolved_oom_defaults_do_not_break_cas(self):
        created = created_container()
        started = copy.deepcopy(created)
        started["HostConfig"]["OomScoreAdj"] = 200
        started["HostConfig"].pop("OomKillDisable")
        self.assertEqual(cutover.configuration_digest(created), cutover.configuration_digest(started))

    def test_other_container_configuration_drift_still_breaks_cas(self):
        original = created_container()
        changes = (
            ("HostConfig", "Privileged", True),
            ("HostConfig", "CapDrop", []),
            ("HostConfig", "PortBindings", {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}),
            ("Config", "Env", ["APP_MODE=production"]),
            ("Mounts", None, [{"Type": "bind", "Source": "/tmp", "Destination": "/home/node"}]),
            ("NetworkSettings", "Networks", {"other_isolated": {}}),
        )
        for section, key, value in changes:
            with self.subTest(section=section, key=key):
                changed = copy.deepcopy(original)
                if key is None:
                    changed[section] = value
                else:
                    changed[section][key] = value
                self.assertNotEqual(cutover.configuration_digest(original), cutover.configuration_digest(changed))


if __name__ == "__main__":
    unittest.main()
