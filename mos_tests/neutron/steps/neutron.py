"""
Neutron steps.

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

from mos_tests.functions.common import wait
from mos_tests.steps import BaseSteps

__all__ = [
    "NeutronSteps"
]


class NeutronSteps(BaseSteps):
    """Neutron steps."""

    def create_network(self, network_name, check=True):
        """Step to create network."""
        network = self._client.create(network_name)['network']

        if check:
            self.check_network_presence(network)

        return network

    def delete_network(self, network, check=True):
        """Step to delete network."""
        self._client.delete(network['id'])

        if check:
            self.check_network_presence(network, present=False)

    def check_network_presence(self, network, present=True, timeout=0):
        """Verify step to check network is present."""
        def predicate():
            try:
                self._client.get(network['id'])
                return present
            except Exception:
                return not present

        wait(predicate, timeout_seconds=timeout)

    def find(self, name):
        """Step to find network."""
        networks = self._client.list_networks()['networks']
        for network in networks:
            if network['name'] == name:
                return network
        else:
            raise LookupError("Network {!r} is absent".format(name))

    def network_id_by_mac(self, mac):
        return self._client.list_ports(
            mac_address=mac)['ports'][0]['network_id']

    def dhcp_host_by_network(self, net_id, filter_attr='host', is_alive=True):
        filter_fn = lambda x: x[filter_attr] if filter_attr else x
        result = self._client.list_dhcp_agent_hosting_networks(net_id)
        nodes = [filter_fn(node) for node in result['agents']
                 if node['alive'] == is_alive]
        return nodes[0]
