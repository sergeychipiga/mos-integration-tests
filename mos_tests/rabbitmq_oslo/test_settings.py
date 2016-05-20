#    Copyright 2016 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import logging
import random

import pytest

from mos_tests.functions.common import wait


logger = logging.getLogger(__name__)


def num_of_rabbit_running_nodes(remote):
    """Get number of 'Started/Master' hosts from pacemaker.
    :param remote: SSH connection point to controller.
    """
    result = remote.execute('pcs status --full | '
                            'grep p_rabbitmq-server | '
                            'grep ocf | '
                            'grep -c -E "Master|Started"', verbose=False)
    count = result['stdout'][0].strip()
    if count.isdigit():
        return int(count)
    else:
        return 0


def wait_for_rabbit_running_nodes(remote, exp_nodes, timeout_min=5):
    """Waits until number of 'Started/Master' hosts from pacemaker
    will be as expected number of controllers.
    :param remote: SSH connection point to controller.
    :param exp_nodes: Expected number of rabbit nodes.
    :param timeout_min: Timeout in minutes to wait.
    """
    wait(lambda: num_of_rabbit_running_nodes(remote) == exp_nodes,
         timeout_seconds=60 * timeout_min,
         sleep_seconds=30,
         waiting_for='number of running nodes will be %s.' % exp_nodes)

# ----------------------------------------------------------------------------


@pytest.mark.undestructive
@pytest.mark.check_env_('is_ha', 'has_1_or_more_computes')
@pytest.mark.testrail_id('844786')
def test_disable_ha_for_rpc_queues_by_default(env):
    """Check that HA RPC is disabled by default.

    :param env: Environment

    Actions:
    1. Get launch parameters for p_rabbitmq-server from pacemaker;
    2. Check that 'enable_notifications_ha=true' and 'enable_rpc_ha=false';
    """
    controllers = env.get_nodes_by_role('controller')
    controller = random.choice(controllers)

    # Install tool on one controller and generate messages
    with controller.ssh() as remote:
        # wait when rabbit will be ok after snapshot revert
        wait_for_rabbit_running_nodes(remote, len(controllers))
        resp_pcs = remote.execute('pcs resource show '
                                  'p_rabbitmq-server')['stdout']
    assert (filter(
        lambda x: 'enable_notifications_ha=true' in x, resp_pcs) != [] and
            filter(lambda x: 'enable_notifications_ha=false' not
                             in x, resp_pcs) != []), (
        'Disabled HA notifications (should be enabled)')

    assert (filter(lambda x: 'enable_rpc_ha=false' in x, resp_pcs) != [] and
            filter(lambda x: 'enable_rpc_ha=true' not in x, resp_pcs) != []), (
        'Enabled HA RPC (should be disabled)')
