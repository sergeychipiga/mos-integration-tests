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

import csv
import logging
from random import randint
import re
import time

import pytest

from mos_tests.functions import common
from mos_tests.functions.common import wait
from mos_tests.functions import file_cache
from mos_tests.functions import network_checks
from mos_tests.neutron.python_tests.base import TestBase
from mos_tests import settings

logger = logging.getLogger(__name__)


@pytest.mark.check_env_("has_1_or_more_computes")
class OvsBase(TestBase):
    """Common functions for ovs tests"""

    def setup_rules_for_default_sec_group(self):
        """Add necessary rules to default security group."""
        default_sec_group = [
            group for group in self.os_conn.nova.security_groups.list()
            if group.name == "default"][0]

        self.os_conn.nova.security_group_rules.create(
            default_sec_group.id,
            ip_protocol='tcp',
            from_port=22,
            to_port=22,
            cidr='0.0.0.0/0')
        self.os_conn.nova.security_group_rules.create(
            default_sec_group.id,
            ip_protocol='icmp',
            from_port=-1,
            to_port=-1,
            cidr='0.0.0.0/0')
        self.os_conn.nova.security_group_rules.create(
            default_sec_group.id,
            ip_protocol='tcp',
            from_port=1,
            to_port=65535,
            cidr='0.0.0.0/0')
        self.os_conn.nova.security_group_rules.create(
            default_sec_group.id,
            ip_protocol='udp',
            from_port=1,
            to_port=65535,
            cidr='0.0.0.0/0')

    def get_current_cookie(self, compute):
        """Get the value of the cookie parameter for br-int or br-tun bridge.
            :param compute: Compute node where the server is scheduled
            :return: cookie value
        """
        cookies = {'br-int': set(), 'br-tun': set()}

        cookie_pattern = re.compile(r'cookie=[^,]+')
        with compute.ssh() as remote:
            result = remote.check_call('ovs-ofctl dump-flows br-int')
            cookies_list = cookie_pattern.findall(result.stdout_string)
            cookies['br-int'].update(cookies_list)

            result = remote.execute('ovs-ofctl dump-flows br-tun')
            if result.is_ok:
                cookies_list = cookie_pattern.findall(result.stdout_string)
                cookies['br-tun'].update(cookies_list)
        return cookies


