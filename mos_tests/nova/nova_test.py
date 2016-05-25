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

import os
from random import randint
import re
import subprocess
import tarfile
from time import sleep
from time import time

import logging
import paramiko
import pytest
import six

from mos_tests.environment.ssh import SSHClient
from mos_tests.functions.base import OpenStackTestCase
from mos_tests.functions import common as common_functions
from mos_tests.functions import file_cache
from mos_tests.functions import network_checks
from mos_tests.neutron.python_tests.base import TestBase
from mos_tests import settings


logger = logging.getLogger(__name__)


@pytest.mark.undestructive
class NovaIntegrationTests(OpenStackTestCase):
    """Basic automated tests for OpenStack Nova verification. """

    def setUp(self):
        super(self.__class__, self).setUp()

        self.instances = []
        self.floating_ips = []
        self.volumes = []
        self.flavors = []
        self.keys = []

        self.sec_group = self.nova.security_groups.create('security_nova',
                                                          'Security group, '
                                                          'created for Nova '
                                                          'automatic tests')
        rules = [
            {
                # ssh
                'ip_protocol': 'tcp',
                'from_port': 22,
                'to_port': 22,
                'cidr': '0.0.0.0/0',
            },
            {
                # ping
                'ip_protocol': 'icmp',
                'from_port': -1,
                'to_port': -1,
                'cidr': '0.0.0.0/0',
            }
        ]
        for rule in rules:
            self.nova.security_group_rules.create(self.sec_group.id, **rule)

    def tearDown(self):
        for inst in self.instances:
            common_functions.delete_instance(self.nova, inst)
        self.instances = []
        for fip in self.floating_ips:
            common_functions.delete_floating_ip(self.nova, fip)
        self.floating_ips = []
        for volume in self.volumes:
            self.os_conn.delete_volume(volume)
        self.volumes = []
        for flavor in self.flavors:
            common_functions.delete_flavor(self.nova, flavor.id)
        self.flavors = []
        for key in self.keys:
            common_functions.delete_keys(self.nova, key.name)
        self.keys = []
        self.nova.security_groups.delete(self.sec_group)

    @pytest.mark.check_env_("is_any_compute_suitable_for_max_flavor")
    @pytest.mark.testrail_id('543358')
    def test_nova_launch_v_m_from_image_with_all_flavours(self):
        """This test case checks creation of instance from image with all
        types of flavor. For this test we need node with compute role:
        8 VCPUs, 16+GB RAM and 160+GB disk for any compute

        Steps:
            1. Create a floating ip
            2. Create an instance from an image with some flavor
            3. Add the floating ip to the instance
            4. Ping the instance by the floating ip
            5. Delete the floating ip
            6. delete the instance
            7. Repeat all steps for all types of flavor
        """
        networks = self.neutron.list_networks()['networks']
        net = [net['id'] for net in networks
               if not net['router:external']][0]
        image_id = [image.id for image in self.nova.images.list() if
                    image.name == 'TestVM'][0]
        flavor_list = self.nova.flavors.list()
        for flavor in flavor_list:
            floating_ip = self.nova.floating_ips.create()
            self.floating_ips.append(floating_ip)
            self.assertIn(floating_ip.ip, [fip_info.ip for fip_info in
                                           self.nova.floating_ips.list()])
            inst = common_functions.create_instance(self.nova,
                                                    "inst_543358_{}"
                                                    .format(flavor.name),
                                                    flavor.id, net,
                                                    [self.sec_group.name],
                                                    image_id=image_id,
                                                    inst_list=self.instances)
            inst.add_floating_ip(floating_ip.ip)
            self.assertTrue(common_functions.check_ip(self.nova, inst.id,
                                                      floating_ip.ip))
            ping = common_functions.ping_command(floating_ip.ip)
            common_functions.delete_instance(self.nova, inst.id)
            self.assertTrue(ping, "Instance is not reachable")

    @pytest.mark.check_env_("is_any_compute_suitable_for_max_flavor")
    @pytest.mark.testrail_id('543360')
    def test_nova_launch_v_m_from_volume_with_all_flavours(self):
        """This test case checks creation of instance from volume with all
        types of flavor. For this test we need node with compute role:
        8 VCPUs, 16+GB RAM and 160+GB disk for any compute

        Steps:
            1. Create bootable volume
            1. Create a floating ip
            2. Create an instance from an image with some flavor
            3. Add the floating ip to the instance
            4. Ping the instance by the floating ip
            5. Delete the floating ip
            6. delete the instance
            7. Repeat all steps for all types of flavor
        """
        image_id = [image.id for image in self.nova.images.list() if
                    image.name == 'TestVM'][0]
        networks = self.neutron.list_networks()['networks']
        net = [net['id'] for net in networks if not net['router:external']][0]
        flavor_list = self.nova.flavors.list()
        volume = common_functions.create_volume(self.cinder, image_id)
        self.volumes.append(volume)
        bdm = {'vda': volume.id}
        for flavor in flavor_list:
            floating_ip = self.nova.floating_ips.create()
            self.floating_ips.append(floating_ip)
            self.assertIn(floating_ip.ip, [fip_info.ip for fip_info in
                                           self.nova.floating_ips.list()])
            inst = common_functions.create_instance(self.nova,
                                                    "inst_543360_{}"
                                                    .format(flavor.name),
                                                    flavor.id, net,
                                                    [self.sec_group.name],
                                                    block_device_mapping=bdm,
                                                    inst_list=self.instances)
            inst.add_floating_ip(floating_ip.ip)
            self.assertTrue(common_functions.check_ip(self.nova, inst.id,
                                                      floating_ip.ip))
            ping = common_functions.ping_command(floating_ip.ip)
            common_functions.delete_instance(self.nova, inst.id)
            self.assertTrue(ping, "Instance is not reachable")

    @pytest.mark.testrail_id('543355')
    def test_resize_down_an_instance_booted_from_volume(self):
        """This test checks that nova allows
            resize down an instance booted from volume
            Steps:
            1. Create bootable volume
            2. Boot instance from newly created volume
            3. Resize instance from m1.small to m1.tiny
        """

        # 1. Create bootable volume
        image_id = [image.id for image in self.nova.images.list() if
                    image.name == 'TestVM'][0]

        volume = common_functions.create_volume(self.cinder, image_id,
                                                timeout=60)
        self.volumes.append(volume)

        # 2. Create instance from newly created volume, associate floating_ip
        name = 'TestVM_543355_instance_to_resize'
        networks = self.neutron.list_networks()['networks']
        net = [net['id'] for net in networks if not net['router:external']][0]
        flavor_list = {f.name: f.id for f in self.nova.flavors.list()}
        initial_flavor = flavor_list['m1.small']
        resize_flavor = flavor_list['m1.tiny']
        bdm = {'vda': volume.id}
        instance = common_functions.create_instance(self.nova,
                                                    name, initial_flavor, net,
                                                    [self.sec_group.name],
                                                    block_device_mapping=bdm,
                                                    inst_list=self.instances)
        self.instances.append(instance.id)

        # Assert for attached volumes
        attached_volumes = self.nova.servers.get(instance).to_dict()[
            'os-extended-volumes:volumes_attached']
        self.assertIn({'id': volume.id}, attached_volumes)

        # Assert to flavor size
        self.assertEqual(self.nova.servers.get(instance).flavor['id'],
                         initial_flavor,
                         "Unexpected instance flavor before resize")

        floating_ip = self.nova.floating_ips.create()
        self.floating_ips.append(floating_ip.ip)
        instance.add_floating_ip(floating_ip.ip)

        # 3. Resize from m1.small to m1.tiny
        self.nova.servers.resize(instance, resize_flavor)
        common_functions.check_inst_status(self.nova, instance.id,
                                           'VERIFY_RESIZE', 60)
        self.nova.servers.confirm_resize(instance)
        common_functions.check_inst_status(self.nova, instance.id,
                                           'ACTIVE', 60)
        self.assertEqual(self.nova.servers.get(instance).flavor['id'],
                         resize_flavor,
                         "Unexpected instance flavor after resize")

        # Check that instance is reachable
        ping = common_functions.ping_command(floating_ip.ip)
        self.assertTrue(ping, "Instance after resize is not reachable")

    @pytest.mark.testrail_id('543359')
    def test_massively_spawn_volumes(self):
        """This test checks massively spawn volumes

        Steps:
            1. Create 10 volumes
            2. Check status of newly created volumes
            3. Delete all volumes
        """
        volume_count = 10
        volumes = []

        # Creation using Cinder
        for num in range(volume_count):
            volumes.append(
                self.cinder.volumes.create(
                    1, name='Volume_{}'.format(num + 1)))
        self.volumes.extend(volumes)

        for volume in self.cinder.volumes.list():
            self.assertTrue(
                common_functions.check_volume_status(self.cinder, volume.id,
                                                     'available', 60),
                "Volume '{0}' is not available".format(volume.id))

    @pytest.mark.testrail_id('543356')
    def test_nova_massively_spawn_v_ms_with_boot_local(self):
        """This test case creates a lot of VMs with boot local, checks it
        state and availability and then deletes it.

        Steps:
            1. Boot 10-100 instances from image.
            2. Check that list of instances contains created VMs.
            3. Check state of created instances
            4. Add the floating ips to the instances
            5. Ping the instances by the floating ips
        """
        initial_instances = self.nova.servers.list()
        primary_name = "testVM_543356"
        count = 10
        image_dict = {im.name: im.id for im in self.nova.images.list()}
        image_id = image_dict["TestVM"]
        flavor_dict = {f.name: f.id for f in self.nova.flavors.list()}
        flavor_id = flavor_dict["m1.micro"]
        networks = self.neutron.list_networks()["networks"]
        net_dict = {net["name"]: net["id"] for net in networks}
        net_internal_id = net_dict["admin_internal_net"]

        self.floating_ips = [self.nova.floating_ips.create()
                             for _ in range(count)]
        fip_new = [fip_info.ip for fip_info in self.floating_ips]
        fip_all = [fip_info.ip for fip_info in self.nova.floating_ips.list()]
        for fip in fip_new:
            self.assertIn(fip, fip_all)

        self.nova.servers.create(primary_name, image_id, flavor_id,
                                 max_count=count,
                                 security_groups=[self.sec_group.name],
                                 nics=[{"net-id": net_internal_id}])
        start_time = time()
        timeout = 5
        while len(self.nova.servers.list()) < len(initial_instances) + count \
                and time() < start_time + timeout * 60:
            sleep(5)

        instances = [inst for inst in self.nova.servers.list()
                     if inst not in initial_instances]
        self.instances = [inst.id for inst in instances]
        for inst_id in self.instances:
            self.assertTrue(common_functions.check_inst_status(self.nova,
                                                               inst_id,
                                                               'ACTIVE'))
        fip_dict = {}
        for inst in instances:
            fip = fip_new.pop()
            inst.add_floating_ip(fip)
            fip_dict[inst.id] = fip

        for inst_id in self.instances:
            self.assertTrue(common_functions.check_ip(
                self.nova, inst_id, fip_dict[inst_id]))

        for inst_id in self.instances:
            ping = common_functions.ping_command(fip_dict[inst_id], i=8)
            self.assertTrue(ping,
                            "Instance {} is not reachable".format(inst_id))

    @pytest.mark.testrail_id('543357')
    def test_nova_massively_spawn_v_ms_boot_from_cinder(self):
        """This test case creates a lot of VMs which boot from Cinder, checks
        it state and availability and then deletes it.

        Steps:
            1. Create 10-100 volumes.
            2. Boot 10-100 instances from volumes.
            3. Check that list of instances contains created VMs.
            4. Check state of created instances
            5. Add the floating ips to the instances
            6. Ping the instances by the floating ips
        """
        initial_instances = self.nova.servers.list()
        count = 10
        primary_name = "testVM_543357"
        image_dict = {im.name: im.id for im in self.nova.images.list()}
        image_id = image_dict["TestVM"]
        flavor_dict = {f.name: f.id for f in self.nova.flavors.list()}
        flavor_id = flavor_dict["m1.tiny"]
        networks = self.neutron.list_networks()["networks"]
        net_dict = {net["name"]: net["id"] for net in networks}
        net_internal_id = net_dict["admin_internal_net"]

        initial_volumes = self.cinder.volumes.list()
        for i in range(count):
            common_functions.create_volume(self.cinder, image_id, size=1)
        self.volumes = [volume for volume in self.cinder.volumes.list()
                        if volume not in initial_volumes]
        msg = "Count of created volumes is incorrect!"
        self.assertEqual(len(self.volumes), 10, msg)

        self.floating_ips = [self.nova.floating_ips.create()
                             for _ in range(count)]
        fip_new = [fip_info.ip for fip_info in self.floating_ips]
        fip_all = [fip_info.ip for fip_info in self.nova.floating_ips.list()]
        for fip in fip_new:
            self.assertIn(fip, fip_all)

        for volume in self.volumes:
            bdm = {'vda': volume.id}
            self.nova.servers.create(primary_name, '', flavor_id,
                                     security_groups=[self.sec_group.name],
                                     block_device_mapping=bdm,
                                     nics=[{"net-id": net_internal_id}])
        start_time = time()
        timeout = 5
        while len(self.nova.servers.list()) < len(initial_instances) + count \
                and time() < start_time + timeout * 60:
            sleep(5)

        instances = [inst for inst in self.nova.servers.list()
                     if inst not in initial_instances]
        self.instances = [inst.id for inst in instances]
        for inst_id in self.instances:
            self.assertTrue(common_functions.check_inst_status(self.nova,
                                                               inst_id,
                                                               'ACTIVE'))
        fip_dict = {}
        for inst in instances:
            fip = fip_new.pop()
            inst.add_floating_ip(fip)
            fip_dict[inst.id] = fip

        for inst_id in self.instances:
            self.assertTrue(common_functions.check_ip(
                self.nova, inst_id, fip_dict[inst_id]))

        for inst_id in self.instances:
            ping = common_functions.ping_command(fip_dict[inst_id], i=8)
            self.assertTrue(ping,
                            "Instance {} is not reachable".format(inst_id))

    @pytest.mark.testrail_id('542823')
    def test_network_connectivity_to_v_m_during_live_migration(self):
        """This test checks network connectivity to VM during Live Migration

            Steps:
             1. Create a floating ip
             2. Create an instance from an image with 'm1.micro' flavor
             3. Add the floating ip to the instance
             4. Ping the instance by the floating ip
             5. Execute live migration
             6. Check current hypervisor and status of instance
             7. Check that packets loss was minimal
        """
        networks = self.neutron.list_networks()['networks']
        net = [net['id'] for net in networks if not net['router:external']][0]
        image_id = [image.id for image in self.nova.images.list() if
                    image.name == 'TestVM'][0]
        flavor = [flavor for flavor in self.nova.flavors.list() if
                  flavor.name == 'm1.micro'][0]
        floating_ip = self.nova.floating_ips.create()
        self.floating_ips.append(floating_ip)
        self.assertIn(floating_ip.ip, [fip_info.ip for fip_info in
                                       self.nova.floating_ips.list()])
        inst = common_functions.create_instance(self.nova,
                                                "inst_2238776_{}"
                                                .format(flavor.name),
                                                flavor.id, net,
                                                [self.sec_group.name],
                                                image_id=image_id,
                                                inst_list=self.instances)
        self.instances.append(inst.id)
        inst.add_floating_ip(floating_ip.ip)
        ping = common_functions.ping_command(floating_ip.ip)
        self.assertTrue(ping, "Instance is not reachable")

        self.live_migration(inst, floating_ip.ip)

    @pytest.mark.testrail_id('542824')
    def test_live_migration_of_v_ms_with_data_on_root_and_ephemeral_disk(self):
        """This test checks Live Migration of VMs with data on root and
        ephemeral disk

            Steps:
             1. Create flavor with ephemeral disk
             2. Create a floating ip
             3. Create an instance from an image with 'm1.ephemeral' flavor
             4. Add the floating ip to the instance
             5. Ssh to instance and create timestamp on root and ephemeral
                disks
             6. Ping the instance by the floating ip
             7. Execute live migration
             8. Check current hypervisor and status of instance
             9. Check that packets loss was minimal
             10. Ssh to instance and check timestamp on root and ephemeral
                 disks
        """
        networks = self.neutron.list_networks()['networks']
        net = [net['id'] for net in networks if not net['router:external']][0]
        image_id = [image.id for image in self.nova.images.list() if
                    image.name == 'TestVM'][0]
        flavor = self.nova.flavors.create("m1.ephemeral", 64, 1, 1,
                                          ephemeral=1, is_public=True)
        self.flavors.append(flavor)
        floating_ip = self.nova.floating_ips.create()
        self.floating_ips.append(floating_ip)
        self.assertIn(floating_ip.ip, [fip_info.ip for fip_info in
                                       self.nova.floating_ips.list()])
        keys = self.nova.keypairs.create('key_2238776')
        self.keys.append(keys)
        private_key = paramiko.RSAKey.from_private_key(six.StringIO(str(
            keys.private_key)))
        inst = common_functions.create_instance(self.nova,
                                                "inst_2238776_{}"
                                                .format(flavor.name),
                                                flavor.id, net,
                                                [self.sec_group.name],
                                                image_id=image_id,
                                                key_name='key_2238776',
                                                inst_list=self.instances)
        self.instances.append(inst.id)
        inst.add_floating_ip(floating_ip.ip)
        ping = common_functions.ping_command(floating_ip.ip, i=10)
        self.assertTrue(ping, "Instance is not reachable")
        out = []
        with SSHClient(host=floating_ip.ip, username="cirros", password=None,
                       private_keys=[private_key]) as vm_r:
            out.append(vm_r.execute("sudo sh -c 'date > /timestamp.txt'"))
            out.append(vm_r.execute("sudo sh -c 'date > /mnt/timestamp.txt'"))
            out.append(vm_r.execute("sudo -i cat /timestamp.txt"))
            out.append(vm_r.execute("sudo -i cat /mnt/timestamp.txt"))

        for i in out:
            if i.get('stderr'):
                raise Exception("ssh commands were executed with errors")

        root_data = out[-2]['stdout'][0]
        ephem_data = out[-1]['stdout'][0]

        self.live_migration(inst, floating_ip.ip)

        out = []
        with SSHClient(host=floating_ip.ip, username="cirros", password=None,
                       private_keys=[private_key]) as vm_r:
            out.append(vm_r.execute("sudo -i cat /timestamp.txt"))
            out.append(vm_r.execute("sudo -i cat /mnt/timestamp.txt"))

        for i in out:
            if i.get('stderr'):
                raise Exception("ssh commands were executed with errors")

        r_data = out[0]['stdout'][0]
        ep_data = out[1]['stdout'][0]
        self.assertEqual(root_data, r_data, "Data on root disk is changed")
        self.assertEqual(ephem_data, ep_data, "Data on ephemeral disk is "
                                              "changed")

    def live_migration(self, instance, ip_to_ping, timeout=20):
        hypervisors = {h.hypervisor_hostname: h for h in
                       self.nova.hypervisors.list()}
        old_hyper = getattr(instance, "OS-EXT-SRV-ATTR:hypervisor_hostname")
        new_hyper = [h for h in hypervisors.keys() if h != old_hyper][0]
        # Start ping of the vm in background
        ping = subprocess.Popen(["/bin/ping", "-c20", "-i1", ip_to_ping],
                                stdout=subprocess.PIPE)
        # Then run the migration
        self.nova.servers.live_migrate(instance, new_hyper,
                                       block_migration=True,
                                       disk_over_commit=False)

        # Check that migration is over, usually it takes about 10-15 seconds
        def instance_hypervisor():
            instance.get()
            return getattr(instance, "OS-EXT-SRV-ATTR:hypervisor_hostname")

        common_functions.wait(lambda: instance_hypervisor() == new_hyper,
                              timeout_seconds=timeout * 60,
                              waiting_for='instance hypervisor to be changed')
        self.assertEqual(instance.status, 'ACTIVE')

        # Now wait till background ping is over
        ping.wait()
        # And check that vm was reachable during migration
        output = re.search(r'(\d+)% packet loss', ping.stdout.read())
        loss = int(output.group(1))
        if loss > 90:
            msg = "Packets loss during migration {}% exceeds the 90% limit"
            raise AssertionError(msg.format(loss))

        # And now sure that vm is stable after the migration
        ping = subprocess.Popen(["/bin/ping", "-c300", "-i0.4",
                                ip_to_ping], stdout=subprocess.PIPE)
        ping.wait()
        output = re.search('([0-9]+)% packet loss', ping.stdout.read())
        loss = int(output.group(1))
        if loss > 10:
            msg = "Packets loss during stability {}% exceeds the 10% limit"
            raise AssertionError(msg.format(loss))

    @pytest.mark.testrail_id('843882')
    def test_boot_instance_from_volume_bigger_than_flavor_size(self):
        """This test checks that nova allows creation instance
            from volume with size bigger than flavor size
            Steps:
            1. Create volume with size 2Gb.
            2. Boot instance with flavor size 'tiny' from newly created volume
            3. Check that instance created with correct values
        """

        # 1. Create volume
        image_id = [image.id for image in self.nova.images.list() if
                    image.name == 'TestVM'][0]

        volume = common_functions.create_volume(self.cinder, image_id,
                                                size=2, timeout=60)
        self.volumes.append(volume)

        # 2. Create router, network, subnet, connect them to external network
        exist_networks = self.os_conn.list_networks()['networks']
        ext_network = [x for x in exist_networks
                       if x.get('router:external')][0]
        self.router = self.os_conn.create_router(name="router01")['router']
        self.os_conn.router_gateway_add(router_id=self.router['id'],
                                        network_id=ext_network['id'])
        net_id = self.os_conn.add_net(self.router['id'])

        # 3. Create instance from newly created volume, associate floating_ip
        name = 'TestVM_1517671_instance'
        flavor_list = {f.name: f.id for f in self.nova.flavors.list()}
        initial_flavor_id = flavor_list['m1.tiny']
        bdm = {'vda': volume.id}
        instance = common_functions.create_instance(self.nova, name,
                                                    initial_flavor_id, net_id,
                                                    [self.sec_group.name],
                                                    block_device_mapping=bdm,
                                                    inst_list=self.instances)
        self.instances.append(instance.id)

        # Assert for attached volumes
        attached_volumes = self.nova.servers.get(instance).to_dict()[
            'os-extended-volumes:volumes_attached']
        self.assertIn({'id': volume.id}, attached_volumes)

        # Assert to flavor size
        self.assertEqual(self.nova.servers.get(instance).flavor['id'],
                         initial_flavor_id,
                         "Unexpected instance flavor after creation")

        floating_ip = self.nova.floating_ips.create()
        self.floating_ips.append(floating_ip.ip)
        instance.add_floating_ip(floating_ip.ip)

        # Check that instance is reachable
        ping = common_functions.ping_command(floating_ip.ip)
        self.assertTrue(ping, "Instance after creation is not reachable")


