"""
Openstack fixtures.

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

from keystoneclient.auth.identity.v2 import Password
from keystoneclient import session as _session
import pytest

from mos_tests import settings

__all__ = [
    'session'
]


@pytest.fixture
def session(auth_url):
    """Fixture to get session."""
    auth = Password(username=settings.KEYSTONE_CREDS['username'],
                    password=settings.KEYSTONE_CREDS['password'],
                    auth_url=auth_url,
                    tenant_name=settings.KEYSTONE_CREDS['tenant_name'])
    return _session.Session(auth=auth)