@pytest.mark.check_env_("has_2_or_more_computes")
class TestOVSRestartTwoVms(OvsBase):
    """Check restarts of openvswitch-agents."""

    def _prepare_openstack(self):
        """Prepare OpenStack for scenarios run

        Steps:
            1. Update default security group
            2. Create router01, create networks net01: net01__subnet,
                192.168.1.0/24, net02: net02__subnet, 192.168.2.0/24 and
                attach them to router01.
            3. Launch vm1 in net01 network and vm2 in net02 network
                on different computes
            4. Go to vm1 console and send pings to vm2
        """
        self.instance_keypair = self.os_conn.create_key(key_name='instancekey')
        zone = self.os_conn.nova.availability_zones.find(zoneName="nova")
        vm_hosts = zone.hosts.keys()[:2]

        self.setup_rules_for_default_sec_group()

        # create router
        router = self.os_conn.create_router(name="router01")

        # create 2 networks and 2 instances
        for i, hostname in enumerate(vm_hosts, 1):
            net, subnet = self.create_internal_network_with_subnet(suffix=i)
            self.os_conn.router_interface_add(
                router_id=router['router']['id'],
                subnet_id=subnet['subnet']['id'])
            self.os_conn.create_server(
                name='server%02d' % i,
                availability_zone='{}:{}'.format(zone.zoneName, hostname),
                key_name=self.instance_keypair.name,
                nics=[{'net-id': net['network']['id']}])

        # check pings
        self.server1 = self.os_conn.nova.servers.find(name="server01")
        self.server2_ip = self.os_conn.get_nova_instance_ips(
            self.os_conn.nova.servers.find(name="server02")
        ).values()[0]

        network_checks.check_ping_from_vm(
            self.env, self.os_conn, self.server1, self.instance_keypair,
            self.server2_ip, timeout=3 * 60)

        # make a list of all ovs agent ids
        self.ovs_agent_ids = [
            agt['id'] for agt in
            self.os_conn.neutron.list_agents(
                binary='neutron-openvswitch-agent')['agents']]
        # make a list of ovs agents that resides only on controllers
        controllers = [node.data['fqdn']
                       for node in self.env.get_nodes_by_role('controller')]
        ovs_agts = self.os_conn.neutron.list_agents(
            binary='neutron-openvswitch-agent')['agents']
        self.ovs_conroller_agents = [agt['id'] for agt in ovs_agts
                                     if agt['host'] in controllers]

    @pytest.mark.testrail_id('580185', params={'count': 1})
    @pytest.mark.testrail_id('542649', params={'count': 40})
    @pytest.mark.parametrize('count', [1, 40], ids=['1x', '40x'])
    def test_ovs_restart_pcs_disable_enable(self, count):
        """Restart openvswitch-agents with pcs disable/enable on controllers

        Steps:
            1. Update default security group
            2. Create router01, create networks net01: net01__subnet,
                192.168.1.0/24, net02: net02__subnet, 192.168.2.0/24 and
                attach them to router01.
            3. Launch vm1 in net01 network and vm2 in net02 network
                on different computes
            4. Go to vm1 console and send pings to vm2
            5. Disable ovs-agents on a controller, restart service
                neutron-plugin-openvswitch-agent on all computes, and enable
                them back. To do this, launch the script against master node.
            6. Wait 30 seconds, send pings from vm1 to vm2 and check that
                it is successful.
            7. Repeat steps 6-7 'count' argument times

        Duration 10m

        """
        self._prepare_openstack()
        for _ in range(count):
            # Check that all ovs agents are alive
            self.os_conn.wait_agents_alive(self.ovs_agent_ids)

            # Disable ovs agent on a controller
            common.disable_ovs_agents_on_controller(self.env)

            # Then check that all ovs went down
            self.os_conn.wait_agents_down(self.ovs_conroller_agents)

            # Restart ovs agent service on all computes
            common.restart_ovs_agents_on_computes(self.env)

            # Enable ovs agent on a controller
            common.enable_ovs_agents_on_controllers(self.env)

            # Then check that all ovs agents are alive
            self.os_conn.wait_agents_alive(self.ovs_agent_ids)

            # sleep is used to check that system will be stable for some time
            # after restarting service
            time.sleep(30)

            network_checks.check_ping_from_vm(
                self.env, self.os_conn, self.server1, self.instance_keypair,
                self.server2_ip, timeout=10 * 60)

            # check all agents are alive
            assert all([agt['alive'] for agt in
                        self.os_conn.neutron.list_agents()['agents']])

    @pytest.mark.testrail_id('542644')
    def test_ovs_restart_pcs_ban_clear(self):
        """Restart openvswitch-agents with pcs ban/clear on controllers

        Steps:
            1. Update default security group
            2. Create router01, create networks.
            3. Launch vm1 in net01 network and vm2 in net02 network
                on different computes.
            4. Go to vm1 console and send pings to vm2
            5. Ban ovs-agents on all controllers, clear them and restart
                service neutron-plugin-openvswitch-agent on all computes.
                To do this, launch the script against master node.
            6. Wait 30 seconds, send pings from vm1 to vm2 and
                check that it is successful.

        Duration 10m

        """
        self._prepare_openstack()
        # Check that all ovs agents are alive
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        # Ban ovs agents on all controllers
        common.ban_ovs_agents_controllers(self.env)

        # Then check that all ovs went down
        self.os_conn.wait_agents_down(self.ovs_conroller_agents)

        # Cleat ovs agent on all controllers
        common.clear_ovs_agents_controllers(self.env)

        # Restart ovs agent service on all computes
        common.restart_ovs_agents_on_computes(self.env)

        # Then check that all ovs agents are alive
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        # sleep is used to check that system will be stable for some time
        # after restarting service
        time.sleep(30)

        network_checks.check_ping_from_vm(
            self.env, self.os_conn, self.server1, self.instance_keypair,
            self.server2_ip, timeout=3 * 60)

        # check all agents are alive
        assert all([agt['alive'] for agt in
                    self.os_conn.neutron.list_agents()['agents']])


