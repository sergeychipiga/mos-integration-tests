"""
Swift steps.

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


class SwiftSteps(object):
    """Swift steps."""

    def __init__(self, client):
        """Constructor."""
        self._client = client

    def container_create(self, container_name, check=True):
        """Step to create container."""
        self._client.container_create(container_name)

        if check:
            assert self.is_container_present(container_name)

    def container_delete(self, container_name, check=True):
        """Step to delete container."""
        self._client.container_delete(container_name)

        if check:
            assert not self.is_container_present(container_name)

    def is_container_present(self, container_name):
        """Step to detect whether container present."""
        containers = self._client.container_list()
        for container in containers:
            if container_name == container['Name']:
                return True
        else:
            return False
