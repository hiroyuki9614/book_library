#!/usr/bin/env python3
"""Keep only Moby's start-resolved OOM defaults out of the container CAS digest."""

import copy
import importlib.util
import json
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

    def test_v2_observation_uses_exact_prior_project_and_tls_ports(self):
        prior_project = cutover.PROJECT + "-upgrade-" + "1" * 32
        next_project = cutover.PROJECT + "-upgrade-" + "2" * 32
        old, new, containers, networks, images = {}, {}, {}, {}, {}
        for side, project, revision in (("old", prior_project, "a" * 40),
                                        ("new", next_project, "b" * 40)):
            for index, service in enumerate(cutover.SERVICES):
                identity = format(index + (0 if side == "old" else 3) + 1, "064x")
                isolated, ingress = project + "_isolated", project + "_ingress"
                base_network = 1 if side == "old" else 3
                network_names = {isolated: format(base_network, "064x")}
                if service == "nginx":
                    network_names[ingress] = format(base_network + 1, "064x")
                image_id = "sha256:" + format(index + (0 if side == "old" else 3) + 100, "064x")
                ports = {}
                if service == "nginx":
                    ports = {"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":"18083"}]}
                    if side == "old":
                        ports["8443/tcp"] = [{"HostIp":"127.0.0.1","HostPort":"18443"}]
                info = {"Id":identity,"Image":image_id,
                    "Config":{"Labels":{"com.docker.compose.project":project,
                        "com.docker.compose.service":service}},
                    "HostConfig":{"PortBindings":ports},"Mounts":[],
                    "NetworkSettings":{"Networks":{name:{"NetworkID":network_id}
                        for name,network_id in network_names.items()}},"State":{"Running":True}}
                containers[identity] = info
                images[image_id] = {"Id":image_id,"Config":{"Labels":{"org.opencontainers.image.revision":revision}}}
                for name, network_id in network_names.items():
                    networks[name] = {"Id":network_id,"Labels":{"com.docker.compose.project":project}}
                pin = {"id":identity,"configuration_sha256":cutover.configuration_digest(info),
                    "network_ids":network_names}
                if side == "old": pin["image_id"] = image_id
                (old if side == "old" else new)[service] = pin
        plan = {"schema":"belib-isolated-copy-cutover/v2","source_sha":"b"*40,
            "operation_id":"2"*32,"port":18083,"prior_project":prior_project,
            "prior_application_sha":"a"*40,"prior_generation_sha256":"c"*64,
            "prior_http_port":18083,"prior_tls_loopback_port":18443,"old":old,"new":new}
        controller = cutover.RetainedCutover(plan)
        def fake_docker(*args):
            if args[0] == "inspect": return json.dumps([containers[args[1]]]).encode()
            if args[0] == "image": return json.dumps([images[args[2]]]).encode()
            if args[0] == "network": return json.dumps([networks[args[2]]]).encode()
            raise AssertionError(args)
        controller.docker = fake_docker
        self.assertEqual(controller.observe("old"), {"backend":True,"frontend":True,"nginx":True})
        plan["prior_tls_loopback_port"] = 18444
        bad = cutover.RetainedCutover(plan)
        bad.docker = fake_docker
        with self.assertRaisesRegex(cutover.CutoverBlocked, "staging loopback endpoint"):
            bad.observe("old")


if __name__ == "__main__":
    unittest.main()
