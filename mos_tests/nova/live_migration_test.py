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
from multiprocessing.dummy import Pool

import dpath.util
from novaclient import exceptions as nova_exceptions
import pytest
from six.moves import configparser
from waiting import ALL

from mos_tests.functions import common
from mos_tests.functions import file_cache
from mos_tests import settings

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.undestructive


def is_migrated(os_conn, instances, target=None, source=None):
    assert any([source, target]), 'One of target or source is required'
    for instance in instances:
        instance.get()
        host = getattr(instance, 'OS-EXT-SRV-ATTR:host')
        if not os_conn.is_server_active(instance):
            return False
        if target and host != target:
            return False
        if source and host == source:
            return False
    return True


@pytest.yield_fixture(scope='module')
def unlimited_live_migrations(env):
    nova_config_path = '/etc/nova/nova.conf'

    nodes = (
        env.get_nodes_by_role('controller') + env.get_nodes_by_role('compute'))
    for node in nodes:
        if 'controller' in node.data['roles']:
            restart_cmd = 'service nova-api restart'
        else:
            restart_cmd = 'service nova-compute restart'
        with node.ssh() as remote:
            remote.check_call('cp {0} {0}.bak'.format(nova_config_path))

            parser = configparser.RawConfigParser()
            with remote.open(nova_config_path) as f:
                parser.readfp(f)
            parser.set('DEFAULT', 'max_concurrent_live_migrations', 0)
            with remote.open(nova_config_path, 'w') as f:
                parser.write(f)
            remote.check_call(restart_cmd)

    common.wait(env.os_conn.is_nova_ready,
                timeout_seconds=60 * 5,
                expected_exceptions=Exception,
                waiting_for="Nova services to be alive")

    yield
    for node in nodes:
        if 'controller' in node.data['roles']:
            restart_cmd = 'service nova-api restart'
        else:
            restart_cmd = 'service nova-compute restart'
        with node.ssh() as remote:
            result = remote.execute('mv {0}.bak {0}'.format(nova_config_path))
            if result.is_ok:
                remote.check_call(restart_cmd)

    common.wait(env.os_conn.is_nova_ready,
                timeout_seconds=60 * 5,
                expected_exceptions=Exception,
                waiting_for="Nova services to be alive")


@pytest.fixture
def big_hypervisors(os_conn):
    hypervisors = os_conn.nova.hypervisors.list()
    for flavor in os_conn.nova.flavors.list():
        suitable_hypervisors = []
        for hypervisor in hypervisors:
            if os_conn.get_hypervisor_capacity(hypervisor, flavor) > 0:
                suitable_hypervisors.append(hypervisor)
        hypervisors = suitable_hypervisors
    if len(hypervisors) < 2:
        pytest.skip('This test requires minimum 2 hypervisors '
                    'suitable for max flavor')
    return hypervisors[:2]


@pytest.yield_fixture
def big_port_quota(os_conn):
    tenant = os_conn.neutron.get_quotas_tenant()
    tenant_id = tenant['tenant']['tenant_id']
    orig_quota = os_conn.neutron.show_quota(tenant_id)
    new_quota = orig_quota.copy()
    # update quota for class C net
    new_quota['quota']['port'] = 256
    os_conn.neutron.update_quota(tenant_id, new_quota)
    yield
    os_conn.neutron.update_quota(tenant_id, orig_quota)


@pytest.fixture(scope='session')
def block_migration(env, request):
    value = request.param
    data = env.get_settings_data()
    if dpath.util.get(data, '*/storage/**/ephemeral_ceph/value') and value:
        pytest.skip('Block migration requires Nova Ceph RBD to be disabled')
    if not dpath.util.get(data,
                          '*/storage/**/ephemeral_ceph/value') and not value:
        pytest.skip('True live migration requires Nova Ceph RBD')
    return value


