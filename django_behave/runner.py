"""Django test runner which uses behave for BDD tests.
"""

import unittest
from optparse import make_option
from os.path import dirname, abspath, basename, join, isdir
import weakref

from django.test.simple import DjangoTestSuiteRunner
from django.test import LiveServerTestCase
from django.db.models import get_app

from behave.configuration import Configuration, ConfigError, options
from behave.runner import Runner
from behave.parser import ParserError
from behave.formatter.ansi_escapes import escapes

import sys


def get_app_dir(app_module):
    app_dir = dirname(app_module.__file__)
    if basename(app_dir) == 'models':
        app_dir = abspath(join(app_dir, '..'))
    return app_dir


def get_features(app_module):
    app_dir = get_app_dir(app_module)
    features_dir = abspath(join(app_dir, 'features'))
    if isdir(features_dir):
        return features_dir
    else:
        return None


# Get Behave command line options and add our own
def get_options():
    option_list = (
        make_option("--behave_browser",
            action="store",
            dest="browser",
            help="Specify the browser to use for testing",
        ),
        make_option("--behave_feature",
            action="store",
            dest="features_dir",
            default=None,
            help="Specify the feature file or directory to run",
        )
    )

    option_info = {}

    for fixed, keywords in options:
        # Look for the long version of this option
        long_option = None
        for option in fixed:
            if option.startswith("--"):
                long_option = option
                break

        # Only deal with those options that have a long version
        # DIRTY HACK
        if long_option and long_option != "--logging-level":
            # Remove 'config_help' as that's not a valid optparse keyword
            if keywords.has_key("config_help"):
                keywords.pop("config_help")

            name = "--behave_" + long_option[2:]

            option_list = option_list + \
                (make_option(name, **keywords),)

            # Need to store a little info about the Behave option so that we
            # can deal with it later.  'has_arg' refers to if the option has
            # an argument.  A boolean option, for example, would NOT have an
            # argument.
            action = keywords.get("action", "store")
            if action == "store" or action == "append":
                has_arg = True
            else:
                has_arg = False

            option_info.update({name: has_arg})

    return (option_list, option_info)


# Parse options that came in.  Deal with ours, create an ARGV for Behave with
# it's options
def parse_argv(argv, option_info):
    new_argv = ["behave",]
    our_opts = {"browser": None}

    for index in xrange(len(argv)):
        if argv[index].startswith("--"):
            if argv[index] == "--behave_browser":
                our_opts["browser"] = argv[index + 1]
                index += 1  # Skip past browser option arg
            elif argv[index] == "--behave_feature":
                our_opts["feature"] = argv[index + 1]
                index += 1
            elif argv[index] == "--behave_wip":
                new_argv.append("-w")
                index += 1
            else:
                # Convert to Behave option
                new_argv.append("--" + argv[index][9:])

                # Add option argument if there is one
                if option_info[argv[index]] == True:
                    new_argv.append(argv[index+1])
                    index += 1  # Skip past option arg

    return (new_argv, our_opts)


class DjangoBehaveTestCase(LiveServerTestCase):
    def __init__(self, **kwargs):
        self.features_dir = kwargs.pop('features_dir')
        self.option_info = kwargs.pop('option_info')
        super(DjangoBehaveTestCase, self).__init__(**kwargs)

    def get_features_dir(self):
        if isinstance(self.features_dir, basestring):
            return [self.features_dir]
        return self.features_dir

    def setUp(self):
        self.setupBehave()

    def setupBehave(self):
        # Create a sys.argv suitable for Behave to parse
        old_argv = sys.argv
        (sys.argv, our_opts) = parse_argv(old_argv, self.option_info)
        self.behave_config = Configuration()
        sys.argv = old_argv
        self.behave_config.browser = our_opts["browser"]

        self.behave_config.server_url = self.live_server_url  # property of LiveServerTestCase
        if our_opts.get("feature", None):
            self.behave_config.paths = [abspath(our_opts["feature"])]
        else:
            self.behave_config.paths = self.get_features_dir()
        self.behave_config.format = ['pretty']
        # disable these in case you want to add set_trace in the tests you're developing
        self.behave_config.stdout_capture = False
        self.behave_config.stderr_capture = False

    def runTest(self, result=None):
        # run behave on a single directory

        # from behave/__main__.py
        #stream = self.behave_config.output
        runner = Runner(self.behave_config)
        runner.test_case = weakref.proxy(self)
        runner.undefined_steps = []
        try:
            failed = runner.run()
        except ParserError, e:
            sys.exit(str(e))
        except ConfigError, e:
            sys.exit(str(e))

        if self.behave_config.show_snippets and runner.undefined_steps:
            msg = u"\nYou can implement step definitions for undefined steps with "
            msg += u"these snippets:\n\n"
            printed = set()

            if sys.version_info[0] == 3:
                string_prefix = "('"
            else:
                string_prefix = u"(u'"

            for step in set(runner.undefined_steps):
                if step in printed:
                    continue
                printed.add(step)

                msg += u"@" + step.step_type + string_prefix + step.name + u"')\n"
                msg += u"def impl(context):\n"
                msg += u"    assert False\n\n"

            sys.stderr.write(escapes['undefined'] + msg + escapes['reset'])
            sys.stderr.flush()

        if failed:
            sys.exit(1)
        # end of from behave/__main__.py


class DjangoBehaveTestSuiteRunner(DjangoTestSuiteRunner):
    # Set up to accept all of Behave's command line options and our own.  In
    # order to NOT conflict with Django's test command, we'll start all options
    # with the prefix "--behave_" (we'll only do the long version of an option).
    (option_list, option_info) = get_options()

    def make_bdd_test_suite(self, features_dir):
        return DjangoBehaveTestCase(features_dir=features_dir, option_info=self.option_info)

    def build_suite(self, test_labels, extra_tests=None, **kwargs):
        suite = unittest.TestSuite()
        extra_tests = extra_tests or []
        #
        # Add BDD tests to the extra_tests
        #
        std_test_suite = super(DjangoBehaveTestSuiteRunner,self).build_suite(test_labels,**kwargs)
        suite.addTest(std_test_suite)

        #
        # Add BDD tests to it
        #

        # always get all features for given apps (for convenience)
        for label in test_labels:
            if '.' in label:
                print "Ignoring label with dot in: " % label
                continue
            app = get_app(label)

            # Check to see if a separate 'features' module exists,
            # parallel to the models module
            features_dir = get_features(app)
            if features_dir is not None:
                # build a test suite for this directory
                extra_tests.append(self.make_bdd_test_suite(features_dir))

        return super(DjangoBehaveTestSuiteRunner, self
                     ).build_suite(test_labels, extra_tests, **kwargs)
# eof:
