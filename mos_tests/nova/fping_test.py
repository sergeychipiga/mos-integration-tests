#    Copyright 2015 Mirantis, Inc.
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
import pytest

from six.moves import configparser

from mos_tests.functions import common as common_functions
from mos_tests.neutron.python_tests.base import TestBase


logger = logging.getLogger(__name__)


@pytest.yield_fixture
def instances_on_diff_computes(
        request, os_conn, security_group, keypair):
    """Create instances (4 by default) on 2 compute nodes at
    'admin_internal_net' and associate floating IP to each VM.
    """
    limit_computes = 2  # Limit computes usage. For e.g. use only 2 from all
    zone = os_conn.nova.availability_zones.find(zoneName="nova")
    compute_hosts = zone.hosts.keys()[:limit_computes]
    param = getattr(request, 'param', {'count': 4})

    # Get ID of admin_internal_net
    nets = os_conn.neutron.list_networks()['networks']
    netid = [net['id'] for net in nets if not net['router:external'] and
             net['name'] == 'admin_internal_net'][0]

    instances = []
    for i in range(param['count']):
        compute = compute_hosts.pop(0)
        compute_hosts.append(compute)  # add back in list pop-ed value
        # create instances
        instance = os_conn.create_server(
            name='server%02d' % i,
            availability_zone='{}:{}'.format(zone.zoneName, compute),
            key_name=keypair.name,
            nics=[{'net-id': netid}],
            security_groups=[security_group.id],
            wait_for_active=False,
            wait_for_avaliable=False)
        instances.append(instance)
    common_functions.wait(
        lambda: all(os_conn.is_server_active(x) for x in instances),
        timeout_seconds=2 * 60,
        waiting_for='instances to became to ACTIVE status')
    common_functions.wait(
        lambda: all(os_conn.is_server_ssh_ready(x) for x in instances),
        timeout_seconds=2 * 60,
        waiting_for='instances to be ssh available')
    # add floating IP to each instance
    floating_ips = []
    for instance in instances:
        floating_ip = os_conn.nova.floating_ips.create()
        floating_ips.append(floating_ip)
        instance.add_floating_ip(floating_ip.ip)
    yield instances
    if 'undestructive' in request.node.keywords:
        for instance in instances:
            instance.force_delete()
        common_functions.wait(
            lambda: all(os_conn.is_server_deleted(x.id) for x in instances),
            timeout_seconds=60,
            waiting_for='instances to be deleted')
        for fip in floating_ips:
            os_conn.delete_floating_ip(fip)


@pytest.yield_fixture
def install_fping_on_controllers(env):
    """Install fping and configure nova.cfg on controllers"""

    def restart_nova_service():
        for controller in controllers:
            with controller.ssh() as remote:
                remote.check_call(nova_restart_ctrllr)
        # wait for nova ready
        common_functions.wait(
            env.os_conn.is_nova_ready,
            timeout_seconds=60 * 2,
            expected_exceptions=Exception,
            waiting_for="OpenStack nova computes are ready")

    nova_cfg_f = '/etc/nova/nova.conf'
    nova_restart_ctrllr = 'service nova-api restart'
    need_nova_restart = False
    controllers = env.get_nodes_by_role('controller')
    # Command to backup nova.conf file
    backup = 'cp {0} {0}_backup'.format(nova_cfg_f)

    for controller in controllers:
        with controller.ssh() as remote:
            # check if fping installed. If not - install.
            if not remote.execute('which fping').is_ok:
                cmd = 'apt-get update && apt-get install fping -y'
                remote.check_call(cmd, verbose=False)
            # check fping install path is the same as in nova.cfg
            cmd = "whereis fping | awk '{print $2}'"
            fp_install_path = remote.check_call(cmd)['stdout'][0].strip()
            with remote.open(nova_cfg_f, 'r') as f:
                parser = configparser.RawConfigParser()
                parser.readfp(f)
                fp_novacfg_path = parser.get('DEFAULT', 'fping_path')
            # update nova.cfg if paths are not equal
            if fp_install_path != fp_novacfg_path:
                need_nova_restart = True
                # take backup of nova.cfg
                remote.check_call(backup)
                # write changes to nova.cfg
                parser.set('DEFAULT', 'fping_path', fp_install_path)
                with remote.open(nova_cfg_f, 'w') as new_f:
                    parser.write(new_f)
    # restart controllers if nova.cfg was changed
    if need_nova_restart:
        restart_nova_service()
    yield
    # revert original file if it was changed before
    if need_nova_restart:
        logger.debug('Revert changes of nova.conf back')
        for controller in controllers:
            with controller.ssh() as remote:
                cmd = 'mv {0}_backup {0}'.format(nova_cfg_f)
                remote.check_call(cmd)
        # restart services
        restart_nova_service()


