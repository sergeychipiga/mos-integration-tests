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

import functools
import pytest
import uuid

from mos_tests.functions import common
from mos_tests.functions import os_cli
from mos_tests.murano import actions


flavor = 'm1.medium'
docker_image = 'ubuntu14.04-x64-docker'
kubernetes_image = 'ubuntu14.04-x64-kubernetes'
labels = "testkey=testvalue"


@pytest.fixture
def murano(os_conn):
    return actions.MuranoActions(os_conn)


@pytest.yield_fixture
def controller_remote(env):
    with env.get_nodes_by_role('controller')[0].ssh() as remote:
        yield remote


@pytest.fixture
def openstack_client(controller_remote):
    return os_cli.OpenStack(controller_remote)


@pytest.yield_fixture
def environment(murano, package):
    environment = murano.murano.environments.create(
        {'name': murano.rand_name('MuranoEnv')})
    yield environment
    murano.murano.environments.delete(environment.id, abandon=True)
    murano.delete_stacks(environment.id)


@pytest.yield_fixture
def session(murano, environment):
    session = murano.murano.sessions.configure(environment.id)
    yield session
    murano.murano.sessions.delete(environment.id, session.id)


@pytest.yield_fixture
def keypair(os_conn):
    keypair = os_conn.create_key(key_name='murano-key')
    yield keypair
    os_conn.delete_key(key_name=keypair.name)


@pytest.fixture
def murano_cli(controller_remote):
    return functools.partial(os_cli.Murano(controller_remote))


@pytest.fixture
def kubernetespod(murano_cli):
    fqn = 'io.murano.apps.docker.kubernetes.KubernetesPod'
    packages = murano_cli(
        'package-import',
        params='{0} --exists-action s'.format(fqn),
        flags='--murano-repo-url=http://storage.apps.openstack.org').listing()
    package = [x for x in packages if x['FQN'] == fqn][0]
    return package


@pytest.fixture
def package(murano_cli, os_conn, request):
    package_names = getattr(request, 'param', ('DockerGrafana',))
    for name in package_names:
        if 'Docker' in name:
            name = 'apps.docker.{}'.format(name)
        elif 'Apache' in name:
            name = 'apps.apache.{}'.format(name)
        murano_cli('package-import',
                   params='io.murano.{} --exists-action s'.format(name),
                   flags='--murano-repo-url=http://storage.apps.openstack.'
                         'org').listing()


@pytest.fixture
def docker(murano, keypair, environment, session):
    docker_data = {
        "instance": {
            "name": murano.rand_name("Docker"),
            "assignFloatingIp": True,
            "keyname": keypair.name,
            "flavor": flavor,
            "image": docker_image,
            "availabilityZone": 'nova',
            "?": {
                "type": "io.murano.resources.LinuxMuranoInstance",
                "id": str(uuid.uuid4())
            },
        },
        "name": "DockerVM",
        "?": {
            "_{id}".format(id=uuid.uuid4().hex): {
                "name": "Docker VM Service"
            },
            "type": "io.murano.apps.docker.DockerStandaloneHost",
            "id": str(uuid.uuid4())
        }
    }
    docker = murano.create_service(environment, session, docker_data)
    return docker


