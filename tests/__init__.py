import os

# Code-review-mode tests set CLAUDECODE explicitly with unittest.mock.patch.dict.
os.environ.pop("CLAUDECODE", None)