@pytest.mark.check_env_('is_vlan')
class TestPortTags(OvsBase):
    """Check that port tags aren't change after ovs-agent restart"""

    def get_ports_tags_data(self, lines):
        """Returns dict with ports as keys and tags as values"""
        port_tags = {}
        last_offset = 0
        port = None
        for line in lines[1:]:
            line = line.rstrip()
            key, val = line.split(None, 1)
            offset = len(line) - len(line.lstrip())
            if port is None:
                if key.lower() == 'port':
                    port = val.strip('"')
                    last_offset = offset
                    continue
            elif offset <= last_offset:
                port = None
            elif key.lower() == 'tag:':
                port_tags[port] = val
                port = None
        return port_tags

    @pytest.mark.testrail_id('542664')
    def test_port_tags_immutable(self):
        """Check that ports tags don't change their values after
            ovs-agents restart

        Scenario:
            1. Collect ovs-vsctl tags before test
            2. Disable ovs-agents on all controllers,
                restart service 'neutron-plugin-openvswitch-agent'
                on all computes, and enable them back
            3. Check that all ovs-agents are in alive state
            4. Collect ovs-vsctl tags after test
            5. Check that values of the tag parameter for every port
                remain the same
        """

        def get_ovs_port_tags(nodes):
            ovs_cfg = {}
            for node in nodes:
                with node.ssh() as remote:
                    result = remote.check_call('ovs-vsctl show')
                    ports_tags = self.get_ports_tags_data(result['stdout'])
                    ovs_cfg[node.data['fqdn']] = ports_tags
            return ovs_cfg

        nodes = self.env.get_all_nodes()

        # Collect ovs-vsctl data before test
        ovs_before_port_tags = get_ovs_port_tags(nodes)

        # ban and clear ovs-agents on controllers
        controller = self.env.get_nodes_by_role('controller')[0]
        cmd = "pcs resource {{action}} {resource}".format(
            resource=common.ovs_agent_name)
        with controller.ssh() as remote:
            remote.check_call(cmd.format(action='disable'))
            remote.check_call(cmd.format(action='enable'))

        # restart ovs-agents on computes
        for node in self.env.get_nodes_by_role('compute'):
            with node.ssh() as remote:
                cmd = 'service {} restart'.format(common.ovs_agent_service)
                remote.check_call(cmd)

        # wait for 30 seconds
        time.sleep(30)

        # Collect ovs-vsctl data after test
        ovs_after_port_tags = get_ovs_port_tags(nodes)

        # Compare
        assert ovs_after_port_tags == ovs_before_port_tags


@pytest.mark.check_env_('is_ha', 'has_2_or_more_computes')
class TestOVSRestartsOneNetwork(OvsBase):

    def _prepare_openstack(self):
        """Prepare OpenStack for scenarios run

        Steps:
            1. Create network1
            2. Create router1 and connect it with network1 and external net
            3. Boot vm1 in network1 and associate floating ip
            4. Boot vm2 in network2
            5. Add rules for ping
            6. ping 8.8.8.8 from vm2
            7. ping vm1 from vm2 and vm1 from vm2
        """

        # init variables
        exist_networks = self.os_conn.list_networks()['networks']
        ext_network = [x for x in exist_networks
                       if x.get('router:external')][0]
        self.zone = self.os_conn.nova.availability_zones.find(zoneName="nova")
        self.hosts = self.zone.hosts.keys()
        self.instance_keypair = self.os_conn.create_key(key_name='instancekey')
        self.setup_rules_for_default_sec_group()

        # create router
        self.router = self.os_conn.create_router(name="router01")['router']
        self.os_conn.router_gateway_add(router_id=self.router['id'],
                                        network_id=ext_network['id'])
        logger.info('router {} was created'.format(self.router['id']))

        # create one network by amount of the compute hosts
        self.net_id = self.os_conn.add_net(self.router['id'])

        # create two instances in that network
        # each instance is on the own compute
        for i, hostname in enumerate(self.hosts, 1):
            self.os_conn.create_server(
                name='server%02d' % i,
                availability_zone='{}:{}'.format(self.zone.zoneName, hostname),
                key_name=self.instance_keypair.name,
                nics=[{'net-id': self.net_id}])

        # check pings
        network_checks.check_vm_connectivity(self.env, self.os_conn)

        # make a list of all ovs agent ids
        self.ovs_agent_ids = [
            agt['id'] for agt in self.os_conn.neutron.list_agents(
                binary='neutron-openvswitch-agent')['agents']]
        # make a list of ovs agents that resides only on controllers
        controllers = [node.data['fqdn']
                       for node in self.env.get_nodes_by_role('controller')]
        ovs_agts = self.os_conn.neutron.list_agents(
            binary='neutron-openvswitch-agent')['agents']
        self.ovs_conroller_agents = [agt['id'] for agt in ovs_agts
                                     if agt['host'] in controllers]

    @pytest.mark.testrail_id('542673')
    def test_restart_openvswitch_agent_under_bat(self):
        """Restart openvswitch-agents with broadcast traffic background

        Steps:
            1. Go to vm1's console and run arping
               to initiate broadcast traffic:
                    arping -I eth0 <vm2_fixed_ip>
            2. Disable ovs-agents on all controllers
            3. Restart service 'neutron-plugin-openvswitch-agent'
               on all computes
            4. Enable ovs-agents back.
            5. Check that pings between vm1 and vm2 aren't interrupted
               or not more than 2 packets are lost
        """
        self._prepare_openstack()
        # Run arping in background on server01 towards server02
        srv_list = self.os_conn.nova.servers.list()
        srv1, srv2 = srv_list[:2]
        vm_ip = self.os_conn.get_nova_instance_ips(srv2)['fixed']

        arping_cmd = 'sudo /usr/sbin/arping -I eth0 {}'.format(vm_ip)
        arping_log = '/tmp/arp.log'
        with self.os_conn.ssh_to_instance(self.env,
                                          srv1,
                                          vm_keypair=self.instance_keypair,
                                          username='cirros') as remote:
            pid = remote.background_call(arping_cmd, stdout=arping_log)

        # Then check that all ovs agents are alive
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        # Disable ovs agent on all controllers
        common.disable_ovs_agents_on_controller(self.env)

        # Then check that all ovs went down
        self.os_conn.wait_agents_down(self.ovs_conroller_agents)

        # Restart ovs agent service on all computes
        common.restart_ovs_agents_on_computes(self.env)

        # Enable ovs agent on all controllers
        common.enable_ovs_agents_on_controllers(self.env)

        # Then check that all ovs agents are alive
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        # Check that arping is still executing
        cmd = 'ps -o pid | grep {}'.format(pid)
        with self.os_conn.ssh_to_instance(self.env,
                                          srv1,
                                          vm_keypair=self.instance_keypair,
                                          username='cirros') as remote:
            result = remote.execute(cmd)
            assert result.is_ok, 'Arping command is died'

            # Read log of arpping execution for future possible debug
            with remote.open(arping_log) as f:
                logger.debug(f.read())

        # Check connectivity
        network_checks.check_vm_connectivity(self.env, self.os_conn)


