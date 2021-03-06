# Copyright 2017 the V8 project authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


import itertools
import json
import optparse
import os
import sys


# Add testrunner to the path.
sys.path.insert(
  0,
  os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))


from local import utils


BASE_DIR = (
    os.path.dirname(
      os.path.dirname(
        os.path.dirname(
          os.path.abspath(__file__)))))

DEFAULT_OUT_GN = 'out.gn'

ARCH_GUESS = utils.DefaultArch()

DEBUG_FLAGS = ["--nohard-abort", "--enable-slow-asserts", "--verify-heap"]
RELEASE_FLAGS = ["--nohard-abort"]
MODES = {
  "debug": {
    "flags": DEBUG_FLAGS,
    "timeout_scalefactor": 4,
    "status_mode": "debug",
    "execution_mode": "debug",
    "output_folder": "debug",
  },
  "optdebug": {
    "flags": DEBUG_FLAGS,
    "timeout_scalefactor": 4,
    "status_mode": "debug",
    "execution_mode": "debug",
    "output_folder": "optdebug",
  },
  "release": {
    "flags": RELEASE_FLAGS,
    "timeout_scalefactor": 1,
    "status_mode": "release",
    "execution_mode": "release",
    "output_folder": "release",
  },
  # Normal trybot release configuration. There, dchecks are always on which
  # implies debug is set. Hence, the status file needs to assume debug-like
  # behavior/timeouts.
  "tryrelease": {
    "flags": RELEASE_FLAGS,
    "timeout_scalefactor": 1,
    "status_mode": "debug",
    "execution_mode": "release",
    "output_folder": "release",
  },
  # This mode requires v8 to be compiled with dchecks and slow dchecks.
  "slowrelease": {
    "flags": RELEASE_FLAGS + ["--enable-slow-asserts"],
    "timeout_scalefactor": 2,
    "status_mode": "debug",
    "execution_mode": "release",
    "output_folder": "release",
  },
}

SUPPORTED_ARCHS = [
  "android_arm",
  "android_arm64",
  "android_ia32",
  "android_x64",
  "arm",
  "ia32",
  "mips",
  "mipsel",
  "mips64",
  "mips64el",
  "s390",
  "s390x",
  "ppc",
  "ppc64",
  "x64",
  "x32",
  "arm64",
]

# Map of test name synonyms to lists of test suites. Should be ordered by
# expected runtimes (suites with slow test cases first). These groups are
# invoked in separate steps on the bots.
TEST_MAP = {
  # This needs to stay in sync with test/bot_default.isolate.
  "bot_default": [
    "debugger",
    "mjsunit",
    "cctest",
    "wasm-spec-tests",
    "inspector",
    "webkit",
    "mkgrokdump",
    "fuzzer",
    "message",
    "preparser",
    "intl",
    "unittests",
  ],
  # This needs to stay in sync with test/default.isolate.
  "default": [
    "debugger",
    "mjsunit",
    "cctest",
    "wasm-spec-tests",
    "inspector",
    "mkgrokdump",
    "fuzzer",
    "message",
    "preparser",
    "intl",
    "unittests",
  ],
  # This needs to stay in sync with test/optimize_for_size.isolate.
  "optimize_for_size": [
    "debugger",
    "mjsunit",
    "cctest",
    "inspector",
    "webkit",
    "intl",
  ],
  "unittests": [
    "unittests",
  ],
}


class TestRunnerError(Exception):
  pass


