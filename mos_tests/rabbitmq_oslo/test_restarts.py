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
import re
import sys
import uuid

import pytest
from six.moves import configparser

from mos_tests.environment import ssh
from mos_tests.functions.common import wait

from mos_tests import settings


logger = logging.getLogger(__name__)


def vars_config(remote, **kwargs):
    """Prepare variables and different paths
    :param remote: SSH connection point to node
    """
    config_vars = {
        'repo': settings.RABBITOSLO_REPO,
        'pkg': settings.RABBITOSLO_PKG,
        'nova_config': '/etc/nova/nova.conf',
        'repo_path': '/root/oslo_messaging_check_tool/',
        'rpc_port': settings.RABBITOSLO_TOOL_PORT,

    }
    # get credentials of rabbitmq
    with remote.open(config_vars['nova_config']) as f:
        parser = configparser.RawConfigParser()
        parser.readfp(f)
        config_vars['rabbit_userid'] = parser.get('oslo_messaging_rabbit',
                                                  'rabbit_userid')
        config_vars['rabbit_password'] = parser.get('oslo_messaging_rabbit',
                                                    'rabbit_password')
        config_vars['rabbit_hosts'] = parser.get('oslo_messaging_rabbit',
                                                 'rabbit_hosts')
    # like: /root/oslo_messaging_check_tool/oslo_msg_check.conf
    config_vars['cfg_file_path'] = '{}oslo_msg_check.conf'.format(
        config_vars['repo_path'])

    config_vars['sample_cfg_file_path'] = '{}oslo_msg_check.conf.sample'.\
        format(config_vars['repo_path'])
    return config_vars


def install_oslomessagingchecktool(remote, **kwargs):
    """Install 'oslo.messaging-check-tool' on controller.
    https://github.com/dmitrymex/oslo.messaging-check-tool
    :param remote: SSH connection point to node
    """
    cmd1 = ("apt-get update 2> /tmp/keymissing ; for key in "
            "$(grep 'NO_PUBKEY' /tmp/keymissing | sed 's/.*NO_PUBKEY //') ; "
            "do echo -e '\nProcessing key: $key' ; "
            "apt-key adv --keyserver keyserver.ubuntu.com --recv-keys $key ; "
            "done;"
            "apt-get install git dpkg-dev debhelper dh-systemd "
            "openstack-pkg-tools po-debconf python-all python-pbr "
            "python-setuptools python-sphinx python-babel "
            "python-eventlet python-flask python-oslo.config "
            "python-oslo.log python-oslo.messaging python-oslosphinx -y && "
            "rm -rf {repo_path} && "
            "git clone {repo} {repo_path} ;").format(**kwargs)
    cmd2 = ("dpkg -r oslo.messaging-check-tool || "
            "echo 'Trying to remove package';"
            "cd {repo_path};"
            "dpkg -i {pkg} || "
            "apt-get -f install -y").format(**kwargs)
    logger.debug('Install "oslo.messaging-check-tool" on %s.' %
                 remote.host)
    remote.check_call(cmd1)
    remote.check_call(cmd2)


