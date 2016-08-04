"""
Server fixtures.

@author: schipiga@mirantis.com
"""

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest

from mos_tests.nova.steps import ServerSteps
from mos_tests.utils import generate_ids

__all__ = [
    'create_server',
    'create_servers',
    'server',
    'server_steps'
]


@pytest.fixture
def server_steps(nova_client):
    """Fixture to get nova steps."""
    return ServerSteps(nova_client.servers)


@pytest.yield_fixture
def create_servers(server_steps):
    """Fixture to create servers with options.

    Can be called several times during test.
    """
    servers = []

    def _create_servers(server_names, *args, **kwgs):
        _servers = server_steps.create_servers(server_names, *args, **kwgs)
        servers.extend(_servers)
        return _servers

    yield _create_servers

    if servers:
        server_steps.delete_servers(servers)


@pytest.fixture
def create_server(create_servers):
    """Fixture to create server with options.

    Can be called several times during test.
    """
    def _create_server(server_name, *args, **kwgs):
        return create_servers([server_name], *args, **kwgs)[0]

    return _create_server


@pytest.fixture
def server(create_server, image):
    """Fixture to create server with default options before test."""
    server_name = next(generate_ids('server'))
    return create_server(server_name, image)