@pytest.mark.undestructive
class TestNovaBasicFunctionality(TestBase):
    """Basic automated tests for OpenStack Nova verification. """

    @classmethod
    @pytest.yield_fixture
    def cleanup_instance(cls, os_conn, security_group):
        logger.info('Store instance list before test')
        instances = os_conn.nova.servers.list()
        yield

        new_instances = [i for i in os_conn.nova.servers.list()
                         if i not in instances]
        logger.info('Delete instance if created')
        for inst in new_instances:
            inst.delete()
        os_conn.wait_servers_deleted(new_instances)

    @classmethod
    @pytest.yield_fixture
    def arch_cirros_image_id(cls, os_conn):
        logger.info('Creating Cirros image from archive')
        image = os_conn.glance.images.create(name="cirros_image_{}"
                                             .format(str(randint(1, 1000))),
                                             disk_format='qcow2',
                                             visibility='public',
                                             container_format='bare')

        with file_cache.get_file(settings.GLANCE_IMAGE_URL) as img_file:
            archive = os.path.join(settings.TEST_IMAGE_PATH, "Cirros.tar.gz")
            with tarfile.open(archive, "w:gz") as tar_img:
                tar_img.add(img_file.name)
                tar_img.close()
            with open(archive, 'rb') as tar_file:
                os_conn.glance.images.upload(image.id, tar_file)

        # Check that status of image is 'active'
        assert os_conn.glance.images.get(image.id)['status'] == 'active'
        logger.info('Cirros image created')

        yield image.id
        os_conn.glance.images.delete(image.id)

    @pytest.mark.testrail_id('851850')
    def test_launch_instance_from_image_created_from_archive(
            self, arch_cirros_image_id, security_group, cleanup_instance):
        """This test checks that it is possible to create image
            from archive(tar.gz) and nova allows launch instance from it

            Steps:
            1. Create archive with img file
            2. Create image from archive
            3. Boot instance from newly created image
            4. Add the floating ip to the instance
            5. Ping the instance by the floating ip
            6. Delete the floating ip, created image, instance
        """

        networks = self.os_conn.neutron.list_networks()['networks']
        logger.info("Try to find internal network")
        int_net = [n['id'] for n in networks if not n['router:external']][0]

        inst = self.os_conn.create_server(
            name="851850_inst_{}".format(str(randint(1, 1000))),
            image_id=arch_cirros_image_id,
            availability_zone='nova',
            wait_for_avaliable=False,
            security_groups=[security_group.id],
            nics=[{'net-id': int_net}])
        logger.info('Instance launched')

        # Add IP and ping the instance
        floating_ip = self.os_conn.nova.floating_ips.create()
        inst.add_floating_ip(floating_ip.ip)
        ping = common_functions.ping_command(floating_ip.ip)

        # Delete floating IP
        self.os_conn.nova.floating_ips.delete(floating_ip)

        logger.info('Check that instance is reachable')
        assert ping is True