def configure_oslomessagingchecktool(remote,
                                     rabbit_message_is_event=True,
                                     rabbit_custom_topic=None,
                                     rabbit_custom_hosts=None,
                                     custom_tool_rpc_port=None,
                                     custom_cfg_filename=None):
    """Write configuration file on host.
    :param remote: SSH connection point to host;
    :param rabbit_message_is_event: set type of messages (true - for events);
    :param rabbit_custom_topic: set custom topic for rabbitmq (by default:
    oslo_messaging_checktool);
    :param rabbit_custom_hosts: set custom rabbitmq hosts (by default used
    hosts from nova.conf);
    :param custom_tool_rpc_port: set custom port for checktool RPC (default
    value current on RABBITOSLO_TOOL_PORT);
    :param custom_cfg_filename: set custom config filename (default:
    oslo_msg_check.conf);
    """
    default_vars = vars_config(remote)
    if rabbit_custom_hosts:
        rabbit_port = ':5673'
        rabbit_hosts = ', '.join([(x + rabbit_port)
                                  for x in rabbit_custom_hosts])
    else:
        rabbit_hosts = default_vars['rabbit_hosts']
    rabbit_topic = 'oslo_messaging_checktool'
    if rabbit_custom_topic:
        rabbit_topic = rabbit_custom_topic
    if rabbit_message_is_event:
        rabbit_topic = "event.%s" % rabbit_topic
    if custom_tool_rpc_port:
        rabbit_rpc_port = str(custom_tool_rpc_port)
    else:
        rabbit_rpc_port = default_vars['rpc_port']
    tool_config = default_vars['repo_path']
    if custom_cfg_filename:
        tool_config = '{path}{filename}'.format(path=tool_config,
                                                filename=custom_cfg_filename)
    else:
        tool_config = '%soslo_msg_check.conf' % tool_config

    with remote.open(default_vars['sample_cfg_file_path'], 'r') as f:
        parser = configparser.RawConfigParser()
        parser.readfp(f)
        parser.set('DEFAULT', 'notif_topic_name', rabbit_topic)
        parser.set('DEFAULT', 'listen_port', rabbit_rpc_port)
        parser.set('oslo_messaging_rabbit', 'rabbit_hosts', rabbit_hosts)
        parser.set('oslo_messaging_rabbit', 'rabbit_userid',
                   default_vars['rabbit_userid'])
        parser.set('oslo_messaging_rabbit', 'rabbit_password',
                   default_vars['rabbit_password'])
        # Dump to cfg file to screen
        parser.write(sys.stdout)
        logger.debug('Write [{0}] config file to {1}.'.format(
            tool_config, remote.host))
        # Write to new cfg file
        with remote.open(tool_config, 'w') as f:
            parser.write(f)


def get_mngmnt_ip_of_ctrllrs(env):
    """Get host IP of management network from all controllers"""
    controllers = env.get_nodes_by_role('controller')
    ctrl_ips = []
    for one in controllers:
        ip = [x['ip'] for x in one.data['network_data']
              if x['name'] == 'management'][0]
        ip = ip.split("/")[0]
        ctrl_ips.append(ip)
    return ctrl_ips


def get_mngmnt_ip_of_computes(env):
    """Get host IP of management network from all computes"""
    controllers = env.get_nodes_by_role('compute')
    ctrl_ips = []
    for one in controllers:
        ip = [x['ip'] for x in one.data['network_data']
              if x['name'] == 'management'][0]
        ip = ip.split("/")[0]
        ctrl_ips.append(ip)
    return ctrl_ips


def get_mngmnt_ip_of_node(host):
    """Get host IP of management network from current host"""
    ip = [x['ip'] for x in host.data['network_data']
          if x['name'] == 'management'][0]
    ip = ip.split("/")[0]
    return ip


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


def num_of_rabbit_primary_running_nodes(remote):
    """Get count of primary RabbitMQ nodes from pacemaker.
    :param remote: SSH connection point to controller.
    """
    result = remote.execute('pcs status --full | '
                            'grep p_rabbitmq-server | '
                            'grep ocf | '
                            'grep -c -E "Master"', verbose=False)
    count = result['stdout'][0].strip()
    if count.isdigit():
        return int(count)
    else:
        return 0


def wait_for_rabbit_running_nodes(remote, exp_nodes, primary_nodes=1,
                                  timeout_min=5):
    """Waits until number of 'Started/Master' hosts from pacemaker
    will be as expected number of controllers.
    :param remote: SSH connection point to controller.
    :param exp_nodes: Expected number of rabbit nodes.
    :param primary_nodes: Count of rabbitmq primary nodes, by default = 1.
    :param timeout_min: Timeout in minutes to wait.
    """
    wait(lambda: num_of_rabbit_running_nodes(remote) == exp_nodes,
         timeout_seconds=60 * timeout_min,
         sleep_seconds=30,
         waiting_for='number of running nodes will be %s.' % exp_nodes)

    if primary_nodes >= 0:
        wait(lambda:
             num_of_rabbit_primary_running_nodes(remote) == primary_nodes,
             timeout_seconds=60 * timeout_min, sleep_seconds=30,
             waiting_for='number of running primary nodes will be %s.'
                         % primary_nodes)


def generate_msg(remote, cfg_file_path, num_of_msg_to_gen=10000):
    """Generate messages with oslo_msg_load_generator
    :param remote: SSH connection point to controller.
    :param cfg_file_path: Path to the config file.
    :param num_of_msg_to_gen: How many messages to generate.
    """
    # Clean if some messages were left after previous failed tests
    cmd = ('oslo_msg_load_consumer '
           '--config-file {0} '
           '--nodebug'.format(cfg_file_path))
    remote.check_call(cmd)
    cmd = ('oslo_msg_load_generator '
           '--config-file {0} '
           '--messages_to_send {1} '
           '--nodebug'.format(cfg_file_path, num_of_msg_to_gen))
    remote.check_call(cmd)


