import unittest

from jobwatch import system


class ParseTests(unittest.TestCase):
    def test_etime(self):
        self.assertEqual(system.parse_etime("05"), 5)
        self.assertEqual(system.parse_etime("02:03"), 123)
        self.assertEqual(system.parse_etime("01:02:03"), 3723)
        self.assertEqual(system.parse_etime("2-01:00:00"), 2 * 86400 + 3600)

    def test_ps_keeps_only_own_processes(self):
        text = ("  101     1   501    10:00  204800   350.5 /usr/bin/python3 train.py --lr 3e-4\n"
                "  102     1     0    01:00    1024     0.0 /usr/sbin/root-daemon\n")
        procs = system.parse_ps(text, uid=501)
        self.assertEqual(list(procs), [101])
        p = procs[101]
        self.assertEqual((p["ppid"], p["etime"], p["rss_kb"], p["cpu"]), (1, 600, 204800, 350.5))
        self.assertEqual(p["cmd"], "/usr/bin/python3 train.py --lr 3e-4")

    def test_is_python(self):
        self.assertTrue(system.is_python("/opt/homebrew/bin/python3.12 x.py"))
        self.assertTrue(system.is_python("/Library/.../Resources/Python.app/Contents/MacOS/Python x.py"))
        self.assertTrue(system.is_python("/venv/bin/pytest -q"))
        self.assertFalse(system.is_python("/Applications/Visual Studio Code.app/Contents/MacOS/Electron"))
        self.assertFalse(system.is_python("caffeinate -i python3 x.py"))

    def test_is_jobwatch(self):
        self.assertTrue(system.is_jobwatch("python3 -m jobwatch serve --port 8765"))
        self.assertTrue(system.is_jobwatch("python3 -m jobwatch _supervise /tmp/x.json"))
        self.assertTrue(system.is_jobwatch("/usr/bin/python3 /Users/a/.local/bin/jobwatch serve"))
        self.assertFalse(system.is_jobwatch("python3 /src/jobwatch/demo/finetune.py"))
        self.assertFalse(system.is_jobwatch("python3 -m jobwatchers.train"))

    def test_lsof(self):
        text = "p42\nfcwd\ntDIR\nn/Users/a/project\nf1\ntREG\nn/Users/a/run.log\nf2\ntCHR\nn/dev/ttys003\n"
        info = system.parse_lsof(text)
        self.assertEqual(info["cwd"], "/Users/a/project")
        self.assertEqual(info["stdout"], "/Users/a/run.log")
        self.assertIsNone(info["stderr"])
        self.assertEqual(info["stderr_kind"], "CHR")

    def test_vm_stat(self):
        text = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                "Pages free:                               1000.\n"
                "Pages active:                             2000.\n"
                "Pages wired down:                         500.\n"
                "Pages occupied by compressor:             100.\n")
        self.assertEqual(system.parse_vm_stat(text, total=1)["used"], 2600 * 16384)

    def test_env_values_with_spaces(self):
        text = ("python3 train.py --lr 3e-4 TERM=xterm JOBWATCH_DESC=ResNet-50 · run 3 · lr=3e-4 "
                "JOBWATCH_STEP=3/8 trial HOME=/Users/a")
        env = system.parse_env(text, ("JOBWATCH_DESC", "JOBWATCH_STEP", "JOBWATCH_MISSING"))
        self.assertEqual(env, {"JOBWATCH_DESC": "ResNet-50 · run 3 · lr=3e-4",
                               "JOBWATCH_STEP": "3/8 trial"})


if __name__ == "__main__":
    unittest.main()