@pytest.fixture
def cluster(murano, keypair, environment, session, request):
    nodes = getattr(request, 'param', {'initial_gateways': 1,
                                       'max_gateways': 1, 'initial_nodes': 1,
                                       'max_nodes': 1, 'cadvisor': True})

    if nodes['max_gateways'] == 1:
        gateways_data = [
            {
                "instance": {
                    "name": "gateway-1",
                    "assignFloatingIp": True,
                    "keyname": keypair.name,
                    "flavor": flavor,
                    "image": kubernetes_image,
                    "availabilityZone": 'nova',
                    "?": {
                        "type": "io.murano.resources.LinuxMuranoInstance",
                        "id": str(uuid.uuid4())
                    }
                },
                "?": {
                    "type": "io.murano.apps.docker.kubernetes."
                            "KubernetesGatewayNode",
                    "id": str(uuid.uuid4())
                }
            }
        ]
    else:
        gateways_data = [
            {
                "instance": {
                    "name": "gateway-1",
                    "assignFloatingIp": True,
                    "keyname": keypair.name,
                    "flavor": flavor,
                    "image": kubernetes_image,
                    "availabilityZone": 'nova',
                    "?": {
                        "type": "io.murano.resources.LinuxMuranoInstance",
                        "id": str(uuid.uuid4())
                    }
                },
                "?": {
                    "type": "io.murano.apps.docker.kubernetes."
                            "KubernetesGatewayNode",
                    "id": str(uuid.uuid4())
                }
            },
            {
                "instance": {
                    "name": "gateway-2",
                    "assignFloatingIp": True,
                    "keyname": keypair.name,
                    "flavor": flavor,
                    "image": kubernetes_image,
                    "availabilityZone": 'nova',
                    "?": {
                        "type": "io.murano.resources.LinuxMuranoInstance",
                        "id": str(uuid.uuid4())
                    }
                },
                "?": {
                    "type": "io.murano.apps.docker.kubernetes."
                            "KubernetesGatewayNode",
                    "id": str(uuid.uuid4())
                }
            }
        ]
    if nodes['max_nodes'] == 1:
        nodes_data = [
            {
                "instance": {
                    "name": "minion-1",
                    "assignFloatingIp": True,
                    "keyname": keypair.name,
                    "flavor": flavor,
                    "image": kubernetes_image,
                    "availabilityZone": 'nova',
                    "?": {
                        "type": "io.murano.resources.LinuxMuranoInstance",
                        "id": str(uuid.uuid4())
                    }
                },
                "?": {
                    "type": "io.murano.apps.docker.kubernetes."
                            "KubernetesMinionNode",
                    "id": str(uuid.uuid4())
                },
                "exposeCAdvisor": nodes['cadvisor']
            }
        ]
    else:
        nodes_data = [
            {
                "instance": {
                    "name": "minion-1",
                    "assignFloatingIp": True,
                    "keyname": keypair.name,
                    "flavor": flavor,
                    "image": kubernetes_image,
                    "availabilityZone": 'nova',
                    "?": {
                        "type": "io.murano.resources.LinuxMuranoInstance",
                        "id": str(uuid.uuid4())
                    }
                },
                "?": {
                    "type": "io.murano.apps.docker.kubernetes."
                            "KubernetesMinionNode",
                    "id": str(uuid.uuid4())
                },
                "exposeCAdvisor": nodes['cadvisor']
            },
            {
                "instance": {
                    "name": "minion-2",
                    "assignFloatingIp": True,
                    "keyname": keypair.name,
                    "flavor": flavor,
                    "image": kubernetes_image,
                    "availabilityZone": 'nova',
                    "?": {
                        "type": "io.murano.resources.LinuxMuranoInstance",
                        "id": str(uuid.uuid4())
                    }
                },
                "?": {
                    "type": "io.murano.apps.docker.kubernetes."
                            "KubernetesMinionNode",
                    "id": str(uuid.uuid4())
                },
                "exposeCAdvisor": nodes['cadvisor']
            }
        ]

    kub_data = {
        "gatewayCount": nodes['initial_gateways'],
        "gatewayNodes": gateways_data,
        "?": {
            "_{id}".format(id=uuid.uuid4().hex): {
                "name": "Kubernetes Cluster"
            },
            "type": "io.murano.apps.docker.kubernetes.KubernetesCluster",
            "id": str(uuid.uuid4())
        },
        "nodeCount": nodes['initial_nodes'],
        "dockerRegistry": "",
        "masterNode": {
            "instance": {
                "name": "master-1",
                "assignFloatingIp": True,
                "keyname": keypair.name,
                "flavor": flavor,
                "image": kubernetes_image,
                "availabilityZone": 'nova',
                "?": {
                    "type": "io.murano.resources.LinuxMuranoInstance",
                    "id": str(uuid.uuid4())
                }
            },
            "?": {
                "type": "io.murano.apps.docker.kubernetes."
                        "KubernetesMasterNode",
                "id": str(uuid.uuid4())
            }
        },
        "minionNodes": nodes_data,
        "name": "KubeClusterTest"
    }
    cluster = murano.create_service(environment, session, kub_data)
    return cluster


@pytest.fixture
def pod(murano, environment, session, cluster, request):
    replicas = getattr(request, 'param', 1)
    pod_data = {
        "kubernetesCluster": cluster,
        "labels": labels,
        "name": "testpod",
        "replicas": replicas,
        "?": {
            "_{id}".format(id=uuid.uuid4().hex): {
                "name": "Kubernetes Pod"
            },
            "type": "io.murano.apps.docker.kubernetes.KubernetesPod",
            "id": str(uuid.uuid4())
        }
    }
    pod = murano.create_service(environment, session, pod_data)
    return pod


@pytest.fixture
def influx(murano, environment, session, pod):
    post_body = {
        "host": pod,
        "name": "Influx",
        "preCreateDB": 'db1;db2',
        "publish": True,
        "?": {
            "_{id}".format(id=uuid.uuid4().hex): {
                "name": "Docker InfluxDB"
            },
            "type": "io.murano.apps.docker.DockerInfluxDB",
            "id": str(uuid.uuid4())
        }
    }
    return murano.create_service(environment, session, post_body)


@pytest.yield_fixture
def volume(os_conn):
    image = os_conn.nova.images.find(name='TestVM')
    volume = common.create_volume(os_conn.cinder, image.id)
    yield volume
    os_conn.delete_volume(volume)


@pytest.fixture
def volume_snapshot(os_conn, volume):
    snapshot = os_conn.cinder.volume_snapshots.create(volume.id)

    def is_snapshot_available(os_conn, snapshot):
        snp_status = os_conn.cinder.volume_snapshots.get(snapshot.id).status
        assert snp_status != 'error'
        return snp_status == 'available'

    common.wait(lambda: is_snapshot_available(os_conn, snapshot),
                timeout_seconds=300,
                waiting_for='snapshot to become in available status')
    return snapshot


@pytest.fixture
def volume_backup(os_conn, volume):
    backup = os_conn.cinder.backups.create(volume.id)

    def is_backup_available(os_conn, backup):
        bck_status = os_conn.cinder.backups.get(backup.id).status
        assert bck_status != 'error'
        return bck_status == 'available'

    common.wait(lambda: is_backup_available(os_conn, backup),
                timeout_seconds=300,
                waiting_for='backup to become in available status')
    return backup


@pytest.fixture
def restart_murano_services(env):
    murano_services_cmd = ("service --status-all 2>&1 | grep '+' | "
                           "grep murano | awk '{ print $4 }'")
    for node in env.get_nodes_by_role('controller'):
        with node.ssh() as remote:
            output = remote.check_call(murano_services_cmd).stdout_string
            for service in output.splitlines():
                remote.check_call('service {0} restart'.format(service))
