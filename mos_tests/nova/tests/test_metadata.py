"""
Server metadata tests.

@author: schipiga@mirantis.com
"""

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

import pytest

from mos_tests.utils import generate_ids


@pytest.fixture
def admin_ssh_key_path(env):
    return env.admin_ssh_keys_paths[0]


@pytest.fixture
def ssh_proxy_data(admin_ssh_key_path, neutron_steps, server_steps, env):
    """Fixture to get ssh proxy data of server."""
    def _ssh_proxy_data(server):
        import ipdb; ipdb.set_trace()
        ip_info = server_steps.get_ips(server, 'fixed').values()[0]
        server_ip = ip_info['ip']
        server_mac = ip_info['mac']
        net_id = neutron_steps.network_by_mac(server_mac)['network_id']
        dhcp_netns = "qdhcp-{}".format(net_id)
        dhcp_server_ip = neutron_steps.dhcp_server_ip_by_network(net_id)
        cmd = 'ssh -i {} root@{} ip netns exec {} netcat {} 22'.format(
            admin_ssh_key_path, dhcp_server_ip, dhcp_netns, server_ip)

        return cmd, server_ip

    return _ssh_proxy_data


@pytest.mark.testrail_id('843871')
def test_metadata_reach_all_booted_vm(security_group, nova_floating_ip,
                                      ubuntu_image, keypair, flavor_steps,
                                      neutron_steps, server_steps,
                                      ssh_proxy_data):
    """Check that metadata reach all booted VMs.

    Scenario:
        1. Create a Glance image based on Ubuntu image
        2. Boot an server based on previously created image
        3. Check that this server is reachable via ssh connection
        4. Delete server
        5. Repeat pp 2-4 100 times (TODO(schipiga): like like a magic number)
    """
    flavor = flavor_steps.find(name='m1.small')
    network = neutron_steps.find(name='admin_internal_net')

    for server_name in generate_ids('server', count=1):
        server = server_steps.create_server(server_name, ubuntu_image, flavor,
                                            network, keypair, [security_group])

        ssh_proxy_cmd, ssh_ip = ssh_proxy_data(server)
#        server_steps.attach_floating_ip(server, nova_floating_ip)
        server_steps.check_ssh_connect(server, keypair, username='ubuntu',
                                       ip=ssh_ip, proxy_cmd=ssh_proxy_cmd,
                                       timeout=600)

#        server_steps.detach_floating_ip(server, nova_floating_ip)
        server_steps.delete_server(server)