def consume_msg(remote, cfg_file_path):
    """Consume messages with oslo_msg_load_consumer
    :param remote: SSH connection point to controller.
    :param cfg_file_path: Path to the config file.
    """
    cmd = ('oslo_msg_load_consumer '
           '--config-file {0} '
           '--nodebug'.format(cfg_file_path))
    out_consume = remote.check_call(cmd)['stdout'][0]
    num_of_msg_consumed = int(re.findall('\d+', out_consume)[0])
    return num_of_msg_consumed


def rabbit_rpc_server_start(remote, cfg_file_path):
    logger.debug('Start [oslo_msg_check_server] on %s.' % remote.host)
    background = '<&- >/dev/null 2>&1 &'
    cmd = 'oslo_msg_check_server --nodebug --config-file {0} {1}'.format(
        cfg_file_path, background)
    remote.execute(cmd)


def rabbit_rpc_client_start(remote, cfg_file_path):
    logger.debug('Start [oslo_msg_check_client] on %s.' % remote.host)
    background = '<&- >/dev/null 2>&1 &'
    cmd = 'oslo_msg_check_client --nodebug --config-file {0} {1}'.format(
        cfg_file_path, background)
    remote.execute(cmd)
    return remote.host


def get_http_code(remote, host="127.0.0.1", port=80):
    cmd = 'curl --max-time 15 --write-out "%{http_code}" --silent ' \
          '--output /dev/null '
    cmd = '%s "http://%s:%d"' % (cmd, host, port)
    # curl to client
    result = remote.execute(cmd)['stdout'][0].strip()
    if result.isdigit():
        return int(result)
    else:
        return 0


def wait_rabbit_ok_on_all_ctrllrs(env, timeout_min=7):
    """Wait untill rabbit will be OK on all controllers"""
    controllers = env.get_nodes_by_role('controller')
    for one in controllers:
        with one.ssh() as remote:
            wait_for_rabbit_running_nodes(
                remote, len(controllers), timeout_min=timeout_min)


def restart_rabbitmq_serv(env, remote=None, wait_time=120):
    """Restart RabbitMQ by pacemaker on one or all controllers.
    After each restart, check that rabbit is up and running.
    :param env: Environment
    :param remote: SSH connection point to controller, if None - restart
    rabbitmq on controllers one by one or together.
    :param wait_time: Delay for restart (ban/clean pcs) rabbitmq.
    """
    # In some cases pcs can return non-zero exit code - it's normal.
    # 'echo' commands are here to fix it.
    restart_commands = {
        'start': 'pcs resource clear p_rabbitmq-server --wait=%d $(hostname)'
                 '|| echo "Started p_rabbitmq-server"' % wait_time,
        'stop': 'pcs resource ban p_rabbitmq-server --wait=%d $(hostname) || '
                'echo "Stopped p_rabbitmq-server"' % wait_time
    }

    controllers = env.get_nodes_by_role('controller')
    if remote:
        # restart on all controllers
        # Before and after restart check that rabbit is ok.
        # Useful if we as restarting.
        logger.debug('Restart RabbitMQ server on current controller')
        wait_for_rabbit_running_nodes(remote, len(controllers))
        remote.check_call(restart_commands['stop'])
        wait_for_rabbit_running_nodes(remote, len(controllers) - 1)
        remote.check_call(restart_commands['start'])
        wait_for_rabbit_running_nodes(remote, len(controllers))
    else:
        logger.debug('Restart RabbitMQ server on ALL controllers '
                     'one-by-one')
        for controller in controllers:
            with controller.ssh() as remote:
                wait_for_rabbit_running_nodes(remote, len(controllers))
                remote.check_call(restart_commands['stop'])
                wait_for_rabbit_running_nodes(remote, len(controllers) - 1)
                remote.check_call(restart_commands['start'])
                wait_for_rabbit_running_nodes(remote, len(controllers))