@pytest.mark.check_env_("has_1_or_more_computes")
class TestOVSRestartTwoVmsOnSingleCompute(OvsBase):
    """Check restarts of openvswitch-agents."""

    def _prepare_openstack(self):
        """Prepare OpenStack for scenarios run

        Steps:
            1. Update default security group
            2. Create networks net01: net01__subnet, 192.168.1.0/24
            3. Launch vm1 and vm2 in net01 network on a single compute compute
            4. Go to vm1 console and send pings to vm2
        """
        self.instance_keypair = self.os_conn.create_key(key_name='instancekey')
        zone = self.os_conn.nova.availability_zones.find(zoneName="nova")
        host = zone.hosts.keys()[0]

        self.setup_rules_for_default_sec_group()

        # create 1 network and 2 instances
        net, subnet = self.create_internal_network_with_subnet()

        self.os_conn.create_server(
            name='server01',
            availability_zone='{}:{}'.format(zone.zoneName, host),
            key_name=self.instance_keypair.name,
            nics=[{'net-id': net['network']['id']}],
            max_count=2,
            wait_for_avaliable=False,
            wait_for_active=False)

        self.server1 = self.os_conn.nova.servers.find(name="server01-1")
        server2 = self.os_conn.nova.servers.find(name="server01-2")
        servers = [self.server1, server2]
        self.os_conn.wait_servers_active(servers)
        self.os_conn.wait_servers_ssh_ready(servers)

        # check pings
        self.server2_ip = self.os_conn.get_nova_instance_ips(server2)['fixed']

        network_checks.check_ping_from_vm(env=self.env,
                                          os_conn=self.os_conn,
                                          vm=self.server1,
                                          vm_keypair=self.instance_keypair,
                                          ip_to_ping=self.server2_ip,
                                          timeout=3 * 60,
                                          vm_login='cirros',
                                          vm_password='cubswin:)')

        # make a list of all ovs agent ids
        self.ovs_agent_ids = [
            agt['id'] for agt in
            self.os_conn.neutron.list_agents(
                binary='neutron-openvswitch-agent')['agents']]
        # make a list of ovs agents that resides only on controllers
        controllers = [node.data['fqdn']
                       for node in self.env.get_nodes_by_role('controller')]
        ovs_agts = self.os_conn.neutron.list_agents(
            binary='neutron-openvswitch-agent')['agents']
        self.ovs_conroller_agents = [agt['id'] for agt in ovs_agts
                                     if agt['host'] in controllers]

    @pytest.mark.testrail_id('542668')
    def test_ovs_restart_pcs_vms_on_single_compute_in_single_network(self):
        """Check connectivity for instances scheduled on a single compute in
         a single private network

        Steps:
            1. Update default security group
            2. Create networks net01: net01__subnet, 192.168.1.0/24
            3. Launch vm1 and vm2 in net01 network on a single compute compute
            4. Go to vm1 console and send pings to vm2
            5. Disable ovs-agents on all controllers, restart service
                neutron-plugin-openvswitch-agent on all computes, and enable
                them back. To do this, launch the script against master node.
            6. Wait 30 seconds, send pings from vm1 to vm2 and check that
                it is successful.

        Duration 10m

        """
        self._prepare_openstack()
        # Check that all ovs agents are alive
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        # Disable ovs agent on all controllers
        common.disable_ovs_agents_on_controller(self.env)

        # Then check that all ovs went down
        self.os_conn.wait_agents_down(self.ovs_conroller_agents)

        # Restart ovs agent service on all computes
        common.restart_ovs_agents_on_computes(self.env)

        # Enable ovs agent on all controllers
        common.enable_ovs_agents_on_controllers(self.env)

        # Then check that all ovs agents are alive
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        # sleep is used to check that system will be stable for some time
        # after restarting service
        time.sleep(30)

        network_checks.check_ping_from_vm(
            self.env, self.os_conn, self.server1, self.instance_keypair,
            self.server2_ip, timeout=3 * 60)

        # check all agents are alive
        assert all([agt['alive'] for agt in
                    self.os_conn.neutron.list_agents()['agents']])