@pytest.yield_fixture(scope='module')
def ubuntu_image_id(os_conn):
    logger.info('Creating ubuntu image')
    image = os_conn.glance.images.create(name="image_ubuntu",
                                         disk_format='qcow2',
                                         container_format='bare')
    with file_cache.get_file(settings.UBUNTU_QCOW2_URL) as f:
        os_conn.glance.images.upload(image.id, f)

    logger.info('Ubuntu image created')
    yield image.id
    os_conn.glance.images.delete(image.id)


@pytest.yield_fixture
def router(os_conn, network):
    router = os_conn.create_router(name='router01')
    os_conn.router_gateway_add(router_id=router['router']['id'],
                               network_id=os_conn.ext_network['id'])

    subnet = os_conn.neutron.list_subnets(
        network_id=network['network']['id'])['subnets'][0]

    os_conn.router_interface_add(router_id=router['router']['id'],
                                 subnet_id=subnet['id'])
    yield router
    os_conn.delete_router(router['router']['id'])


class TestLiveMigrationBase(object):
    @pytest.fixture(autouse=True)
    def init(self, env, os_conn, keypair, security_group, network):
        self.env = env
        self.os_conn = os_conn
        self.keypair = keypair
        self.security_group = security_group
        self.network = network
        self.instances = []
        self.volumes = []

    def create_instances(self,
                         zone,
                         flavor,
                         instances_count,
                         image_id=None,
                         userdata=None,
                         create_args=None):
        boot_marker = 'INSTANCE BOOT COMPLETED'

        logger.info('Start with flavor {0.name}, '
                    'creates {1} instances'.format(flavor, instances_count))
        if userdata is not None:
            userdata += '\necho "{marker}"'.format(marker=boot_marker)

        if create_args is not None:
            assert len(create_args) == instances_count
        else:
            create_args = [{}] * instances_count
        for i in range(instances_count):
            kwargs = create_args[i]
            instance = self.os_conn.create_server(
                name='server%02d' % i,
                image_id=image_id,
                userdata=userdata,
                flavor=flavor,
                availability_zone=zone,
                key_name=self.keypair.name,
                nics=[{'net-id': self.network['network']['id']}],
                security_groups=[self.security_group.id],
                wait_for_active=False,
                wait_for_avaliable=False,
                **kwargs)
            self.instances.append(instance)
        predicates = [lambda: self.os_conn.is_server_active(x)
                      for x in self.instances]
        common.wait(
            ALL(predicates),
            timeout_seconds=5 * 60,
            waiting_for="instances to became to ACTIVE status")

        if userdata is None:
            predicates = [lambda: self.os_conn.is_server_ssh_ready(x)
                          for x in self.instances]
        else:
            predicates = [lambda: boot_marker in x.get_console_output()
                          for x in self.instances]
        common.wait(
            ALL(predicates),
            timeout_seconds=5 * 60,
            waiting_for="instances to be ready")

    def delete_instances(self, force=False):
        for instance in self.instances:
            if force:
                delete = instance.force_delete
            else:
                delete = instance.delete
            try:
                delete()
            except nova_exceptions.NotFound:
                pass
        common.wait(
            lambda: all(self.os_conn.is_server_deleted(x.id)
                        for x in self.instances),
            timeout_seconds=2 * 60,
            waiting_for='instances to be deleted')
        self.instances = []

    @pytest.yield_fixture
    def cleanup_instances(self):
        yield
        self.delete_instances(force=True)

    @pytest.yield_fixture
    def cleanup_volumes(self):
        yield
        for volume in self.volumes:
            common.delete_volume(self.os_conn.cinder, volume)

    def successive_migration(self, block_migration, hypervisor_from):
        logger.info('Start successive migrations')
        for instance in self.instances:
            instance.live_migrate(block_migration=block_migration)

        common.wait(
            lambda: is_migrated(self.os_conn, self.instances,
                                source=hypervisor_from.hypervisor_hostname),
            timeout_seconds=5 * 60,
            waiting_for='instances to migrate from '
                        '{0.hypervisor_hostname}'.format(hypervisor_from))

    def concurrent_migration(self, block_migration, hypervisor_to):
        pool = Pool(len(self.instances))
        logger.info('Start concurrent migrations')
        host = hypervisor_to.hypervisor_hostname
        try:
            pool.map(
                lambda x: x.live_migrate(host=host,
                                         block_migration=block_migration),
                self.instances)
        finally:
            pool.terminate()

        common.wait(
            lambda: is_migrated(self.os_conn, self.instances,
                                target=hypervisor_to.hypervisor_hostname),
            timeout_seconds=5 * 60,
            waiting_for='instances to migrate to '
                        '{0.hypervisor_hostname}'.format(hypervisor_to))

    def wait_hypervisor_be_free(self, hypervisor):
        hyp_id = hypervisor.id
        common.wait(
            lambda: (
                self.os_conn.nova.hypervisors.get(hyp_id).running_vms == 0),
            timeout_seconds=2 * 60,
            waiting_for='hypervisor info be updated')

    def wait_instances_to_be_ssh_available(self):
        predicates = [lambda: self.os_conn.is_server_ssh_ready(x)
                      for x in self.instances]
        common.wait(
            ALL(predicates),
            timeout_seconds=3 * 60,
            waiting_for='instances to be ssh available')