def restart_rabbitmq_cluster(env, wait_time=120):
    """Restart RabbitMQ cluster by pacemaker.
    After each restart, check that rabbit is up and running.
    :param env: Environment
    :param wait_time: Delay for restart (disable/enable pcs) rabbitmq.
    """

    restart_commands = {
        'enable': 'pcs resource enable p_rabbitmq-server --wait=%d $(hostname)'
                  ' || echo "Started p_rabbitmq-server"' % wait_time,
        'disable': 'pcs resource disable p_rabbitmq-server --wait=%d '
                   '$(hostname)|| echo "Stopped p_rabbitmq-server"' % wait_time
    }
    controllers = env.get_nodes_by_role('controller')
    controller = random.choice(controllers)

    with controller.ssh() as remote:
        wait_for_rabbit_running_nodes(remote, len(controllers))
        logger.debug('Restart RabbitMQ cluster with enable/disable commands')
        remote.check_call(restart_commands['disable'])
        wait_for_rabbit_running_nodes(remote, 0, 0)
        remote.check_call(restart_commands['enable'])
        wait_for_rabbit_running_nodes(remote, len(controllers))


def migrate_rabbitmq_primary_node(env, wait_time=120):
    """Migrate primary node in RabbitMQ cluster

    :param env: Environment
    :param wait_time: Delay for ban command rabbitmq.

    """

    controllers = env.get_nodes_by_role('controller')
    controller = random.choice(controllers)

    with controller.ssh() as remote:
        wait_for_rabbit_running_nodes(remote, len(controllers))
        current_primary = remote.check_call(
            "pcs status --full | grep p_rabbitmq-server | grep ocf | "
            "grep Master | grep -o 'node-.*'")['stdout'][0].strip()
        remote.execute("pcs resource ban p_rabbitmq-server --wait=%d %s" %
                       (wait_time, current_primary))
        wait_for_rabbit_running_nodes(remote, len(controllers) - 1)
        remote.execute("pcs resource clear p_rabbitmq-server --wait=%d %s" %
                       (wait_time, current_primary))
        wait_for_rabbit_running_nodes(remote, len(controllers))


def kill_rabbitmq_on_node(remote, timeout_min=7):
    """Waiting for rabbit startup and got pid, then kill-9 it"""

    def get_pid():
        cmd = "rabbitmqctl status | grep '{pid' | tr -dc '0-9'"
        result = remote.check_call(cmd)['stdout']
        if len(result) > 0:
            pid = result[0].strip()
            if pid.isdigit():
                return pid

    wait(lambda: get_pid(),
         timeout_seconds=60 * timeout_min,
         sleep_seconds=30,
         waiting_for='Rabbit get its pid on %s.' % remote.host)
    cmd = "pkill beam.smp"
    remote.check_call(cmd)


# ----------------------------------------------------------------------------


@pytest.mark.undestructive
@pytest.mark.check_env_('is_ha', 'has_1_or_more_computes')
@pytest.mark.testrail_id('857390', params={'consume_message_from': 'same'})
@pytest.mark.testrail_id('857391', params={'consume_message_from': 'other'})
@pytest.mark.parametrize('consume_message_from', ['same', 'other'])
def test_check_send_and_receive_messages_from_the_same_nodes(
        env, consume_message_from):
    """[Undestructive] Send/receive messages to all rabbitmq nodes.
    :param env: Enviroment
    :param consume_message_from: Consume message the same or other node
    (which upload messages).

    Actions:
    1. Install "oslo.messaging-check-tool" on compute;
    2. Prepare config files for all controllers;
    3. Generate 10000 messages for all controllers;
    4. Consume messages;
    5. Check that number of generated and consumed messages is equal.
    """

    controllers = env.get_nodes_by_role('controller')
    computes = env.get_nodes_by_role('compute')
    compute = random.choice(computes)
    controller = random.choice(controllers)
    topic = str(uuid.uuid4())

    # Get management IPs of all controllers
    ctrl_ips = get_mngmnt_ip_of_ctrllrs(env)

    # Wait when rabbit will be ok after snapshot revert
    with controller.ssh() as remote:
        wait_for_rabbit_running_nodes(remote, len(controllers))

    # Install tool on one compute and make configs
    with compute.ssh() as remote:
        kwargs = vars_config(remote)
        install_oslomessagingchecktool(remote, **kwargs)
        # configure
        for ctrl_ip in ctrl_ips:
            configure_oslomessagingchecktool(
                remote, rabbit_message_is_event=False,
                rabbit_custom_topic=topic,
                rabbit_custom_hosts=[ctrl_ip],
                custom_cfg_filename='oslo_msg_check_%s.conf' % ctrl_ip,
            )
        # Generate messages and consume
        num_of_msg_to_gen = 10000
        for ctrl_ip in ctrl_ips:
            config_path = "{path}{config_name}".format(
                path=kwargs['repo_path'],
                config_name='oslo_msg_check_%s.conf' % ctrl_ip)

            generate_msg(remote, config_path, num_of_msg_to_gen)

            if consume_message_from == 'same':
                num_of_msg_consumed = consume_msg(remote, config_path)
                logger.debug("Host %s messages[%s/%s]." % (
                    ctrl_ip, num_of_msg_consumed, num_of_msg_to_gen))
            elif consume_message_from == 'other':
                custom_ctrl_ips = ctrl_ips
                custom_ctrl_ips.remove(ctrl_ip)
                custom_ctrl_ip = random.choice(custom_ctrl_ips)
                config_path = "{path}{config_name}".format(
                    path=kwargs['repo_path'],
                    config_name='oslo_msg_check_%s.conf' % custom_ctrl_ip)
                num_of_msg_consumed = consume_msg(remote, config_path)
                logger.debug("Upload to %s, download from %s. "
                             "Stats messages[%s/%s]." %
                             (ctrl_ip, custom_ctrl_ip, num_of_msg_consumed,
                              num_of_msg_to_gen))

            assert num_of_msg_to_gen == num_of_msg_consumed, \
                ('Generated and consumed number of messages is different on '
                 '%s host.' % ctrl_ip)