class BaseTestRunner(object):
  def __init__(self):
    self.outdir = None

    self.arch = None
    self.arch_and_mode = None
    self.mode = None

    self.auto_detect = None

  def execute(self):
    try:
      options, args = self._parse_args()
      return self._do_execute(options, args)
    except TestRunnerError:
      return 1

  def _parse_args(self):
    parser = optparse.OptionParser()
    parser.usage = '%prog [options] [tests]'
    parser.description = """TESTS: %s""" % (TEST_MAP["default"])
    self._add_parser_default_options(parser)
    self._add_parser_options(parser)
    options, args = parser.parse_args()
    try:
      self._process_default_options(options)
      self._process_options(options)
    except TestRunnerError:
      parser.print_help()
      raise

    return options, args

  def _add_parser_default_options(self, parser):
    parser.add_option("--gn", help="Scan out.gn for the last built"
                      " configuration",
                      default=False, action="store_true")
    parser.add_option("--outdir", help="Base directory with compile output",
                      default="out")
    parser.add_option("--buildbot",
                      help="Adapt to path structure used on buildbots",
                      default=False, action="store_true")
    parser.add_option("--arch",
                      help=("The architecture to run tests for, "
                            "'auto' or 'native' for auto-detect: %s" %
                            SUPPORTED_ARCHS))
    parser.add_option("--arch-and-mode",
                      help="Architecture and mode in the format 'arch.mode'")
    parser.add_option("-m", "--mode",
                      help="The test modes in which to run (comma-separated,"
                      " uppercase for ninja and buildbot builds): %s"
                      % MODES.keys())

  def _add_parser_options(self, parser):
    pass

  def _process_default_options(self, options):
    # Try to autodetect configuration based on the build if GN was used.
    # This can't be ovveridden by cmd-line arguments.
    if options.gn:
      outdir = self._get_gn_outdir()
    else:
      outdir = options.outdir

    self.auto_detect = self._read_build_config(outdir, options)
    if not self.auto_detect:
      self.arch = options.arch or 'ia32,x64,arm'
      self.mode = options.mode or 'release,debug'
      self.outdir = outdir
      if options.arch_and_mode:
        self.arch_and_mode = map(lambda am: am.split('.'),
                                 options.arch_and_mode.split(','))
        self.arch = ','.join(map(lambda am: am[0], self.arch_and_mode))
        self.mode = ','.join(map(lambda am: am[1], self.arch_and_mode))

    self.mode = self.mode.split(',')
    for mode in self.mode:
      if not self._buildbot_to_v8_mode(mode) in MODES:
        print "Unknown mode %s" % mode
        raise TestRunnerError()

    if self.arch in ["auto", "native"]:
      self.arch = ARCH_GUESS
    self.arch = self.arch.split(",")
    for arch in self.arch:
      if not arch in SUPPORTED_ARCHS:
        print "Unknown architecture %s" % arch
        raise TestRunnerError()

    # Store the final configuration in arch_and_mode list. Don't overwrite
    # predefined arch_and_mode since it is more expressive than arch and mode.
    if not self.arch_and_mode:
      self.arch_and_mode = itertools.product(self.arch, self.mode)

  def _get_gn_outdir(self):
    gn_out_dir = os.path.join(BASE_DIR, DEFAULT_OUT_GN)
    latest_timestamp = -1
    latest_config = None
    for gn_config in os.listdir(gn_out_dir):
      gn_config_dir = os.path.join(gn_out_dir, gn_config)
      if not os.path.isdir(gn_config_dir):
        continue
      if os.path.getmtime(gn_config_dir) > latest_timestamp:
        latest_timestamp = os.path.getmtime(gn_config_dir)
        latest_config = gn_config
    if latest_config:
      print(">>> Latest GN build found: %s" % latest_config)
      return os.path.join(DEFAULT_OUT_GN, latest_config)

  # Auto-detect test configurations based on the build (GN only).
  # sets:
  #   - arch
  #   - arch_and_mode
  #   - mode
  #   - outdir
  def _read_build_config(self, outdir, options):
    if options.buildbot:
      build_config_path = os.path.join(
        BASE_DIR, outdir, options.mode, "v8_build_config.json")
    else:
      build_config_path = os.path.join(
        BASE_DIR, outdir, "v8_build_config.json")

    if not os.path.exists(build_config_path):
      return False

    with open(build_config_path) as f:
      try:
        build_config = json.load(f)
      except Exception:
        print("%s exists but contains invalid json. Is your build up-to-date?"
              % build_config_path)
        raise TestRunnerError()

    # In auto-detect mode the outdir is always where we found the build
    # config.
    # This ensures that we'll also take the build products from there.
    self.outdir = os.path.dirname(build_config_path)
    self.arch_and_mode = None

    # In V8 land, GN's x86 is called ia32.
    if build_config["v8_target_cpu"] == "x86":
      build_config["v8_target_cpu"] = "ia32"

    if options.mode:
      # In auto-detect mode we don't use the mode for more path-magic.
      # Therefore transform the buildbot mode here to fit to the GN build
      # config.
      options.mode = self._buildbot_to_v8_mode(options.mode)

    # TODO(majeski): merge next two loops and put everything in self.

    # Get options from the build config. Sanity check that we're not trying to
    # use inconsistent options.
    for param, value in (
      ('arch', build_config["v8_target_cpu"]),
      ('mode', 'debug' if build_config["is_debug"] else 'release'),
    ):
      cmd_line_value = getattr(options, param)
      if (cmd_line_value not in [None, True, False] and
          cmd_line_value != value):
        # TODO(machenbach): This is for string options only. Requires
        # options  to not have default values. We should make this more
        # modular and implement it in our own version of the option parser.
        print("Attempted to set %s to %s, while build is %s." %
              (param, cmd_line_value, value))
        raise TestRunnerError()
      if cmd_line_value == True and value == False:
        print("Attempted to turn on %s, but it's not available." % param)
        raise TestRunnerError()
      if cmd_line_value != value:
        print(">>> Auto-detected %s=%s" % (param, value))
      setattr(self, param, value)

    # Update options based on the build config. Sanity check that we're not
    # trying to use inconsistent options.
    for param, value in (
      ('asan', build_config["is_asan"]),
      ('dcheck_always_on', build_config["dcheck_always_on"]),
      ('gcov_coverage', build_config["is_gcov_coverage"]),
      ('msan', build_config["is_msan"]),
      ('no_i18n', not build_config["v8_enable_i18n_support"]),
      ('no_snap', not build_config["v8_use_snapshot"]),
      ('tsan', build_config["is_tsan"]),
      ('ubsan_vptr', build_config["is_ubsan_vptr"]),
    ):
      cmd_line_value = getattr(options, param)
      if (cmd_line_value not in [None, True, False] and
          cmd_line_value != value):
        # TODO(machenbach): This is for string options only. Requires
        # options  to not have default values. We should make this more
        # modular and implement it in our own version of the option parser.
        print("Attempted to set %s to %s, while build is %s." %
              (param, cmd_line_value, value))
        raise TestRunnerError()
      if cmd_line_value == True and value == False:
        print("Attempted to turn on %s, but it's not available." % param)
        raise TestRunnerError()
      if cmd_line_value != value:
        print(">>> Auto-detected %s=%s" % (param, value))
      setattr(options, param, value)

    return True

  def _buildbot_to_v8_mode(self, config):
    """Convert buildbot build configs to configs understood by the v8 runner.

    V8 configs are always lower case and without the additional _x64 suffix
    for 64 bit builds on windows with ninja.
    """
    mode = config[:-4] if config.endswith('_x64') else config
    return mode.lower()

  def _process_options(self, options):
    pass

  # TODO(majeski): remove options & args parameters
  def _do_execute(self, options, args):
    raise NotImplementedError()