@pytest.mark.testrail_id('856599')
def test_image_access_host_device_when_resizing(os_conn,
                                                ubuntu_image_id,
                                                keypair,
                                                network,
                                                router,
                                                security_group):
    """Test to cover bugs #1552683 and #1548450 (CVE-2016-2140)

    1. Check use_cow_images=0 value in nova config on all computes
    2. Start instance with ephemeral disk
    3. umount /mnt in instance
    4. On instance create qcow2 image with baking_file
        linked to target host device in ephemeral block device
        something like: qemu-img create -f qcow2
        -o backing_file=/dev/sda3,backing_fmt=raw /dev/vdb 20G
    5. Change flavor or migrate instance

    EX: /vbd in instance should not be linked to host device

    Duration: 2-5 minutes
    """

    # change nova config on all computes and restart nova service
    nova_config_path = '/etc/nova/nova.conf'
    restart_cmd = 'service nova-compute restart'

    def wait_nova_alive():
        common.wait(os_conn.is_nova_ready,
                    timeout_seconds=60 * 3,
                    expected_exceptions=Exception,
                    waiting_for="Nova services to be alive")

    computes = os_conn.env.get_nodes_by_role('compute')

    for node in computes:
        with node.ssh() as remote:
            remote.check_call('cp {0} {0}.bak'.format(nova_config_path))

            parser = configparser.RawConfigParser()
            with remote.open(nova_config_path) as f:
                parser.readfp(f)
            parser.set('DEFAULT', 'use_cow_images', False)
            with remote.open(nova_config_path, 'w') as f:
                parser.write(f)
            remote.check_call(restart_cmd)

    wait_nova_alive()

    # create 2 flavors and spawn instance

    flavor_little = os_conn.nova.flavors.create(name='test-eph',
                                                ram=1024,
                                                vcpus=1,
                                                disk=5,
                                                ephemeral=1)

    flavor_large = os_conn.nova.flavors.create(name='test-eph-large',
                                               ram=2048,
                                               vcpus=1,
                                               disk=5,
                                               ephemeral=1)

    instance = os_conn.create_server(
        name='server-test-ubuntu',
        availability_zone='nova',
        key_name=keypair.name,
        image_id=ubuntu_image_id,
        flavor=flavor_little,
        nics=[{'net-id': network['network']['id']}],
        security_groups=[security_group.id],
        wait_for_active=False,
        wait_for_avaliable=False)

    common.wait(
        lambda: os_conn.is_server_active(instance),
        timeout_seconds=2 * 60,
        waiting_for="Instance to be in active status")

    common.wait(
        lambda: os_conn.is_server_ssh_ready(instance),
        timeout_seconds=5 * 60,
        waiting_for="Instance to be accessed via ssh")

    # validate + umount /mnt and create qcow image

    with os_conn.ssh_to_instance(os_conn.env,
                                 instance,
                                 vm_keypair=keypair,
                                 username='ubuntu') as remote:
        cmd_result = remote.execute('sudo lsblk')

        assert (cmd_result.is_ok is True), "lsblk command fail"
        assert ("/mnt" in cmd_result.stdout_string), "/mnt should be mounted"
        remote.check_call('sudo apt-get install -y qemu-utils && '
                          'sudo umount /mnt && '
                          'sudo qemu-img create -f qcow2 '
                          '-o backing_file=/dev/sda3,backing_fmt=raw '
                          '/dev/vdb 20G')

    # resize instance
    instance.resize(flavor_large)

    common.wait(
        lambda: os_conn.server_status_is(instance, 'VERIFY_RESIZE'),
        timeout_seconds=1 * 60,
        waiting_for='instance became to VERIFY_RESIZE status')

    instance.confirm_resize()

    common.wait(
        lambda: os_conn.is_server_ssh_ready(instance),
        timeout_seconds=1 * 60,
        waiting_for="Instance to be accessed via ssh")

    # validate /mnt is not mounted
    with os_conn.ssh_to_instance(os_conn.env,
                                 instance,
                                 vm_keypair=keypair,
                                 username='ubuntu') as remote:
        cmd_result = remote.execute('sudo lsblk')
        assert (cmd_result.is_ok is True), "lsblk command fail"
        assert ("/mnt" not in cmd_result.stdout_string),\
            "/mnt mounted,instance have access to host machine"

    # cleanup
    instance.force_delete()

    os_conn.nova.flavors.delete(flavor_little)
    os_conn.nova.flavors.delete(flavor_large)

    # restore configs
    for node in computes:
        with node.ssh() as remote:
            result = remote.execute('cp {0}.bak {0}'.format(nova_config_path))
            if result.is_ok:
                remote.check_call(restart_cmd)

    wait_nova_alive()