@pytest.mark.check_env_("has_2_or_more_computes")
class TestOVSRestartWithIperfTraffic(OvsBase):
    """Restart ovs-agents with iperf traffic background"""

    def create_image(self):
        """Create image
        :return: image object for Glance
        """
        image = self.os_conn.glance.images.create(name="image_ubuntu",
                                                  disk_format='qcow2',
                                                  container_format='bare')
        with file_cache.get_file(settings.UBUNTU_QCOW2_URL) as f:
            self.os_conn.glance.images.upload(image['id'], f)
        return image

    def get_lost_percentage(self, output):
        """Get lost percentage

        :param output: output of iperf client
        :return: percentage of lost datagrams
        """
        output = output.strip()
        logger.debug('Iperf result:\n{}'.format(output))
        reader = csv.reader(output.splitlines())
        rows = list(reader)
        return float(rows[-1][12])

    def launch_iperf_client(self, client, server, keypair, vm_login,
                            stdout):
        """Launch iperf client"""
        client_cmd = ('iperf --port 5002 -u --client {0} --len 64'
                      ' --bandwidth 1M --time 60 -i 10 -y C -x CDMS').format(
                          self.os_conn.get_nova_instance_ips(server)['fixed'])
        with self.os_conn.ssh_to_instance(self.env,
                                          vm=client,
                                          vm_keypair=keypair,
                                          username=vm_login) as remote:
            return remote.background_call(client_cmd, stdout=stdout)

    def wait_command_done(self, pid, vm, keypair, vm_login):
        with self.os_conn.ssh_to_instance(self.env,
                                          vm=vm,
                                          vm_keypair=keypair,
                                          username=vm_login) as remote:
            cmd = 'ps -o pid | grep {}'.format(pid)
            wait(lambda: not remote.execute(cmd).is_ok,
                 timeout_seconds=2 * 60,
                 sleep_seconds=15,
                 waiting_for='command with pid {} to be done'.format(pid))

    def _prepare_openstack(self):
        """Prepare OpenStack for scenarios run

        Steps:
            1. Update default security group
            2. Upload the image with pre-installed iperf
            3. Create router01, create networks net01: net01__subnet,
            192.168.1.0/24, net02: net02__subnet, 192.168.2.0/24 and
            attach them to router01.
            4. Create keypair
            5. Launch vm1 in net01 network and vm2 in net02 network
            on different computes
            6. Go to vm1 console and send pings to vm2
        """

        self.setup_rules_for_default_sec_group()
        vm_image = self.create_image()

        self.instance_keypair = self.os_conn.create_key(
            key_name='instancekey')
        zone = self.os_conn.nova.availability_zones.find(zoneName="nova")
        hosts = zone.hosts.keys()[:2]

        # create router
        router = self.os_conn.create_router(name="router01")

        ext_network = self.os_conn.ext_network

        self.os_conn.router_gateway_add(router_id=router['router']['id'],
                                        network_id=ext_network['id'])

        ready_marker = 'ironic_iperf_boot_complete'

        userdata = '\n'.join([
            '#!/bin/bash -v',
            'apt-get install -yq iperf',
            'iperf -u -s -p 5002 <&- > /tmp/iperf_udp.log 2>&1 &',
            'echo "{marker}"',
        ]).format(marker=ready_marker)

        # create 2 networks and 2 instances
        instances = []
        for i, hostname in enumerate(hosts, 1):
            net, subnet = self.create_internal_network_with_subnet(suffix=i)
            self.os_conn.router_interface_add(
                router_id=router['router']['id'],
                subnet_id=subnet['subnet']['id'])
            instance = self.os_conn.create_server(
                name='server%02d' % i,
                availability_zone='{}:{}'.format(zone.zoneName, hostname),
                image_id=vm_image.id,
                userdata=userdata,
                flavor=2,
                timeout=60 * 10,
                key_name=self.instance_keypair.name,
                nics=[{'net-id': net['network']['id']}],
                wait_for_active=False,
                wait_for_avaliable=False)
            instances.append(instance)

        self.os_conn.wait_servers_active(instances)

        self.os_conn.wait_marker_in_servers_log(instances, marker=ready_marker)

        # check pings
        self.server1 = self.os_conn.nova.servers.find(name="server01")
        self.server2 = self.os_conn.nova.servers.find(name="server02")
        self.server2_ip = self.os_conn.get_nova_instance_ips(
            self.os_conn.nova.servers.find(name="server02")).values()[0]
        network_checks.check_ping_from_vm(
            self.env, self.os_conn, self.server1, self.instance_keypair,
            self.server2_ip, vm_login='ubuntu', vm_password='ubuntu',
            timeout=4 * 60)

        # make a list of ovs agents that resides only on controllers
        controllers = [node.data['fqdn']
                       for node in self.env.get_nodes_by_role('controller')]
        ovs_agts = self.os_conn.neutron.list_agents(
            binary='neutron-openvswitch-agent')['agents']

        # make a list of all ovs agent ids
        self.ovs_agent_ids = [agt['id'] for agt in ovs_agts]
        self.ovs_conroller_agents = [agt['id'] for agt in ovs_agts
                                     if agt['host'] in controllers]

    @pytest.mark.testrail_id('542659')
    def test_ovs_restart_with_iperf_traffic(self):
        """Checks that iperf traffic is not interrupted during ovs restart

        Steps:
            1. Run iperf server on server2
            2. Run iperf client on server 1
            3. Check that  packet losses < 1%
            4. Disable ovs-agents on all controllers,
                restart service neutron-plugin-openvswitch-agent
                on all computes, and enable them back.
            5. Check that all ovs-agents are in alive state
            6. Check that iperf traffic wasn't interrupted during ovs restart,
                and not more than 20% datagrams are lost
        """
        self._prepare_openstack()

        iperf_log_file = '/tmp/iperf_client.log'

        client = self.server1
        server = self.server2

        # Launch iperf client
        pid = self.launch_iperf_client(client, server,
                                       self.instance_keypair,
                                       vm_login='ubuntu',
                                       stdout=iperf_log_file)

        time.sleep(60)

        self.wait_command_done(pid, vm=client, keypair=self.instance_keypair,
                               vm_login='ubuntu')

        with self.os_conn.ssh_to_instance(self.env,
                                          vm=client,
                                          vm_keypair=self.instance_keypair,
                                          username='ubuntu') as remote:
            with remote.open(iperf_log_file) as f:
                iperf_result = f.read()

        # Check iperf traffic before restart
        lost = self.get_lost_percentage(iperf_result)
        err_msg = "Packet losses more than 1%. Actual value is {0}%".format(
            lost)
        assert lost < 1, err_msg

        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        # Launch client in background and restart agents
        pid = self.launch_iperf_client(client, server,
                                       self.instance_keypair,
                                       vm_login='ubuntu',
                                       stdout=iperf_log_file)

        common.disable_ovs_agents_on_controller(self.env)
        self.os_conn.wait_agents_down(self.ovs_conroller_agents)
        common.restart_ovs_agents_on_computes(self.env)
        common.enable_ovs_agents_on_controllers(self.env)
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        self.wait_command_done(pid, vm=client, keypair=self.instance_keypair,
                               vm_login='ubuntu')

        with self.os_conn.ssh_to_instance(self.env,
                                          vm=client,
                                          vm_keypair=self.instance_keypair,
                                          username='ubuntu') as remote:
            with remote.open(iperf_log_file) as f:
                iperf_result = f.read()

        err_msg = "{0}% datagrams lost. Should be < 20%".format(lost)
        assert lost < 20, err_msg

        # check all agents are alive
        assert all([agt['alive'] for agt in
                    self.os_conn.neutron.list_agents()['agents']])


