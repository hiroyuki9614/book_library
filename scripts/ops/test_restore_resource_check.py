import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import restore_resource_check as checker


class ResourceCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "destination"
        self.target.mkdir()
        self.meminfo = self.root / "meminfo.mock"
        self.pressure = self.root / "pressure.mock"
        self.write_sensors()

    def tearDown(self):
        self.temp.cleanup()

    def write_sensors(self, mem="MemAvailable: 2097152 kB\n", pressure="some avg10=1.00 avg60=2.00 avg300=3.00 total=10\nfull avg10=1.00 avg60=2.00 avg300=3.00 total=10\n"):
        self.meminfo.write_text(mem, encoding="ascii")
        self.pressure.write_text(pressure, encoding="ascii")

    def test_measures_only_supplied_target_and_sensor_files(self):
        result = checker.measure(
            str(self.target), self.meminfo, self.pressure,
            disk_usage=lambda path: type("Usage", (), {"free": 3 * 1024**3})(),
        )
        self.assertEqual(result["free_bytes"], 3 * 1024**3)
        self.assertEqual(result["mem_available_bytes"], 2 * 1024**3)
        self.assertEqual(result["io_full_avg10_percent"], checker.Decimal("1.00"))

    def test_protected_or_missing_sensors_fail_closed(self):
        with self.assertRaises(OSError):
            checker.measure(str(self.target), self.root / "unreadable", self.pressure)
        self.pressure.write_text("some avg10=0 avg60=0\n", encoding="ascii")
        with self.assertRaises(checker.InputError):
            checker.measure(str(self.target), self.meminfo, self.pressure)

    def test_bad_pressure_and_missing_target_fail_closed(self):
        self.write_sensors(pressure="full avg10=nan avg60=1\n")
        with self.assertRaises(checker.InputError):
            checker.measure(str(self.target), self.meminfo, self.pressure)
        with self.assertRaises(checker.InputError):
            checker.measure(str(self.root / "absent"), self.meminfo, self.pressure)

    def test_full_pressure_requires_every_field_exactly_once(self):
        malformed = (
            "full avg10=1 avg60=2 total=10\n",  # missing avg300
            "full avg10=1 avg60=2 avg300=3\n",  # missing total
            "full avg10=1 avg60=2 avg300=3 avg300=4 total=10\n",
            "full avg10=1 avg60=2 avg300=3 total=10 total=11\n",
        )
        for record in malformed:
            with self.subTest(record=record), self.assertRaises(checker.InputError):
                self.pressure.write_text(record, encoding="ascii")
                checker._read_io_full_pressure(self.pressure)

    def test_vacuous_and_overflow_thresholds_are_rejected(self):
        with self.assertRaises(checker.InputError):
            checker._positive_integer("1", "min", checker.MINIMUM_FLOOR_BYTES)
        with self.assertRaises(checker.InputError):
            checker._positive_integer(str(2**63), "min", 1)
        for bad in ("-1", "nan", "1e999", "101"):
            with self.subTest(bad=bad), self.assertRaises(checker.InputError):
                checker._pressure_limit(bad, "pressure")

    def test_cli_emits_candidate_only_json_when_all_explicit_gates_pass(self):
        argv = ["--target", str(self.target), "--min-free-bytes", str(2 * 1024**3),
                "--min-memavailable-bytes", str(1024**3), "--max-io-full-avg10", "5",
                "--max-io-full-avg60", "5"]
        stdout = io.StringIO()
        with patch.object(checker, "measure", return_value={
                "free_bytes": 3 * 1024**3,
                "mem_available_bytes": 2 * 1024**3,
                "io_full_avg10_percent": checker.Decimal("1"),
                "io_full_avg60_percent": checker.Decimal("2"),
        }), contextlib.redirect_stdout(stdout):
            # Explicit injection keeps this CLI test independent of host sensors.
            code = checker.main(argv)
        output = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(output["code"], "RESOURCE_CANDIDATE_ONLY")
        self.assertNotIn("target", output)

    def test_resource_shortfall_is_nonzero_block(self):
        argv = ["--target", str(self.target), "--min-free-bytes", str(2 * 1024**3),
                "--min-memavailable-bytes", str(1024**3), "--max-io-full-avg10", "5",
                "--max-io-full-avg60", "5"]
        stdout = io.StringIO()
        with patch.object(checker, "measure", return_value={
                "free_bytes": 1024**3,
                "mem_available_bytes": 2 * 1024**3,
                "io_full_avg10_percent": checker.Decimal("1"),
                "io_full_avg60_percent": checker.Decimal("2"),
        }), contextlib.redirect_stdout(stdout):
            code = checker.main(argv)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["code"], "BLOCK_RESOURCE")

    def test_missing_cli_input_is_json_block(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = checker.main([])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["code"], "BLOCK_UNMEASURED")

    def test_help_or_unknown_options_do_not_emit_non_json_output(self):
        for argv in (["--help"], ["--unknown"]):
            stdout = io.StringIO()
            with self.subTest(argv=argv), contextlib.redirect_stdout(stdout):
                code = checker.main(argv)
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())["code"], "BLOCK_UNMEASURED")


if __name__ == "__main__":
    unittest.main()
