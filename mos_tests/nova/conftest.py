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

from mos_tests.functions import common


@pytest.yield_fixture
def network(os_conn, request):
    network = os_conn.create_network(name='net01')
    subnet = os_conn.create_subnet(network_id=network['network']['id'],
                                   name='net01__subnet',
                                   cidr='192.168.1.0/24')
    yield network
    if 'undestructive' in request.node.keywords:
        os_conn.delete_subnet(subnet['subnet']['id'])
        os_conn.delete_network(network['network']['id'])


@pytest.yield_fixture
def keypair(os_conn, request):
    keypair = os_conn.create_key(key_name='instancekey')
    yield keypair
    if 'undestructive' in request.node.keywords:
        os_conn.delete_key(key_name=keypair.name)


@pytest.yield_fixture
def security_group(os_conn, request):
    sec_group = os_conn.create_sec_group_for_ssh()
    yield sec_group
    if 'undestructive' in request.node.keywords:
        os_conn.delete_security_group(name=sec_group.name)


@pytest.yield_fixture
def instances(request, os_conn, security_group, keypair, network):
    """2 instances on one compute node at one network"""
    zone = os_conn.nova.availability_zones.find(zoneName="nova")
    compute_host = zone.hosts.keys()[0]
    instances = []
    for i in range(2):
        instance = os_conn.create_server(
            name='server%02d' % i,
            availability_zone='{}:{}'.format(zone.zoneName, compute_host),
            key_name=keypair.name,
            nics=[{'net-id': network['network']['id']}],
            security_groups=[security_group.id],
            wait_for_active=False,
            wait_for_avaliable=False)
        instances.append(instance)
    common.wait(lambda: all(os_conn.is_server_active(x) for x in instances),
                timeout_seconds=2 * 60,
                waiting_for='instances to became to ACTIVE status')
    common.wait(lambda: all(os_conn.is_server_ssh_ready(x) for x in instances),
                timeout_seconds=2 * 60,
                waiting_for='instances to be ssh available')
    yield instances
    if 'undestructive' in request.node.keywords:
        for instance in instances:
            instance.delete()
        common.wait(
            lambda: all(os_conn.is_server_deleted(x.id) for x in instances),
            timeout_seconds=60,
            waiting_for='instances to be deleted')