class TestOVSRestartAddFlows(OvsBase):
    """Check that new flows are added after restarts of openvswitch-agents."""

    def _prepare_openstack(self):
        """Prepare OpenStack for scenarios run

        Steps:
            1. Update default security group
            2. Create create network net01: net01__subnet,
            192.168.1.0/24.
            3. Launch vm1 in net01 network
            4. Get list of openvswitch-agents
        """
        self.instance_keypair = self.os_conn.create_key(key_name='instancekey')
        zone = self.os_conn.nova.availability_zones.find(zoneName="nova")
        host = zone.hosts.keys()[0]

        self.setup_rules_for_default_sec_group()

        # create 1 network and 1 instance
        net, subnet = self.create_internal_network_with_subnet(suffix=1)
        self.os_conn.create_server(
            name='server_for_flow_check',
            availability_zone='{}:{}'.format(zone.zoneName, host),
            key_name=self.instance_keypair.name,
            nics=[{'net-id': net['network']['id']}])

        controllers = [node.data['fqdn']
                       for node in self.env.get_nodes_by_role('controller')]
        ovs_agts = self.os_conn.neutron.list_agents(
            binary='neutron-openvswitch-agent')['agents']

        # make a list of all ovs agent ids
        self.ovs_agent_ids = [agt['id'] for agt in ovs_agts]
        # make a list of ovs agents that resides only on controllers
        self.ovs_conroller_agents = [agt['id'] for agt in ovs_agts
                                     if agt['host'] in controllers]

    @pytest.mark.testrail_id('542654')
    def test_ovs_new_flows_added_after_restart(self):
        """Check that new flows are added after ovs-agents restart

        Steps:
            1. Create network net01: net01__subnet, 192.168.1.0/24
            2. Launch vm1 in net01 network
            3. Get list of flows for br-int
            4. Save cookie parameter for bridge
            5. Disable ovs-agents on all controllers, restart service
               neutron-plugin-openvswitch-agent on all computes, and enable
               them back. To do this, launch the script against master node.
            6. Check that all ovs-agents are in alive state
            7. Get list of flows for br-int again
            8. Compare cookie parameters
        """
        self._prepare_openstack()
        server = self.os_conn.nova.servers.find(name="server_for_flow_check")
        node_name = getattr(server, "OS-EXT-SRV-ATTR:hypervisor_hostname")
        compute = [i for i in self.env.get_nodes_by_role('compute')
                   if i.data['fqdn'] == node_name][0]

        # Check that all ovs agents are alive
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        before_value = self.get_current_cookie(compute)

        assert all([len(x) < 2 for x in before_value.values()])

        # Disable ovs agent on all controllers
        common.disable_ovs_agents_on_controller(self.env)

        # Then check that all ovs went down
        self.os_conn.wait_agents_down(self.ovs_conroller_agents)

        # Restart ovs agent service on all computes
        common.restart_ovs_agents_on_computes(self.env)

        # Enable ovs agent on all controllers
        common.enable_ovs_agents_on_controllers(self.env)

        # Then check that all ovs agents are alive
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        # sleep is used to check that system will be stable for some time
        # after restarting service
        time.sleep(30)

        after_value = self.get_current_cookie(compute)
        assert before_value != after_value

        assert all([len(x) < 2 for x in after_value.values()])


