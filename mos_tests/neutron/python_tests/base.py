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
import warnings

from neutronclient.common.exceptions import OverQuotaClient
from neutronclient.common.exceptions import ServiceUnavailable
import paramiko
import pytest
import six

from mos_tests.functions.common import wait
from mos_tests.functions import network_checks
from mos_tests import settings


logger = logging.getLogger(__name__)


class NotFound(Exception):
    message = "Not Found."


class TestBase(object):
    """Class contains common methods for neutron tests"""

    @pytest.fixture(autouse=True)
    def init(self, fuel, env, os_conn, devops_env):
        self.fuel = fuel
        self.env = env
        self.os_conn = os_conn
        self.devops_env = devops_env
        self.cirros_creds = {'username': 'cirros',
                             'password': 'cubswin:)'}

    def get_node_with_dhcp(self, net_id):
        nodes = self.os_conn.get_node_with_dhcp_for_network(net_id)
        if not nodes:
            raise NotFound("Nodes with dhcp for network with id:{}"
                           " not found.".format(net_id))

        return self.env.find_node_by_fqdn(nodes[0])

    def create_internal_network_with_subnet(self, suffix=1, cidr=None):
        """Create network with subnet.

        :param suffix: desired integer suffix to names of network, subnet
        :param cidr: desired cidr of subnet
        :returns: tuple, network and subnet
        """
        if cidr is None:
            cidr = '192.168.%d.0/24' % suffix

        network = self.os_conn.create_network(name='net%02d' % suffix)
        subnet = self.os_conn.create_subnet(
            network_id=network['network']['id'],
            name='net%02d__subnet' % suffix,
            cidr=cidr)
        return network, subnet

    def create_router_between_nets(self, ext_net, subnet, suffix=1):
        """Create router between external network and sub network.

        :param ext_net: external network to set gateway
        :param subnet: subnet which for provide route to external network
        :param suffix: desired integer suffix to names of router

        :returns: created router
        """
        router = self.os_conn.create_router(name='router%02d' % suffix)
        self.os_conn.router_gateway_add(
            router_id=router['router']['id'],
            network_id=ext_net['id'])

        self.os_conn.router_interface_add(
            router_id=router['router']['id'],
            subnet_id=subnet['subnet']['id'])
        return router

    def check_no_ping_from_vm(self,
                              vm,
                              vm_keypair=None,
                              ip_to_ping=None,
                              timeout=None,
                              vm_login='cirros',
                              vm_password='cubswin:)'):
        logger.info('Expecting that ping from VM should fail')
        # Get ping results
        with pytest.raises(AssertionError):
            network_checks.check_ping_from_vm(env=self.env,
                                              os_conn=self.os_conn,
                                              vm=vm,
                                              vm_keypair=vm_keypair,
                                              ip_to_ping=ip_to_ping,
                                              timeout=timeout)

    def check_ping_from_vm_with_ip(self, vm_ip, vm_keypair=None,
                                   ip_to_ping=None, ping_count=1,
                                   vm_login='cirros',
                                   vm_password='cubswin:)'):
        if ip_to_ping is None:
            ip_to_ping = [settings.PUBLIC_TEST_IP]
        if isinstance(ip_to_ping, six.string_types):
            ip_to_ping = [ip_to_ping]
        pkeys = self.convert_private_key_for_vm([vm_keypair.private_key])
        cmd_list = ["ping -c{0} {1}".format(ping_count, x) for x in ip_to_ping]
        with self.env.get_ssh_to_vm(vm_ip, private_keys=pkeys,
                                    username=vm_login,
                                    password=vm_password) as vm_remote:
            res = vm_remote.execute(' && '.join(cmd_list))
            assert 0 == res['exit_code'], \
                ('Ping is not successful, exit code {0},'
                 'stdout {1}, stderr {2}'.format(res['exit_code'],
                                                 res['stdout'],
                                                 res['stderr']))

    def run_on_cirros(self, vm, cmd):
        """Run command on Cirros VM, connected by floating ip.

        :param vm: instance with cirros
        :param cmd: command to execute
        :returns: dict, result of command with code, stdout, stderr.
        """
        vm = self.os_conn.get_instance_detail(vm)
        _floating_ip = self.os_conn.get_nova_instance_ips(vm)['floating']

        with self.env.get_ssh_to_vm(_floating_ip,
                                    **self.cirros_creds) as remote:
            res = remote.execute(cmd)
        return res

    def check_ping_from_cirros(self, vm, ip_to_ping=None):
        """Run ping some ip from Cirros instance.

        :param vm: instance with cirros
        :param ip_to_ping: ip to ping
        """
        ip_to_ping = ip_to_ping or settings.PUBLIC_TEST_IP
        cmd = "ping -c1 {0}".format(ip_to_ping)
        res = self.run_on_cirros(vm, cmd)
        error_msg = (
            'Instance has no connectivity, '
            'exit code {exit_code},'
            'stdout {stdout}, stderr {stderr}').format(**res)
        assert 0 == res['exit_code'], error_msg

    def check_vm_is_accessible_with_ssh(self,
                                        vm_ip,
                                        username=None,
                                        password=None,
                                        pkeys=None):
        """Check that instance is accessible with ssh via floating_ip.

            :param vm_ip: floating_ip of instance
            :param username: username to login to instance
            :param password: password to connect to instance
            :param pkeys: private keys to connect to instance
            """

        error_msg = ('Instance with ip {0} '
                     'is not accessible with ssh.').format(vm_ip)

        def is_accessible():
            ssh_client = self.env.get_ssh_to_vm(vm_ip, username, password,
                                                pkeys)
            return ssh_client.check_connection() is True

        wait(is_accessible,
             sleep_seconds=10,
             timeout_seconds=60,
             waiting_for=error_msg)

    @staticmethod
    def convert_private_key_for_vm(private_keys):
        return [paramiko.RSAKey.from_private_key(six.StringIO(str(pkey)))
                for pkey in private_keys]

    def run_udhcpc_on_vm(self, vm):
        with self.os_conn.ssh_to_instance(
                self.env, vm, vm_keypair=self.instance_keypair) as remote:
            remote.check_call('sudo -i cirros-dhcpc up eth0')

    def create_delete_number_of_instances(self, net_number, router, net_list,
                                          inst_keypair, security_group):
        """Create X number of networks, create and delete instance on it.

        :param net_number: number of networks to create
        :param router: router to external network
        :param net_list: list of existed networks
        :param inst_keypair: private keys to connect to instance
        :param security_group: security group that instance is related to
        :returns: -
        """
        warnings.warn('This method is deprecated and will be removed in '
                      'future. Use `set_neutron_quota` and '
                      '`create_max_networks_with_instances` instead.',
                      DeprecationWarning)
        exists_nets_count = len(self.os_conn.list_networks()['networks'])
        tenant = self.os_conn.neutron.get_quotas_tenant()
        tenant_id = tenant['tenant']['tenant_id']
        self.os_conn.neutron.update_quota(tenant_id, {'quota':
                                                      {'network': 50,
                                                       'router': 50,
                                                       'subnet': 50,
                                                       'port': 150}})
        for x in range(exists_nets_count, net_number + exists_nets_count):
            net_id = self.os_conn.add_net(router['id'])
            net_list.append(net_id)
            logger.info('Total networks created at the moment {}'.format(
                        len(net_list)))
            srv = self.os_conn.create_server(
                name='instanceNo{}'.format(x),
                key_name=inst_keypair.name,
                security_groups=[security_group.name],
                nics=[{'net-id': net_id}],
                wait_for_avaliable=False)
            logger.info('Delete the server {}'.format(srv.name))
            self.os_conn.nova.servers.delete(srv)

    def set_neutron_quota(self,
                          network=50,
                          router=50,
                          subnet=50,
                          port=150,
                          **kwargs):
        tenant = self.os_conn.neutron.get_quotas_tenant()
        tenant_id = tenant['tenant']['tenant_id']
        quota = {'network': network,
                 'router': router,
                 'subnet': subnet,
                 'port': port}
        quota.update(kwargs)
        self.os_conn.neutron.update_quota(tenant_id, {'quota': quota})

    def create_max_networks_with_instances(self, router):
        """Create max possible networks, boot and delete instances on it"""

        def delete_instances(servers):
            self.os_conn.wait_servers_active(servers)
            logger.info('Delete created servers')
            for server in servers:
                server.delete()
            self.os_conn.wait_servers_deleted(servers)

        hypervisors = self.os_conn.nova.hypervisors.list()
        flavor = self.os_conn.nova.flavors.find(name='m1.micro')
        capacities = []
        for hypervisor in hypervisors:
            capacities.append(
                self.os_conn.get_hypervisor_capacity(hypervisor, flavor))
        max_instances = sum(capacities)

        i = 0
        net_list = []
        servers = []
        try:
            while True:
                i += 1
                logger.info('Create network #{}'.format(i))
                net_id = self.os_conn.add_net(router['id'])
                net_list.append(net_id)
                if len(servers) >= max_instances:
                    delete_instances(servers)
                    servers = []

                logger.info('Create server #{}'.format(i))
                srv = self.os_conn.create_server(name='instanceNo{}'.format(i),
                                                 nics=[{'net-id': net_id}],
                                                 flavor=flavor,
                                                 wait_for_active=False,
                                                 wait_for_avaliable=False)
                servers.append(srv)
        except (ServiceUnavailable, OverQuotaClient) as e:
            logger.info(e)

        delete_instances(servers)

        return net_list
