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

from contextlib2 import suppress
from novaclient import exceptions as nova_exceptions
import pytest

from mos_tests.functions import common

from .fixtures import *  # noqa

logger = logging.getLogger(__name__)


@pytest.yield_fixture
def network(os_conn, request):
    network = os_conn.create_network(name='net01')
    os_conn.create_subnet(network_id=network['network']['id'],
                          name='net01__subnet', cidr='192.168.1.0/24')
    yield network
    if 'undestructive' in request.node.keywords:
        os_conn.delete_net_subnet_smart(network['network']['id'])


def delete_instances(os_conn, instances):
    for instance in instances:
        with suppress(nova_exceptions.NotFound):
            instance.force_delete()
    os_conn.wait_servers_deleted(instances)


@pytest.yield_fixture
def instances(request, os_conn, security_group, keypair, network):
    """Some instances (2 by default) on one compute node at one network"""
    zone = os_conn.nova.availability_zones.find(zoneName="nova")
    compute_host = zone.hosts.keys()[0]
    instances = []
    param = getattr(request, 'param', {'count': 2})
    for i in range(param['count']):
        instance = os_conn.create_server(
            name='server%02d' % i,
            availability_zone='{}:{}'.format(zone.zoneName, compute_host),
            key_name=keypair.name,
            nics=[{'net-id': network['network']['id']}],
            security_groups=[security_group.id],
            wait_for_active=False,
            wait_for_avaliable=False)
        instances.append(instance)
    os_conn.wait_servers_active(instances)
    os_conn.wait_servers_ssh_ready(instances)

    yield instances
    if 'undestructive' in request.node.keywords:
        hosts = {os_conn.get_srv_hypervisor_name(x) for x in instances if
                 x in os_conn.nova.servers.list()}
        hosts.add(compute_host)
        delete_instances(os_conn, instances)
        for host in hosts:
            hypervisor = os_conn.nova.hypervisors.find(
                hypervisor_hostname=host)
            os_conn.wait_hypervisor_be_free(hypervisor)


@pytest.fixture
def error_instance(request, os_conn, security_group, keypair, network):
    instance = os_conn.create_server(
        name='err_server',
        availability_zone='nova:node-999.test.domain.local',
        key_name=keypair.name,
        nics=[{'net-id': network['network']['id']}],
        security_groups=[security_group.id],
        wait_for_active=False,
        wait_for_avaliable=False)

    if 'undestructive' in request.node.keywords:
        request.addfinalizer(lambda: delete_instances(os_conn, [instance]))

    common.wait(lambda: os_conn.nova.servers.get(instance).status == 'ERROR',
                timeout_seconds=2 * 60,
                waiting_for='instances to became to ERROR status')

    return instance