@pytest.mark.undestructive
@pytest.mark.check_env_('is_ha', 'has_1_or_more_computes')
@pytest.mark.testrail_id('857392', params={'node_type': 'compute'})
@pytest.mark.testrail_id('857393', params={'node_type': 'controller'})
@pytest.mark.parametrize('node_type', ['compute', 'controller'])
def test_check_send_and_receive_messages_from_diff_type_nodes(env, node_type):
    """[Undestructive] Send/receive messages to rabbitmq
    cluster for different types of fuel nodes.
    :param env: Enviroment
    :param node_type: Select type of nodes for send/recv messages.

    Actions:
    1. Install "oslo.messaging-check-tool" on compute;
    2. Prepare config files for current fuel node types;
    3. Generate and consume 10000 messages from RabbitMQ cluster.
    4. Check that number of generated and consumed messages is equal.
    """

    controllers = env.get_nodes_by_role('controller')
    computes = env.get_nodes_by_role('compute')
    compute = random.choice(computes)
    controller = random.choice(controllers)

    # Wait when rabbit will be ok after snapshot revert
    with controller.ssh() as remote:
        wait_for_rabbit_running_nodes(remote, len(controllers))

    # Install tool on one compute and make configs
    if node_type == 'compute':
        host = compute
    elif node_type == 'controller':
        host = controller
    with host.ssh() as remote:
        kwargs = vars_config(remote)
        install_oslomessagingchecktool(remote, **kwargs)
        # configure
        configure_oslomessagingchecktool(remote, rabbit_message_is_event=False)
        # Generate messages and consume
        num_of_msg_to_gen = 10000
        generate_msg(remote, kwargs['cfg_file_path'], num_of_msg_to_gen)
        num_of_msg_consumed = consume_msg(remote, kwargs['cfg_file_path'])

    assert num_of_msg_to_gen == num_of_msg_consumed, \
        'Generated and consumed number of messages is different'