@pytest.mark.undestructive
class TestNovaDeferredDelete(TestBase):
    """Nova Deferred Delete test cases"""
    recl_interv_long = 24 * 60 * 60  # seconds
    recl_interv_short = 30           # seconds

    @classmethod
    @pytest.yield_fixture
    def volumes(cls, os_conn):
        """Volumes cleanUp"""
        volumes = []
        yield volumes
        for volume in volumes:
            os_conn.delete_volume(volume)

    @pytest.mark.testrail_id('842493')
    @pytest.mark.parametrize(
        'set_recl_inst_interv', [recl_interv_long], indirect=True)
    def test_restore_deleted_instance(
            self, set_recl_inst_interv, instances, volumes):
        """Restore previously deleted instance.
        Actions:
        1. Update '/etc/nova/nova.conf' with 'reclaim_instance_interval=86400'
        and restart Nova on all nodes;
        2. Create net and subnet;
        3. Create and run two instances (vm1, vm2) inside same net;
        4. Check that ping are successful between vms;
        5. Create a volume and attach it to an instance vm1;
        6. Delete instance vm1 and check that it's in 'SOFT_DELETE' state;
        7. Restore vm1 instance and check that it's in 'ACTIVE' state;
        8. Check that ping are successful between vms;
        """
        timeout = 60  # (sec) timeout to wait instance for status change

        # Create two vms
        vm1, vm2 = instances

        # Ping one vm from another
        vm1_ip = self.os_conn.get_nova_instance_ips(vm1).values()[0]
        vm2_ip = self.os_conn.get_nova_instance_ips(vm2).values()[0]
        network_checks.check_ping_from_vm(
            self.env, self.os_conn, vm1, ip_to_ping=vm2_ip, timeout=60)

        # Create a volume and attach it to an instance vm1
        volume = common_functions.create_volume(
            self.os_conn.cinder, image_id=None)
        self.os_conn.nova.volumes.create_server_volume(
            server_id=vm1.id, volume_id=volume.id, device='/dev/vdb')
        volumes.append(volume)

        # Delete instance vm1 and check that it's in "SOFT_DELETED" state
        common_functions.delete_instance(self.os_conn.nova, vm1.id)
        assert vm1 not in self.os_conn.get_servers()
        common_functions.wait(
            lambda: self.os_conn.server_status_is(vm1, 'SOFT_DELETED'),
            timeout_seconds=timeout, sleep_seconds=5,
            waiting_for='instance {0} changes status to SOFT_DELETED'.format(
                vm1.name))

        # Restore vm1 instance and check that it's in "ACTIVE" state now
        resp = self.os_conn.nova.servers.restore(vm1.id)
        assert resp[0].ok
        common_functions.wait(
            lambda: self.os_conn.is_server_active(vm1.id),
            timeout_seconds=timeout, sleep_seconds=5,
            waiting_for='instance {0} changes status to ACTIVE'.format(
                vm1.name))

        # Ping one vm from another
        network_checks.check_ping_from_vm(
            self.env, self.os_conn, vm2, ip_to_ping=vm1_ip, timeout=60)

    @pytest.mark.testrail_id('842494')
    @pytest.mark.parametrize(
        'set_recl_inst_interv', [recl_interv_short], indirect=True)
    def test_inst_deleted_reclaim_interval_timeout(
            self, set_recl_inst_interv, instances, volumes):
        """Check that softly-deleted instance is totally deleted after
        reclaim interval timeout.
        Actions:
        1. Update '/etc/nova/nova.conf' with short 'reclaim_instance_interval'
        and restart Nova on all nodes;
        2. Create net and subnet;
        3. Create and run two instances (vm1, vm2) inside same net;
        4. Create a volume and attach it to an instance vm1;
        5. Delete instance vm1 and check that it's in 'SOFT_DELETE' state;
        6. Wait for the reclaim instance interval to expire and make sure
        the vm1 is deleted;
        7. Check that volume is released now and has an Available state;
        8. Attach the volume to vm2 instance to ensure that the volume's reuse
        doesn't call any errors.

        ~! BUG !~
        https://bugs.launchpad.net/cinder/+bug/1463856
        Cinder volume isn't available after instance soft-deleted timer
        expired while volume is still attached.
        """
        timeout = 60  # (sec) timeout to wait instance for status change

        # Create two vms
        vm1, vm2 = instances

        # Create a volume and attach it to an instance vm1
        volume = common_functions.create_volume(
            self.os_conn.cinder, image_id=None)
        self.os_conn.nova.volumes.create_server_volume(
            server_id=vm1.id, volume_id=volume.id, device='/dev/vdb')
        volumes.append(volume)

        # Delete instance vm1 and check that it's in "SOFT_DELETED" state
        common_functions.delete_instance(self.os_conn.nova, vm1.id)
        assert vm1 not in self.os_conn.get_servers()
        common_functions.wait(
            lambda: self.os_conn.server_status_is(vm1, 'SOFT_DELETED'),
            timeout_seconds=timeout, sleep_seconds=5,
            waiting_for='instance {0} changes status to SOFT_DELETED'.format(
                vm1.name))

        # Wait interval and check that instance is not present
        time_to_sleep = 2.5 * self.recl_interv_short
        logger.debug(('Sleep to wait for 2.5 reclaim_instance_interval ({0})'
                      ).format(time_to_sleep))
        sleep(time_to_sleep)
        try:
            self.os_conn.get_instance_detail(vm1.id)
        except Exception as e:
            assert e.code == 404
        else:
            raise Exception(('Instance {0} not deleted after '
                             '"reclaim_interval_timeout"').format(vm1.name))

        # Update volume information
        volume = self.os_conn.cinder.volumes.get(volume.id)

        # ~! BUG !~: https://bugs.launchpad.net/cinder/+bug/1463856
        # Check that volume is released now and has an Available state
        assert volume.status == 'available'
        # Check volume is not attached
        assert volume.attachments == []

        # Attach the volume to vm2 instance
        self.os_conn.nova.volumes.create_server_volume(
            server_id=vm2.id, volume_id=volume.id, device='/dev/vdb')

        # Check volume status after re-attach
        assert self.os_conn.cinder.volumes.get(volume.id).status == 'in-use'

    @pytest.mark.testrail_id('842495')
    @pytest.mark.parametrize(
        'set_recl_inst_interv', [recl_interv_long], indirect=True)
    def test_force_delete_inst_before_deferred_cleanup(
            self, set_recl_inst_interv, instances, volumes):
        """Force delete of instance before deferred cleanup
        Actions:
        1. Update '/etc/nova/nova.conf' with long 'reclaim_instance_interval'
        and restart Nova on all nodes;
        2. Create net and subnet;
        3. Create and run two instances (vm1, vm2) inside same net;
        4. Create a volume and attach it to an instance vm1;
        5. Delete instance vm1 and check that it's in 'SOFT_DELETE' state;
        6. Delete instance vm1 with 'force' option and check that it's not
        present.
        7. Check that volume is released now and has an Available state;
        8. Attach the volume to vm2 instance to ensure that the volume's reuse
        doesn't call any errors.
        """
        timeout = 60  # (sec) timeout to wait instance for status change

        # Create two vms
        vm1, vm2 = instances

        # Create a volume and attach it to an instance vm1
        volume = common_functions.create_volume(
            self.os_conn.cinder, image_id=None)
        self.os_conn.nova.volumes.create_server_volume(
            server_id=vm1.id, volume_id=volume.id, device='/dev/vdb')
        volumes.append(volume)

        # Delete instance vm1 and check that it's in "SOFT_DELETED" state
        common_functions.delete_instance(self.os_conn.nova, vm1.id)
        assert vm1 not in self.os_conn.get_servers()
        common_functions.wait(
            lambda: self.os_conn.server_status_is(vm1, 'SOFT_DELETED'),
            timeout_seconds=timeout, sleep_seconds=5,
            waiting_for='instance {0} changes status to SOFT_DELETED'.format(
                vm1.name))

        # Force delete and check vm1 not present
        common_functions.delete_instance(self.os_conn.nova, vm1.id, force=True)
        common_functions.wait(
            lambda: self.os_conn.is_server_deleted(vm1.id),
            timeout_seconds=timeout, sleep_seconds=5,
            waiting_for='instance {0} to be forced deleted'.format(vm1.name))

        # Check that volume is released now and has an Available state
        assert common_functions.check_volume_status(
            self.os_conn.cinder, volume.id, 'available', 1)

        # Check volume is not attached
        assert self.os_conn.cinder.volumes.get(volume.id).attachments == []

        # Attach the volume to vm2 instance
        self.os_conn.nova.volumes.create_server_volume(
            server_id=vm2.id, volume_id=volume.id, device='/dev/vdb')
        # Check volume status is 'in-use' after re-attach
        assert common_functions.check_volume_status(
            self.os_conn.cinder, volume.id, 'in-use', 1)
        # Check that volume has correct server id
        volume = self.os_conn.cinder.volumes.get(volume.id)
        assert volume.attachments[0]['server_id'] == vm2.id
