# This file is part of the sos project: https://github.com/sosreport/sos
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# version 2 of the GNU General Public License.
#
# See the LICENSE file in the source distribution for further information.

from sos_tests import StageTwoReportTest


class PluginTimeoutTest(StageTwoReportTest):
    """Test that whole plugin timeout control is working

    :avocado: tags=stagetwo
    """

    install_plugins = ['timeout_test']
    sos_cmd = '-o timeout_test -vvv --plugin-timeout=10'

    def test_plugin_timed_out(self):
        self.assertSosLogNotContains('collected plugin \'timeout_test\' in')
        self.assertSosUILogContains('Plugin timeout_test timed out')

    def test_no_output_collected(self):
        self.assertFileNotExists('sos_commands/timeout_test/echo_out')


class NativeCmdTimeoutTest(StageTwoReportTest):
    """Test that the native timeout control for the plugin API is working

    :avocado: tags=stagetwo
    """

    install_plugins = ['timeout_test']
    sos_cmd = '-o timeout_test -vvv'

    def test_plugin_completed(self):
        self.assertSosLogContains('collected plugin \'timeout_test\' in')
        self.assertFileCollected('sos_commands/timeout_test/echo_good')

    def test_command_timed_out(self):
        self.assertSosLogContains(r"\[plugin:timeout_test\] command 'sleep 30' timed out after 10s")
        self.assertFileCollected('sos_commands/timeout_test/sleep_30')