@pytest.mark.check_env_('is_ha', 'has_1_or_more_computes')
@pytest.mark.testrail_id('857394', params={'restart_type': 'single'})
@pytest.mark.testrail_id('857395', params={'restart_type': 'one_by_one'})
@pytest.mark.parametrize('restart_type', ['single', 'one_by_one'])
def test_upload_10000_events_to_cluster_and_restart_controllers(env,
                                                                restart_type):
    """Load 10000 events to RabbitMQ cluster and restart controllers single
    or one-by-one.
    :param env: Enviroment
    :param restart_type: This parameter specifies the node restart strategy.

    Actions:
    1. Install "oslo.messaging-check-tool" on compute;
    2. Prepare config files for current fuel node types;
    3. Generate 10000 events to RabbitMQ cluster.
    4. Restart one random rabbitmq node or all(one-by-one).
    5. Consume 10000 events from RabbitMQ cluster.
    6. Check that number of generated and consumed messages is equal.
    """

    controllers = env.get_nodes_by_role('controller')
    controller = random.choice(controllers)

    # Wait when rabbit will be ok after snapshot revert
    with controller.ssh() as remote:
        wait_for_rabbit_running_nodes(remote, len(controllers))

    with controller.ssh() as remote:
        kwargs = vars_config(remote)
        install_oslomessagingchecktool(remote, **kwargs)
        configure_oslomessagingchecktool(remote)

        # Generate messages and consume
        num_of_msg_to_gen = 10000
        generate_msg(remote, kwargs['cfg_file_path'], num_of_msg_to_gen)
        if restart_type == 'single':
            logger.debug("Restarting RabbitMQ on one random node")
            restart_rabbitmq_serv(env, remote)
        elif restart_type == 'one_by_one':
            logger.debug("Restarting RabbitMQ on all nodes "
                         "(with one-by-one strategy)")
            restart_rabbitmq_serv(env)
        num_of_msg_consumed = consume_msg(remote, kwargs['cfg_file_path'])
    assert num_of_msg_to_gen == num_of_msg_consumed, \
        ('Generated and consumed number of messages is different '
         'after RabbitMQ cluster restarting.')


@pytest.mark.check_env_('is_ha', 'has_1_or_more_computes')
@pytest.mark.testrail_id('857396')
def test_upload_messages_on_one_restart_and_receive_on_other(env):
    """"[Destructive] Send messages to one rabbitmq node, restart other and
    receive messages on them.

    :param env: Enviroment.

   Actions:
    1. Install "oslo.messaging-check-tool" on compute;
    2. Prepare config files;
    3. Generate 10000 messages to current RabbitMQ node.
    4. Restart all other rabbitmq nodes.
    5. Consume 10000 messages from one of other RabbitMQ node.
    6. Check that number of generated and consumed messages is equal.
    """

    controllers = env.get_nodes_by_role('controller')
    controller = random.choice(controllers)
    controller_ip = get_mngmnt_ip_of_node(controller)
    other_controllers = controllers[:]
    other_controllers.remove(controller)

    # Get management IPs of all controllers
    ctrl_ips_exclude_current = get_mngmnt_ip_of_ctrllrs(env)
    ctrl_ips_exclude_current.remove(controller_ip)

    # Wait when rabbit will be ok after snapshot revert
    with controller.ssh() as remote:
        wait_for_rabbit_running_nodes(remote, len(controllers))

    with controller.ssh() as remote:
        kwargs = vars_config(remote)
        install_oslomessagingchecktool(remote, **kwargs)
        configure_oslomessagingchecktool(remote, False,
                                         rabbit_custom_hosts=[controller_ip])

        # Generate messages and consume
        num_of_msg_to_gen = 10000
        generate_msg(remote, kwargs['cfg_file_path'], num_of_msg_to_gen)

    for current in other_controllers:
        with current.ssh() as current_remote:
            restart_rabbitmq_serv(env, current_remote)

    with controller.ssh() as remote:
        kwargs = vars_config(remote)
        configure_oslomessagingchecktool(
            remote, False,
            rabbit_custom_hosts=[random.choice(ctrl_ips_exclude_current)])

        num_of_msg_consumed = consume_msg(remote, kwargs['cfg_file_path'])

    assert num_of_msg_to_gen == num_of_msg_consumed, \
        ('Generated and consumed number of messages is different '
         'after RabbitMQ cluster restarting.')


