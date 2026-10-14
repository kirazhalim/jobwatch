import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jobwatch import config, progress, server


class ProgressClientTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        progress._state.clear()

    def tearDown(self):
        self.dir.cleanup()

    def test_nothing_happens_without_the_variable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            progress.update(1, 3)
        self.assertEqual(os.listdir(self.dir.name), [])

    def test_update_and_stage(self):
        path = Path(self.dir.name) / "p.json"
        with mock.patch.dict(os.environ, {"JOBWATCH_PROGRESS": str(path)}):
            progress.update(1, 3, unit="epoch", stage="train")
            progress.stage("validate")
        d = json.loads(path.read_text())
        self.assertEqual((d["done"], d["total"], d["unit"], d["stage"]), (1, 3, "epoch", "validate"))
        self.assertLessEqual(d["started"], d["done_at"])

    def test_a_write_error_does_not_stop_the_job(self):
        bad = str(Path(self.dir.name) / "missing" / "p.json")
        with mock.patch.dict(os.environ, {"JOBWATCH_PROGRESS": bad}):
            progress.update(0, 1)


class DescribeTests(unittest.TestCase):
    def job(self, cmd, cwd="/Users/a/myrepo-wt-lr-sweep"):
        return {"pid": 1, "cmd": cmd, "name": server.short_name(cmd), "cwd": cwd,
                "stdout": None, "parent_cmd": None}

    def describe(self, job, env=None, **cfg):
        with mock.patch("jobwatch.system.read_env", return_value=env or {}):
            return server.describe(job, {**config.DEFAULTS, **cfg})

    def test_short_name(self):
        self.assertEqual(server.short_name("python -m pkg.train --lr 1"), "pkg.train")
        self.assertEqual(server.short_name("python /x/train.py --lr 1"), "train.py")

    def test_default_name_comes_from_the_run_options(self):
        info = self.describe(self.job("python train.py --config configs/resnet50.yaml"))
        self.assertEqual(info["alias"], "resnet50")
        self.assertEqual(info["key"], "train.py")

    def test_tags_folder_prefixes_and_key_args(self):
        info = self.describe(
            self.job("python train.py --outdir run7 --model vit --ticket PROJ-42"),
            tags=[{"regex": "(?i)proj-(\\d+)", "format": "PROJ-{0}"}],
            folder_prefixes=["myrepo-wt-"], key_args=["--model"])
        self.assertEqual(info["alias"], "PROJ-42 · run7")
        self.assertEqual(info["key"], "train.py|--model=vit")
        info = self.describe(self.job("python train.py --outdir run7"), folder_prefixes=["myrepo-wt-"])
        self.assertEqual(info["alias"], "lr-sweep · run7")

    def test_environment_and_item_rule(self):
        rule = {"arg": "--folds", "start": "^=== fold (?P<item>\\S+)", "unit": "fold"}
        info = self.describe(self.job("python bt.py --folds a b c"),
                             env={"JOBWATCH_DESC": "Backtest", "JOBWATCH_STEP": "2/4"},
                             item_rules=[rule])
        self.assertEqual(info["desc"], "Backtest")
        self.assertEqual(info["step"], {"i": 2, "n": 4, "unit": "job"})
        self.assertEqual(info["items"], ["a", "b", "c"])

    def test_script_note(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "run_all.sh"
            script.write_text("#!/usr/bin/env bash\n# Learning-rate sweep (see notes.md).\nset -e\n")
            self.assertEqual(server.script_note(f"bash {script}", d), "Learning-rate sweep")
            self.assertEqual(server.script_note("bash run_all.sh", d), "Learning-rate sweep")


if __name__ == "__main__":
    unittest.main()