@pytest.mark.check_env_("has_2_or_more_computes", "is_vlan")
class TestOVSRestartTwoSeparateVms(OvsBase):
    """Check restarts of openvswitch-agents."""

    def _prepare_openstack(self):
        """Prepare OpenStack for scenarios run

        Steps:
        1. Update default security group if needed
        2. Create CONFIG 1:
        Network: test_net_05
        SubNetw: test_net_05__subnet, 192.168.5.0/24
        Router:  test_router_05
        3. Create CONFIG 2:
        Network: test_net_06
        SubNetw: test_net_06__subnet, 192.168.6.0/24
        Router:  test_router_06
        4. Launch 'test_vm_05' in 'config 1'
        5. Launch 'test_vm_05' in 'config 2'
        6. Go to 'test_vm_05' console and send pings to 'test_vm_05'.
        Pings should NOT go between VMs.
        """
        # Create new key-pair with random name
        keypair_name = 'instancekey_{0}'.format(randint(100, 1000))
        self.instance_keypair = self.os_conn.create_key(key_name=keypair_name)
        logger.info('New keypair "{0}" was created'.format(keypair_name))

        zone = self.os_conn.nova.availability_zones.find(zoneName="nova")
        hosts = zone.hosts.keys()[:2]

        # Try to add sec groups if they were not added before
        logger.info('Add security groups')
        self.setup_rules_for_default_sec_group()

        # Create 2 separate routers, 2 networks, 2 vm instances
        # and associate each element to their router (06net+06sub-> 06 router)
        for i, hostname in enumerate(hosts, 5):  # will be: 5,6
            net, subnet = self.create_internal_network_with_subnet(suffix=i)
            # Create router
            router_name = 'test_router_%02d' % i  # test_router_05/_06
            logger.info('Create router: "{0}"'.format(router_name))
            router = self.os_conn.create_router(name=router_name)
            # Add interface to router
            logger.info('Add subnet "{0}" to router "{1}"'.format(
                subnet['subnet']['name'],
                router['router']['name']))
            self.os_conn.router_interface_add(
                router_id=router['router']['id'],
                subnet_id=subnet['subnet']['id'])
            # Create server
            vm_server_name = 'test_vm_%02d' % i  # 'test_vm_05' / 'test_vm_06'
            logger.info('Create VM instance: "{0}"'.format(vm_server_name))
            self.os_conn.create_server(
                name=vm_server_name,
                availability_zone='{}:{}'.format(zone.zoneName, hostname),
                key_name=self.instance_keypair.name,
                timeout=200,
                nics=[{'net-id': net['network']['id']}])

        # Check pings with alive ovs-agents,
        # and before restart 'neutron-plugin-openvswitch-agent'
        self.server1 = self.os_conn.nova.servers.find(name="test_vm_06")
        self.server2_ip = self.os_conn.get_nova_instance_ips(
            self.os_conn.nova.servers.find(name="test_vm_05")
        ).values()[0]

        # Ping should NOT go between VMs
        self.check_no_ping_from_vm(self.server1, self.instance_keypair,
                                   self.server2_ip, timeout=None)

        # make a list of all ovs agent ids
        self.ovs_agent_ids = [
            agt['id'] for agt in
            self.os_conn.neutron.list_agents(
                binary='neutron-openvswitch-agent')['agents']]
        # make a list of ovs agents that resides only on controllers
        controllers = [node.data['fqdn']
                       for node in self.env.get_nodes_by_role('controller')]
        ovs_agts = self.os_conn.neutron.list_agents(
            binary='neutron-openvswitch-agent')['agents']
        self.ovs_conroller_agents = [agt['id'] for agt in ovs_agts
                                     if agt['host'] in controllers]

    @pytest.mark.testrail_id('542666')
    def test_ovs_restart_pcs_disable_enable_ping_private_vms(self):
        """Restart openvswitch-agents with pcs disable/enable on controllers.

        Steps:
            1. Update default security group if needed
            2. Create CONFIG 1:
                Network: test_net_05
                SubNetw: test_net_05__subnet, 192.168.5.0/24
                Router:  test_router_05
            3. Create CONFIG 2:
                Network: test_net_06
                SubNetw: test_net_06__subnet, 192.168.6.0/24
                Router:  test_router_06
            4. Launch 'test_vm_05' inside 'config 1'
            5. Launch 'test_vm_06' inside 'config 2'
            6. Go to 'test_vm_05' console and send pings to 'test_vm_05'.
                Pings should NOT go between VMs.
            7. Operations with OVS agents:
                - Check that all OVS are alive;
                - Disable ovs-agents on all controllers;
                - Check that they wend down;
                - Restart OVS agent service on all computes;
                - Enable ovs-agents on all controllers;
                - Check that they wend up and alive;
            8. Wait 30 seconds, send pings from 'test_vm_05' to 'test_vm_06'
                and check that they are still NOT successful.

        Duration 5m

        """
        self._prepare_openstack()
        # Check that all ovs agents are alive
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        # Disable ovs agent on all controllers
        common.disable_ovs_agents_on_controller(self.env)

        # Then check that all ovs went down
        self.os_conn.wait_agents_down(self.ovs_conroller_agents)

        # Restart ovs agent service on all computes
        common.restart_ovs_agents_on_computes(self.env)

        # Enable ovs agent on all controllers
        common.enable_ovs_agents_on_controllers(self.env)

        # Then check that all ovs agents are alive
        self.os_conn.wait_agents_alive(self.ovs_agent_ids)

        # sleep is used to check that system will be stable for some time
        # after restarting service
        time.sleep(30)

        self.check_no_ping_from_vm(self.server1, self.instance_keypair,
                                   self.server2_ip, timeout=None)