@pytest.mark.check_env_('is_ha', 'has_1_or_more_computes')
@pytest.mark.testrail_id('857397', params={'action': 'restart_all'})
@pytest.mark.testrail_id('857398', params={'action': 'migrate_primary'})
@pytest.mark.parametrize('action', ['restart_all', 'migrate_primary'])
def test_verify_cnnct_after_full_restart_rabbitmq_or_migrate_primary_node(
        env, action):
    """"[Destructive] Verify connectivity after full-restart RabbitMQ cluster
    or ban primary node.

    :param env: Enviroment.
    :param action: Test action restart rabbitmq cluster on migrate primary node

   Actions:
    1. Install "oslo.messaging-check-tool" on controller;
    2. Prepare config file;
    3. Restart Rabbitmq cluster or migrate primary node.
    4. Check rabbitmq connectivity by OSLO RPC Client/Server app.
    """

    controllers = env.get_nodes_by_role('controller')
    controller = random.choice(controllers)
    rpc_tool_port = 12400

    # Wait when rabbit will be ok after snapshot revert
    with controller.ssh() as remote:
        wait_for_rabbit_running_nodes(remote, len(controllers))

    with controller.ssh() as remote:
        kwargs = vars_config(remote)
        install_oslomessagingchecktool(remote, **kwargs)
        configure_oslomessagingchecktool(remote,
                                         custom_tool_rpc_port=rpc_tool_port)
        if action == 'restart_all':
            restart_rabbitmq_cluster(env)
        elif action == 'migrate_primary':
            migrate_rabbitmq_primary_node(env)

        rabbit_rpc_server_start(remote, kwargs['cfg_file_path'])
        rabbit_rpc_client_start(remote, kwargs['cfg_file_path'])
        wait(lambda: get_http_code(remote, port=rpc_tool_port) != 000,
             timeout_seconds=60 * 3,
             sleep_seconds=30,
             waiting_for='wait for starting oslo.messaging-check-tool '
                         'RPC server/client app')

        response_status_code = get_http_code(remote, port=rpc_tool_port)

    assert 200 == response_status_code, 'Verify rabbitmq connection was failed'


@pytest.mark.check_env_('is_ha', 'has_1_or_more_computes')
@pytest.mark.testrail_id('857422')
def test_kill_all_rabbit_nodes_and_check_connectivity_many_times(env):
    """"[Destructive] Kill all rabbitmq nodes and check connectivity (x10).

    :param env: Enviroment.

   Actions:
    1. Install "oslo.messaging-check-tool" on controller;
    2. Prepare config file;
    3. Kill (with `kill -9`) all rabbitmq nodes and wait when rabbit will
    be up and running;
    4. Check rabbitmq connectivity by OSLO RPC Client/Server app.
    5. Retry step 3-4 (x10)
    """

    controllers = env.get_nodes_by_role('controller')
    controller = random.choice(controllers)
    rpc_tool_port = 12400

    # Wait when rabbit will be ok after snapshot revert
    with controller.ssh() as remote:
        wait_for_rabbit_running_nodes(remote, len(controllers))

    with controller.ssh() as remote:
        kwargs = vars_config(remote)
        install_oslomessagingchecktool(remote, **kwargs)
        configure_oslomessagingchecktool(remote,
                                         custom_tool_rpc_port=rpc_tool_port)
        rabbit_rpc_server_start(remote, kwargs['cfg_file_path'])
        rabbit_rpc_client_start(remote, kwargs['cfg_file_path'])
        wait(lambda: get_http_code(remote, port=rpc_tool_port) != 000,
             timeout_seconds=60 * 3,
             sleep_seconds=30,
             waiting_for='wait for starting oslo.messaging-check-tool '
                         'RPC server/client app')
        max_retry = 10
        for current_retry in range(1, max_retry):
            for current_controller in controllers:
                with current_controller.ssh() as current_remote:
                    kill_rabbitmq_on_node(current_remote)
            wait_for_rabbit_running_nodes(remote, len(controllers),
                                          timeout_min=15)

            wait(lambda: get_http_code(remote, port=rpc_tool_port) != 000,
                 timeout_seconds=60 * 3,
                 sleep_seconds=30,
                 waiting_for='wait for starting oslo.messaging-check-tool '
                             'RPC server/client app')

            response_status_code = get_http_code(remote, port=rpc_tool_port)
            assert 200 == response_status_code, \
                ('Retry [%d/%d] - Verify rabbitmq connection was failed' %
                 (current_retry, max_retry))