class TestLiveMigration(TestLiveMigrationBase):
    @pytest.mark.testrail_id('838028', block_migration=True)
    @pytest.mark.testrail_id('838257',
                             block_migration=False,
                             with_volume=False)
    @pytest.mark.testrail_id('838231', block_migration=False, with_volume=True)
    @pytest.mark.parametrize(
        'block_migration, with_volume',
        [(True, False), (False, False), (False, True)],
        ids=['block LM w/o vol', 'true LM w/o vol', 'true LM w vol'],
        indirect=['block_migration'])
    @pytest.mark.usefixtures('unlimited_live_migrations', 'cleanup_instances',
                             'cleanup_volumes')
    def test_live_migration_max_instances_with_all_flavors(
            self, big_hypervisors, block_migration, big_port_quota,
            with_volume):
        """LM of maximum allowed amount of instances created with all available
            flavors

        Scenario:
            1. Allow unlimited concurrent live migrations
            2. Restart nova-api services on controllers and
                nova-compute services on computes
            3. Create maximum allowed number of instances on a single
                compute node
            4. Initiate serial block LM of previously created instances
                to another compute node and estimate total time elapsed
            5. Check that all live-migrated instances are hosted on target host
                and are in Active state:
            6. Send pings between pairs of VMs to check that network
                connectivity between these hosts is still alive
            7. Initiate concurrent block LM of previously created instances
                to another compute node and estimate total time elapsed
            8. Check that all live-migrated instances are hosted on target host
                and are in Active state
            9. Send pings between pairs of VMs to check that network
                connectivity between these hosts is alive
            10. Repeat pp.3-9 for every available flavor
        """
        project_id = self.os_conn.session.get_project_id()
        image = self.os_conn._get_cirros_image()

        instances_create_args = []
        if with_volume:
            max_volumes = self.os_conn.cinder.quotas.get(project_id).volumes
            for i in range(max_volumes):
                vol = common.create_volume(self.os_conn.cinder,
                                           image['id'],
                                           size=10,
                                           timeout=5,
                                           name='volume_i'.format(i))
                self.volumes.append(vol)
                instances_create_args.append(
                    dict(block_device_mapping={'vda': vol.id}))

        zone = self.os_conn.nova.availability_zones.find(zoneName="nova")
        hypervisor1, hypervisor2 = big_hypervisors
        flavors = sorted(self.os_conn.nova.flavors.list(),
                         key=lambda x: -x.ram)
        for flavor in flavors:
            # Skip small flavors
            if flavor.ram < 512:
                continue

            instances_count = min(
                self.os_conn.get_hypervisor_capacity(hypervisor1, flavor),
                self.os_conn.get_hypervisor_capacity(hypervisor2, flavor))

            instance_zone = '{}:{}'.format(zone.zoneName,
                                           hypervisor1.hypervisor_hostname)
            if with_volume:
                instances_count = min(instances_count, max_volumes)
                create_args = instances_create_args[:instances_count]
            else:
                create_args = None
            self.create_instances(instance_zone,
                                  flavor,
                                  instances_count,
                                  create_args=create_args)

            self.successive_migration(block_migration,
                                      hypervisor_from=hypervisor1)

            self.wait_instances_to_be_ssh_available()

            self.wait_hypervisor_be_free(hypervisor1)

            self.concurrent_migration(block_migration,
                                      hypervisor_to=hypervisor1)

            self.wait_instances_to_be_ssh_available()

            self.wait_hypervisor_be_free(hypervisor2)

            self.delete_instances()

    @pytest.mark.testrail_id('838029', block_migration=True)
    @pytest.mark.testrail_id('838258', block_migration=False)
    @pytest.mark.usefixtures('unlimited_live_migrations', 'cleanup_instances',
                             'cleanup_volumes')
    @pytest.mark.parametrize('block_migration',
                             [True, False],
                             ids=['block LM', 'true LM'],
                             indirect=True)
    def test_live_migration_with_volumes(self, big_hypervisors,
                                         block_migration):
        """LM of instances with volumes attached

        Scenario:
            1. Allow unlimited concurrent live migrations
            2. Restart nova-api services on controllers and
                nova-compute services on computes
            3. Create maximum allowed number of instances with attached volumes
                on a single compute node
            4. Initiate serial block LM of previously created instances
                to another compute node
            5. Check that all live-migrated instances are hosted on target host
                and are in Active state:
            6. Send pings between pairs of VMs to check that network
                connectivity between these hosts is still alive
            7. Initiate concurrent block LM of previously created instances
                to another compute node
            8. Check that all live-migrated instances are hosted on target host
                and are in Active state
            9. Send pings between pairs of VMs to check that network
                connectivity between these hosts is alive
        """
        zone = self.os_conn.nova.availability_zones.find(zoneName="nova")
        hypervisor1, hypervisor2 = big_hypervisors
        flavor = sorted(self.os_conn.nova.flavors.list(),
                        key=lambda x: x.ram)[0]
        project_id = self.os_conn.session.get_project_id()
        max_volumes = self.os_conn.cinder.quotas.get(project_id).volumes
        instances_count = min(
            self.os_conn.get_hypervisor_capacity(hypervisor1, flavor),
            self.os_conn.get_hypervisor_capacity(hypervisor2, flavor),
            max_volumes)
        instance_zone = '{}:{}'.format(zone.zoneName,
                                       hypervisor1.hypervisor_hostname)
        self.create_instances(instance_zone, flavor, instances_count)

        image = self.os_conn._get_cirros_image()

        for instance in self.instances:
            vol = common.create_volume(self.os_conn.cinder,
                                       image['id'],
                                       size=1,
                                       timeout=5,
                                       name='{0.name}_volume'.format(instance))
            self.volumes.append(vol)
            self.os_conn.nova.volumes.create_server_volume(instance.id, vol.id)

        self.successive_migration(block_migration, hypervisor_from=hypervisor1)

        self.wait_instances_to_be_ssh_available()

        self.wait_hypervisor_be_free(hypervisor1)

        self.concurrent_migration(block_migration, hypervisor_to=hypervisor1)

        self.wait_instances_to_be_ssh_available()

        self.delete_instances()


