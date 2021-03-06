# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
sys.path.insert(0, '.')
import unittest

# Importing all of these modules causes all the relevant flags to get defined.
# They thus become overrideable, either with cmd line args to run_tests or via
# the test_flags file.
import tests.test_coords
import tests.test_dual_net
import tests.test_features
import tests.test_go
import tests.test_mcts
import tests.test_preprocessing
import tests.test_sgf_wrapper
import tests.test_shipname
import tests.test_strategies
import tests.test_symmetries
import tests.test_utils

from absl import flags

if __name__ == '__main__':
    # Parse test flags and initialize default flags
    flags.FLAGS(['ignore', '--flagfile=tests/test_flags'])

    if len(sys.argv) == 1:
        # Replicate the behavior of `python -m unittest discover tests`
        unittest.main(module=None, argv=['run_tests.py', 'discover'])
    elif len(sys.argv) >= 2:
        # Replicate the behavior of `python -m unittest tests`
        for arg in sys.argv[1:]:
            assert arg.startswith('test_') and '.' not in arg, arg
        unittest.main(module=None, argv=['run_tests.py'] + sys.argv[1:])