@pytest.mark.check_env_('is_ha', 'has_1_or_more_computes')
@pytest.mark.testrail_id('857428')
def test_check_primary_node_migration_many_times(env):
    """"[Destructive] Stop rabbitmq primary node, wait migration
    finished, start same slave node (x10).

    :param env: Enviroment.

   Actions:
    1. Install "oslo.messaging-check-tool" on controller;
    2. Prepare config file;
    3. Check rabbitmq connectivity by OSLO RPC Client/Server app.
    4. Ban primary rabbitmq node and wait while migration was finished.
    5. Check rabbitmq connectivity by OSLO RPC Client/Server app.
    6. Retry step 4-5 (x10)
    """

    controllers = env.get_nodes_by_role('controller')
    controller = random.choice(controllers)
    rpc_tool_port = 12400

    # Wait when rabbit will be ok after snapshot revert
    with controller.ssh() as remote:
        wait_for_rabbit_running_nodes(remote, len(controllers))

    with controller.ssh() as remote:
        kwargs = vars_config(remote)
        install_oslomessagingchecktool(remote, **kwargs)
        configure_oslomessagingchecktool(remote,
                                         custom_tool_rpc_port=rpc_tool_port)
        rabbit_rpc_server_start(remote, kwargs['cfg_file_path'])
        rabbit_rpc_client_start(remote, kwargs['cfg_file_path'])
        wait(lambda: get_http_code(remote, port=rpc_tool_port) != 000,
             timeout_seconds=60 * 3,
             sleep_seconds=30,
             waiting_for='wait for starting oslo.messaging-check-tool '
                         'RPC server/client app')
        max_retry = 10
        for current_retry in range(1, max_retry):
            migrate_rabbitmq_primary_node(env)
            wait(lambda: get_http_code(remote, port=rpc_tool_port) != 000,
                 timeout_seconds=60 * 3,
                 sleep_seconds=30,
                 waiting_for='wait for starting oslo.messaging-check-tool '
                             'RPC server/client app')


@pytest.mark.check_env_('is_ha', 'has_1_or_more_computes')
@pytest.mark.testrail_id('844792')
def test_check_logs_for_epmd_successfully_restarted(env):
    """"[Destructive] Restart rabbitmq by pcs and check logs in all nodes.

    :param env: Enviroment.

   Actions:
    1. Make SSH to one of controller;
    2. Restart rabbitmq cluster by pcs;
    3. Check logs for "already running" error messages in all controllers.
    """

    controllers = env.get_nodes_by_role('controller')
    restart_rabbitmq_cluster(env)
    for controller in controllers:
        with controller.ssh() as remote:
            result = remote.execute(
                "grep 'already running' /var/log/rabbitmq/*")['exit_code']
            assert 0 != result, ('Find error in logs on %s host.' %
                                 get_mngmnt_ip_of_node(remote))


@pytest.mark.check_env_('is_ha', 'has_1_or_more_computes')
@pytest.mark.testrail_id('844791')
def test_check_rabbitmqctl_on_segfaults(env):
    """"[Destructive] Check rabbitmqctl segfaults on controller.

    :param env: Enviroment.

   Actions:
    1. Make SSH to one of controller;
    2. Ban rabbitmq node by pcs on current controller;
    3. Call 'rabbitmqctl status' command and verify that stdout+stderr
    don't contain 'segfault' word. (x30)
    """

    def check_rabbitmqctl_segfault():
        max_retry = 30
        result = 0
        for step in range(1, max_retry):
            logger.debug("Retry[%s/%s]: Call rabbitmqctl status." %
                         (step, max_retry))
            try:
                result = remote.execute('rabbitmqctl status 2>&1 | '
                                        'grep "segfault"')['exit_code']
            except ssh.CalledProcessError:
                logger.info("Ignore none-zero exit code for "
                            "`rabbitmqctl status` command")

            assert 0 != result, 'Found segfault message'

    controllers = env.get_nodes_by_role('controller')
    controller = random.choice(controllers)

    # Wait when rabbit will be ok after snapshot revert
    with controller.ssh() as remote:
        wait_for_rabbit_running_nodes(remote, len(controllers))

    with controller.ssh() as remote:
        check_rabbitmqctl_segfault()
        remote.execute('pcs resource ban p_rabbitmq-server --wait=120 '
                       '$(hostname)')

        check_rabbitmqctl_segfault()

        exp_nodes = len(controllers) - 1
        wait(lambda: num_of_rabbit_running_nodes(remote) == exp_nodes,
             timeout_seconds=120,
             sleep_seconds=30,
             waiting_for='number of running nodes will be %s.' % exp_nodes)

        remote.execute('pcs resource clear p_rabbitmq-server --wait=120 '
                       '$(hostname)')

        exp_nodes = len(controllers)
        wait(lambda: num_of_rabbit_running_nodes(remote) == exp_nodes,
             timeout_seconds=120,
             sleep_seconds=30,
             waiting_for='number of running nodes will be %s.' % exp_nodes)

        check_rabbitmqctl_segfault()
