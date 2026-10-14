import json
import tempfile
import unittest
from pathlib import Path

from jobwatch.estimate import (Durations, Estimator, LogScan, file_observation, fmt,
                               item_observation, items_from_args, line_observation, parse_step)

RULE = {"arg": "--folds", "start": "^=== fold (?P<item>\\S+)", "skip": "^skip fold (?P<item>\\S+)",
        "unit": "fold"}


class LineTests(unittest.TestCase):
    def test_tqdm(self):
        obs = line_observation(" 45%|####5     | 45/100 [00:10<00:12,  4.5it/s]")
        self.assertEqual((obs["done"], obs["total"], obs["unit"]), (45, 100, "it"))

    def test_count_means_this_unit_started(self):
        obs = line_observation("Epoch [3/10] loss=0.41")
        self.assertEqual((obs["done"], obs["total"], obs["unit"]), (2, 10, "epoch"))
        obs = line_observation("processing shard 7 of 120")
        self.assertEqual((obs["done"], obs["total"], obs["unit"]), (6, 120, "shard"))

    def test_no_progress(self):
        self.assertIsNone(line_observation("loading the model weights"))
        self.assertIsNone(line_observation("ratio 5/3 is too high"))  # i > n


class StepTests(unittest.TestCase):
    def test_parse_step(self):
        self.assertEqual(parse_step("11/18 job"), {"i": 11, "n": 18, "unit": "job"})
        self.assertEqual(parse_step(" 2 / 6 trial"), {"i": 2, "n": 6, "unit": "trial"})
        self.assertEqual(parse_step("3/8")["unit"], "job")
        self.assertIsNone(parse_step("9/8"))
        self.assertIsNone(parse_step("0/8"))
        self.assertIsNone(parse_step(None))

    def test_items_from_args(self):
        args = "python bt.py --folds a b c --model ridge".split()
        self.assertEqual(items_from_args(args, "--folds"), ["a", "b", "c"])
        self.assertEqual(items_from_args(args, "--missing"), [])

    def test_fmt(self):
        self.assertEqual([fmt(12), fmt(420), fmt(3900)], ["12s", "7m", "1h05m"])


class LogScanTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "run.log"

    def tearDown(self):
        self.dir.cleanup()

    def write(self, text):
        with open(self.log, "a") as f:
            f.write(text)

    def test_only_the_jobs_own_items_count(self):
        # An earlier job wrote other folds to the same log.
        self.write("=== fold x1\n=== fold x2\n=== fold x3\n")
        scan = LogScan(str(self.log), RULE)
        scan.update(now=100.0)
        self.write("=== fold a\n")
        scan.update(now=110.0)
        obs = item_observation(scan, ["a", "b"], "fold", running=True)
        self.assertEqual((obs["done"], obs["total"], obs["item"], obs["at"]), (0, 2, "a", 110.0))

    def test_old_lines_have_no_time_but_a_last_marker_has_the_file_time(self):
        self.write("=== fold a\nsome output\n=== fold b\n")
        scan = LogScan(str(self.log), RULE)
        scan.update(now=500.0)
        self.assertIsNone(scan.markers[0]["seen"])
        self.assertAlmostEqual(scan.markers[1]["seen"], self.log.stat().st_mtime)

    def test_skip_lines_count_as_done(self):
        self.write("skip fold a\n")
        scan = LogScan(str(self.log), RULE)
        scan.update(now=1.0)
        obs = item_observation(scan, ["a", "b"], "fold", running=True)
        self.assertEqual((obs["done"], obs["item"]), (1, None))

    def test_waiting_before_the_first_item(self):
        self.write("building the feature panel ...\n")
        scan = LogScan(str(self.log), RULE)
        scan.update(now=1.0)
        self.assertTrue(item_observation(scan, ["a"], "fold", running=True)["waiting"])
        self.assertIsNone(item_observation(scan, ["a"], "fold", running=False))

    def test_the_last_count_line_counts_not_the_last_line(self):
        self.write("Epoch [3/10]\nloss=0.41\nloss=0.39\n")
        scan = LogScan(str(self.log))
        scan.update(now=1.0)
        self.assertEqual(scan.last_line, "loss=0.39")
        self.assertEqual(line_observation(scan.count_line)["done"], 2)

    def test_carriage_return_keeps_the_last_bar_state(self):
        self.write("\r 10%|#   | 10/100\r 20%|##  | 20/100")
        scan = LogScan(str(self.log))
        scan.update(now=1.0)
        self.assertEqual(line_observation(scan.count_line)["done"], 20)


class EstimatorTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "durations.json"

    def tearDown(self):
        self.dir.cleanup()

    def obs(self, done, at=None, total=4):
        return {"done": done, "total": total, "unit": "fold", "at": at}

    def test_learns_the_time_per_unit_and_saves_it(self):
        est = Estimator("bt.py", Durations(self.path), started=0.0)
        est.update(self.obs(0, at=10.0), now=10.0)
        est.update(self.obs(1, at=40.0), now=40.0)
        est.update(self.obs(2, at=70.0), now=70.0)
        p = est.estimate(self.obs(2), now=85.0, running=True)
        # 2 folds done, fold 3 is half done (15 s of ~30 s).
        self.assertAlmostEqual(p["pct"], (2 + 0.5) / 4)
        self.assertAlmostEqual(p["eta"], 30 * 2 - 15)
        self.assertIn("fold 3 of 4", p["label"])
        self.assertEqual(json.loads(self.path.read_text())["bt.py|fold"], [30.0, 30.0])

    def test_a_new_job_uses_the_saved_time(self):
        d = Durations(self.path)
        d.add("bt.py", "fold", 60)
        est = Estimator("bt.py", Durations(self.path), started=0.0)
        est.update(self.obs(0, at=5.0), now=5.0)
        p = est.estimate(self.obs(0), now=35.0, running=True)
        self.assertAlmostEqual(p["pct"], 0.5 / 4)
        self.assertAlmostEqual(p["eta"], 60 * 4 - 30)

    def test_a_slow_unit_does_not_give_a_negative_time_left(self):
        d = Durations(self.path)
        d.add("bt.py", "fold", 60)
        est = Estimator("bt.py", d, started=0.0)
        est.update(self.obs(2, at=0.0), now=0.0)
        p = est.estimate(self.obs(2), now=200.0, running=True)
        self.assertAlmostEqual(p["eta"], 60)  # at least the last fold
        self.assertLess(p["pct"], 1.0)

    def test_no_time_left_when_the_unit_start_is_unknown(self):
        d = Durations(self.path)
        d.add("bt.py", "fold", 60)
        est = Estimator("bt.py", d, started=0.0)
        est.update(self.obs(1, at=None), now=100.0)  # first sample, old log
        self.assertIsNone(est.estimate(self.obs(1), now=130.0, running=True)["eta"])

    def test_short_or_crashed_units_are_not_saved(self):
        est = Estimator("bt.py", Durations(self.path), started=0.0)
        est.update(self.obs(0, at=0.0), now=0.0)
        est.update(self.obs(1, at=2.0), now=2.0)
        self.assertFalse(self.path.exists())

    def test_overall_from_the_step_variable(self):
        d = Durations(self.path)
        d.add("trial.py", "job", 100)
        est = Estimator("trial.py", d, started=0.0)
        o = est.overall({"i": 3, "n": 5, "unit": "trial"}, {"pct": 0.5, "eta": 50.0}, now=50.0)
        self.assertAlmostEqual(o["pct"], 2.5 / 5)
        self.assertAlmostEqual(o["eta"], 50 + 2 * 100)
        self.assertEqual(o["label"], "trial 3 of 5")

    def test_progress_file(self):
        f = Path(self.dir.name) / "p.json"
        f.write_text(json.dumps({"done": 2, "total": 6, "unit": "epoch", "stage": "validate",
                                 "done_at": 50.0, "started": 3.0}))
        obs = file_observation(str(f))
        self.assertEqual((obs["done"], obs["total"], obs["unit"], obs["stage"], obs["at"]),
                         (2, 6, "epoch", "validate", 50.0))
        self.assertIsNone(file_observation(str(Path(self.dir.name) / "missing.json")))


if __name__ == "__main__":
    unittest.main()