class TestLiveMigrationUnderWorkload(TestLiveMigrationBase):

    memory_cmd = 'stress --vm-bytes 5M --vm-keep -m 1 <&- >/dev/null 2>&1 &'
    cpu_cmd = 'cpulimit -l 50 -- gzip -9 </dev/urandom >/dev/null 2>&1 &'
    hdd_cmd = """for i in {1..3}; do
        killall stress
        stress --hdd $i <&- >/dev/null 2>&1 &
        sleep 5
        util=$(iostat -d -x -y 5 1| grep '[hsv]d[abc]' | awk '{print $14}')
        echo "util is $util"
        if [ "$(echo $util'>95' | bc -l)" -eq "1" ]; then break; fi
    done"""

    def make_stress_instances(self,
                              ubuntu_image_id,
                              instances_count,
                              zone,
                              flavor=None):
        userdata = '\n'.join([
            '#!/bin/bash -v',
            'apt-get install -yq stress cpulimit sysstat iperf',
        ])
        flavor = flavor or self.os_conn.nova.flavors.find(name='m1.small')
        self.create_instances(zone=zone,
                              flavor=flavor,
                              instances_count=instances_count,
                              image_id=ubuntu_image_id,
                              userdata=userdata)

    @pytest.yield_fixture
    def stress_instance(self, ubuntu_image_id, block_migration):
        self.make_stress_instances(ubuntu_image_id,
                                   instances_count=1,
                                   zone='nova')
        instance = self.instances[0]
        yield instance
        self.delete_instances(force=True)

    @pytest.yield_fixture
    def stress_instances(self, ubuntu_image_id, block_migration,
                         big_hypervisors):
        hypervisor1, hypervisor2 = big_hypervisors
        flavor = self.os_conn.nova.flavors.find(name='m1.small')
        instances_count = min(
            self.os_conn.get_hypervisor_capacity(hypervisor1, flavor),
            self.os_conn.get_hypervisor_capacity(hypervisor2, flavor))
        instances_zone = 'nova:{0.hypervisor_hostname}'.format(hypervisor1)
        self.make_stress_instances(ubuntu_image_id,
                                   instances_count=instances_count,
                                   zone=instances_zone,
                                   flavor=flavor)
        yield self.instances
        self.delete_instances(force=True)

    @pytest.yield_fixture
    def iperf_instances(self, os_conn, keypair, security_group, network,
                        ubuntu_image_id, block_migration):
        userdata = '\n'.join([
            '#!/bin/bash -v',
            'apt-get install -yq iperf',
            'iperf -u -s -p 5002 <&- >/dev/null 2>&1 &',
        ])
        flavor = os_conn.nova.flavors.find(name='m1.small')
        self.create_instances(zone='nova',
                              flavor=flavor,
                              instances_count=2,
                              image_id=ubuntu_image_id,
                              userdata=userdata)
        yield self.instances
        self.delete_instances(force=True)

    @pytest.mark.testrail_id('838032', block_migration=True, cmd=memory_cmd)
    @pytest.mark.testrail_id('838261', block_migration=False, cmd=memory_cmd)
    @pytest.mark.testrail_id('838033', block_migration=True, cmd=cpu_cmd)
    @pytest.mark.testrail_id('838262', block_migration=False, cmd=cpu_cmd)
    @pytest.mark.testrail_id('838035', block_migration=True, cmd=hdd_cmd)
    @pytest.mark.testrail_id('838264', block_migration=False, cmd=hdd_cmd)
    @pytest.mark.parametrize('block_migration',
                             [True, False],
                             ids=['block LM', 'true LM'],
                             indirect=True)
    @pytest.mark.parametrize('cmd',
                             [memory_cmd, cpu_cmd, hdd_cmd],
                             ids=['memory', 'cpu', 'hdd'])
    @pytest.mark.usefixtures('router')
    def test_lm_with_workload(self, stress_instance, keypair, block_migration,
                              cmd):
        """LM of instance under memory workload

        Scenario:
            1. Boot an instance with Ubuntu image as a source and install
                the some stress utilities on it
            2. Generate a workload with executing command on instance
            3. Initiate live migration to another compute node
            4. Check that instance is hosted on another host and on ACTIVE
                status
            5. Check that network connectivity to instance is OK
        """
        with self.os_conn.ssh_to_instance(self.env,
                                          stress_instance,
                                          vm_keypair=keypair,
                                          username='ubuntu') as remote:
            remote.check_call(cmd)

        old_host = getattr(stress_instance, 'OS-EXT-SRV-ATTR:host')
        stress_instance.live_migrate(block_migration=block_migration)

        common.wait(
            lambda: is_migrated(self.os_conn, [stress_instance],
                                source=old_host),
            timeout_seconds=5 * 60,
            waiting_for='instance to migrate from {0}'.format(old_host))

        common.wait(lambda: self.os_conn.is_server_ssh_ready(stress_instance),
                    timeout_seconds=2 * 60,
                    waiting_for='instance to be available via ssh')

    @pytest.mark.testrail_id('838034', block_migration=True)
    @pytest.mark.testrail_id('838263', block_migration=False)
    @pytest.mark.parametrize('block_migration',
                             [True, False],
                             ids=['block LM', 'true LM'],
                             indirect=True)
    @pytest.mark.usefixtures('router')
    def test_lm_with_network_workload(self, iperf_instances, keypair,
                                      block_migration):
        """LM of instance under memory workload

        Scenario:
            1. Boot 2 instances with Ubuntu image as a source and install
                the iperf on it
            2. Start iperf server on first instance:
                iperf -u -s -p 5002
            2. Generate a workload with executing command on second instance:
                iperf --port 5002 -u --client <vm1_fixed_ip> --len 64 \
                --bandwidth 5M --time 60 -i 10
            3. Initiate live migration first instance to another compute node
            4. Check that instance is hosted on another host and on ACTIVE
                status
            5. Check that network connectivity to instance is OK
        """
        client, server = iperf_instances
        server_ip = self.os_conn.get_nova_instance_ips(server)['fixed']
        with self.os_conn.ssh_to_instance(self.env,
                                          client,
                                          vm_keypair=keypair,
                                          username='ubuntu') as remote:
            remote.check_call('iperf -u -c {ip} -p 5002 -t 240 --len 64'
                              '--bandwidth 5M <&- >/dev/null 2&>1 &'.format(
                                  ip=server_ip))

        old_host = getattr(server, 'OS-EXT-SRV-ATTR:host')
        server.live_migrate(block_migration=block_migration)

        common.wait(
            lambda: is_migrated(self.os_conn, [server],
                                source=old_host),
            timeout_seconds=5 * 60,
            waiting_for='instance to migrate from {0}'.format(old_host))

        common.wait(lambda: self.os_conn.is_server_ssh_ready(server),
                    timeout_seconds=2 * 60,
                    waiting_for='instance to be available via ssh')

    @pytest.mark.testrail_id('838037', block_migration=True)
    @pytest.mark.testrail_id('838265', block_migration=False)
    @pytest.mark.parametrize('block_migration',
                             [True, False],
                             ids=['block LM', 'true LM'],
                             indirect=True)
    @pytest.mark.usefixtures('router', 'unlimited_live_migrations')
    def test_lm_under_cpu_work_multi_instances(
            self, stress_instances, keypair, big_hypervisors, block_migration):
        """LM of multiple instances under CPU workload

        Scenario:
            1. Allow unlimited concurrent live migrations
            2. Restart nova-api services on controllers and
                nova-compute services on computes
            3. Create maximum allowed number of instances on a single compute
                node and install stress utilities on it
            4. Initiate serial block LM of previously created instances
                to another compute node
            5. Check that all live-migrated instances are hosted on target host
                and are in Active state:
            6. Send pings between pairs of VMs to check that network
                connectivity between these hosts is still alive
            7. Initiate concurrent block LM of previously created instances
                to another compute node
            8. Check that all live-migrated instances are hosted on target host
                and are in Active state
            9. Send pings between pairs of VMs to check that network
                connectivity between these hosts is alive
        """
        hypervisor1, _ = big_hypervisors
        for instance in self.instances:
            with self.os_conn.ssh_to_instance(self.env,
                                              instance,
                                              vm_keypair=keypair,
                                              username='ubuntu') as remote:
                remote.check_call(self.cpu_cmd)

        self.successive_migration(block_migration, hypervisor_from=hypervisor1)

        self.wait_instances_to_be_ssh_available()

        self.wait_hypervisor_be_free(hypervisor1)

        self.concurrent_migration(block_migration, hypervisor_to=hypervisor1)

        self.wait_instances_to_be_ssh_available()

    @pytest.mark.testrail_id('838038', block_migration=True)
    @pytest.mark.testrail_id('838266', block_migration=False)
    @pytest.mark.parametrize('block_migration',
                             [True, False],
                             ids=['block LM', 'true LM'],
                             indirect=True)
    @pytest.mark.usefixtures('router', 'unlimited_live_migrations')
    def test_lm_under_network_work_multi_instances(
            self, stress_instances, keypair, big_hypervisors, block_migration):
        """LM of multiple instances under CPU workload

        Scenario:
            1. Allow unlimited concurrent live migrations
            2. Restart nova-api services on controllers and
                nova-compute services on computes
            3. Create maximum allowed number of instances on a single compute
                node and install iperf utility on it
            4. Group instances to pairs and run iperf server on firsh instances
                on each pair:
                iperf -u -s -p 5002
            5. Launch iperf client on seconf instances in pairs:
                iperf --port 5002 -u --client <vm1_fixed_ip> --len 64 \
                --bandwidth 5M --time 60 -i 10
            4. Initiate serial block LM of previously created instances
                to another compute node
            5. Check that all live-migrated instances are hosted on target host
                and are in Active state:
            6. Send pings between pairs of VMs to check that network
                connectivity between these hosts is still alive
            7. Initiate concurrent block LM of previously created instances
                to another compute node
            8. Check that all live-migrated instances are hosted on target host
                and are in Active state
            9. Send pings between pairs of VMs to check that network
                connectivity between these hosts is alive
        """
        hypervisor1, _ = big_hypervisors
        clients = self.instances[::2]
        servers = self.instances[1::2]
        for server in servers:
            with self.os_conn.ssh_to_instance(self.env,
                                              server,
                                              vm_keypair=keypair,
                                              username='ubuntu') as remote:
                remote.check_call('iperf -u -s -p 5002 <&- >/dev/null 2>&1 &')

        if len(servers) < len(clients):
            servers.append(servers[-1])
        for client, server in zip(clients, servers):

            server_ip = self.os_conn.get_nova_instance_ips(server)['fixed']
            with self.os_conn.ssh_to_instance(self.env,
                                              client,
                                              vm_keypair=keypair,
                                              username='ubuntu') as remote:
                remote.check_call(
                    'iperf -u -c {ip} -p 5002 -t 240 --len 64 --bandwidth 5M '
                    '<&- >/dev/null 2>&1 &'.format(ip=server_ip))

        self.successive_migration(block_migration, hypervisor_from=hypervisor1)

        self.wait_instances_to_be_ssh_available()

        self.wait_hypervisor_be_free(hypervisor1)

        self.concurrent_migration(block_migration, hypervisor_to=hypervisor1)

        self.wait_instances_to_be_ssh_available()