@pytest.mark.undestructive
@pytest.mark.check_env_('has_2_or_more_computes')
class TestNovaOSfpingExtension(TestBase):
    """Tests OS-fping Nova extension"""

    def fping_server_status(self, fpinglist):
        """Links fping alive (True/False) status with server's id.
        Returns like:
        .    {u'1e4587fe-da2c-4cff-9438-40e5f775d4aa': False,
        .    u'b46df02e-f72c-412d-8c0e-4c74fbdc5252': True}
        """
        return {fping.id: fping.alive for fping in fpinglist}

    @pytest.mark.testrail_id('842501')
    def test_ping_all_instances_in_tenant(
            self, install_fping_on_controllers, instances_on_diff_computes):
        """Ping all instances in a tenant with the use of fping utility
        Actions:
        1. Install pfing on all controllers and update 'nova.conf' if required.
        2. Create net and subnet;
        3. Create and run four instances (vm0-vm3) inside same net, but 2 vms
        should be on one compute, rest 2 - on another;
        4. Stop 2 vms from different computes and wait for SHUTOFF state;
        5. Run fping;
        6. Check that 2 vms are alive according fping and 2 vms are not alive;
        7. Start 2 stopped vms and check that all VMs are alive in fping.
        """
        timeout = 60  # (sec) timeout to wait instance for status change

        # Create 4 instances on 2 different computes.
        # Compute2: vm0,2 | Compute1: vm1,3
        vm0, vm1, vm2, vm3, = instances_on_diff_computes
        vms_for_operations = [vm0, vm1]

        # Stop VMs with different computes: vm0(compute1) and vm1(compute2)
        for vm in vms_for_operations:
            vm.stop()

        # Wait for servers power off
        common_functions.wait(
            lambda: all(self.os_conn.server_status_is(x, 'SHUTOFF')
                        for x in vms_for_operations),
            timeout_seconds=timeout, sleep_seconds=5,
            waiting_for='instances to change status to SHUTOFF')

        # Get fping results
        fping_list = self.os_conn.nova.fping.list()
        fping_serv_result = self.fping_server_status(fping_list)

        # Check stopped servers not alive in fping
        assert (fping_serv_result[vm0.id] is False and
                fping_serv_result[vm1.id] is False)
        # Check alive servers are alive in fping
        assert (fping_serv_result[vm2.id] is True and
                fping_serv_result[vm3.id] is True)

        # Start stopped VMs
        for vm in vms_for_operations:
            vm.start()

        # Wait for servers became alive and ssh available
        common_functions.wait(
            lambda: all(self.os_conn.is_server_ssh_ready(x)
                        for x in vms_for_operations),
            timeout_seconds=timeout, sleep_seconds=5,
            waiting_for='SSH became available on instances')

        # Update fping results
        fping_list = self.os_conn.nova.fping.list()
        fping_serv_result = self.fping_server_status(fping_list)

        # Check all VMs are alive in fping
        assert all(fping_serv_result[x.id] for x in instances_on_diff_computes)