# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import patch

import ceph_radosgw_context as context
import charmhelpers.contrib.storage.linux.ceph as ceph
import charmhelpers.fetch as fetch

from test_utils import CharmTestCase

TO_PATCH = [
    'config',
    'log',
    'relation_get',
    'relation_ids',
    'related_units',
    'cmp_pkgrevno',
    'arch',
    'socket',
    'unit_public_ip',
    'determine_api_port',
    'cmp_pkgrevno',
    'leader_get',
    'multisite',
    'utils',
]


class HAProxyContextTests(CharmTestCase):
    def setUp(self):
        super(HAProxyContextTests, self).setUp(context, TO_PATCH)
        self.relation_get.side_effect = self.test_relation.get
        self.config.side_effect = self.test_config.get
        self.cmp_pkgrevno.return_value = 1
        self.arch.return_value = 'amd64'

    @patch('charmhelpers.contrib.openstack.context.get_relation_ip')
    @patch('charmhelpers.contrib.openstack.context.mkdir')
    @patch('charmhelpers.contrib.openstack.context.local_unit')
    @patch('charmhelpers.contrib.openstack.context.config')
    @patch('charmhelpers.contrib.hahelpers.cluster.config_get')
    @patch('charmhelpers.contrib.openstack.context.relation_ids')
    @patch('charmhelpers.contrib.hahelpers.cluster.relation_ids')
    def test_ctxt(self, _harelation_ids, _ctxtrelation_ids, _haconfig,
                  _ctxtconfig, _local_unit, _mkdir, _get_relation_ip):
        _get_relation_ip.return_value = '10.0.0.10'
        _ctxtconfig.side_effect = self.test_config.get
        _haconfig.side_effect = self.test_config.get
        _harelation_ids.return_value = []
        haproxy_context = context.HAProxyContext()
        self.utils.listen_port.return_value = 80
        self.determine_api_port.return_value = 70
        expect = {
            'cephradosgw_bind_port': 70,
            'service_ports': {'cephradosgw-server': [80, 70]},
            'backend_options': {'cephradosgw-server': [{
                'option': 'httpchk GET /swift/healthcheck',
            }]},
            'https': False
        }
        self.assertEqual(expect, haproxy_context())


class MonContextTest(CharmTestCase):
    maxDiff = None

    def setUp(self):
        super(MonContextTest, self).setUp(context, TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.unit_public_ip.return_value = '10.255.255.255'
        self.cmp_pkgrevno.side_effect = lambda *args: 1
        self.arch.return_value = 'amd64'
        self.test_config.set('zonegroup', 'zonegroup1')
        self.test_config.set('realm', 'realmX')

    @staticmethod
    def plain_list_stub(key):
        if key == "zone":
            return ["default"]
        if key == "zonegroup":
            return ["zonegroup1"]
        if key == "realm":
            return ["realmX"]
        else:
            return []

    @patch('charmhelpers.contrib.hahelpers.cluster.relation_ids')
    @patch('charmhelpers.contrib.hahelpers.cluster.config_get')
    @patch.object(ceph, 'config', lambda *args:
                  '{"client.radosgw.gateway": {"rgw init timeout": 60}}')
    @patch.object(context, 'ensure_host_resolvable_v6')
    def test_ctxt(
        self, mock_ensure_rsv_v6, mock_config_get, mock_relation_ids
    ):
        mock_relation_ids.return_value = []
        mock_config_get.side_effect = self.test_config.get
        self.socket.gethostname.return_value = 'testhost'
        mon_ctxt = context.MonContext()
        addresses = ['10.5.4.1', '10.5.4.2', '10.5.4.3']

        def _relation_get(attr, unit, rid):
            if attr == 'ceph-public-address':
                return addresses.pop()
            elif attr == 'auth':
                return 'cephx'
            elif attr == 'rgw.testhost_key':
                return 'testkey'
            elif attr == 'fsid':
                return 'testfsid'

        self.relation_get.side_effect = _relation_get
        self.relation_ids.return_value = ['mon:6']
        self.related_units.return_value = ['ceph/0', 'ceph/1', 'ceph/2']
        self.multisite.plain_list = self.plain_list_stub
        self.determine_api_port.return_value = 70
        expect = {
            'auth_supported': 'cephx',
            'hostname': 'testhost',
            'mon_hosts': '10.5.4.1 10.5.4.2 10.5.4.3',
            'old_auth': False,
            'systemd_rgw': True,
            'unit_public_ip': '10.255.255.255',
            'use_syslog': 'false',
            'loglevel': 1,
            'port': 70,
            'client_radosgw_gateway': {'rgw init timeout': 60},
            'ipv6': False,
            'rgw_zone': 'default',
            'fsid': 'testfsid',
            'rgw_swift_versioning': False,
            'frontend': 'beast',
            'relaxed_s3_bucket_names': False,
            'rgw_zonegroup': 'zonegroup1',
            'rgw_realm': 'realmX',
            'behind_https_proxy': False,
            'virtual_hosted_bucket_enabled': False,
        }
        self.assertEqual(expect, mon_ctxt())
        self.assertFalse(mock_ensure_rsv_v6.called)

        self.test_config.set('prefer-ipv6', True)
        addresses = ['10.5.4.1', '10.5.4.2', '10.5.4.3']
        expect['ipv6'] = True
        expect['port'] = "[::]:%s" % (70)
        self.assertEqual(expect, mon_ctxt())
        self.assertTrue(mock_ensure_rsv_v6.called)

    @patch('ceph_radosgw_context.https')
    @patch.object(ceph, 'config', lambda *args:
                  '{"client.radosgw.gateway": {"rgw init timeout": 60}}')
    @patch.object(context, 'ensure_host_resolvable_v6')
    def test_ctxt_with_https_proxy(self, mock_ensure_rsv_v6, mock_https):
        mock_https.return_value = True
        self.socket.gethostname.return_value = 'testhost'
        mon_ctxt = context.MonContext()
        addresses = ['10.5.4.1', '10.5.4.2', '10.5.4.3']

        def _relation_get(attr, unit, rid):
            if attr == 'ceph-public-address':
                return addresses.pop()
            elif attr == 'auth':
                return 'cephx'
            elif attr == 'rgw.testhost_key':
                return 'testkey'
            elif attr == 'fsid':
                return 'testfsid'

        self.relation_get.side_effect = _relation_get
        self.relation_ids.return_value = ['mon:6']
        self.related_units.return_value = ['ceph/0', 'ceph/1', 'ceph/2']
        self.multisite.plain_list = self.plain_list_stub
        self.determine_api_port.return_value = 70
        expect = {
            'auth_supported': 'cephx',
            'hostname': 'testhost',
            'mon_hosts': '10.5.4.1 10.5.4.2 10.5.4.3',
            'old_auth': False,
            'systemd_rgw': True,
            'unit_public_ip': '10.255.255.255',
            'use_syslog': 'false',
            'loglevel': 1,
            'port': 70,
            'client_radosgw_gateway': {'rgw init timeout': 60},
            'ipv6': False,
            'rgw_zone': 'default',
            'fsid': 'testfsid',
            'rgw_swift_versioning': False,
            'frontend': 'beast',
            'relaxed_s3_bucket_names': False,
            'rgw_zonegroup': 'zonegroup1',
            'rgw_realm': 'realmX',
            'behind_https_proxy': True,
            'virtual_hosted_bucket_enabled': False,
        }
        self.assertEqual(expect, mon_ctxt())
        self.assertFalse(mock_ensure_rsv_v6.called)

        self.test_config.set('prefer-ipv6', True)
        addresses = ['10.5.4.1', '10.5.4.2', '10.5.4.3']
        expect['ipv6'] = True
        expect['port'] = "[::]:%s" % (70)
        self.assertEqual(expect, mon_ctxt())
        self.assertTrue(mock_ensure_rsv_v6.called)

    @patch('charmhelpers.contrib.hahelpers.cluster.relation_ids')
    @patch('charmhelpers.contrib.hahelpers.cluster.config_get')
    @patch.object(ceph, 'config', lambda *args:
                  '{"client.radosgw.gateway": {"rgw init timeout": 60}}')
    @patch.object(context, 'ensure_host_resolvable_v6')
    def test_list_of_addresses_from_ceph_proxy(
        self, mock_ensure_rsv_v6, mock_config_get, mock_relation_ids
    ):
        mock_relation_ids.return_value = []
        mock_config_get.side_effect = self.test_config.get
        self.socket.gethostname.return_value = 'testhost'
        mon_ctxt = context.MonContext()
        addresses = ['10.5.4.1 10.5.4.2 10.5.4.3']
        self.cmp_pkgrevno.return_value = 1

        def _relation_get(attr, unit, rid):
            if attr == 'ceph-public-address':
                return addresses.pop()
            elif attr == 'auth':
                return 'cephx'
            elif attr == 'rgw.testhost_key':
                return 'testkey'
            elif attr == 'fsid':
                return 'testfsid'

        self.relation_get.side_effect = _relation_get
        self.relation_ids.return_value = ['mon:6']
        self.multisite.plain_list = self.plain_list_stub
        self.related_units.return_value = ['ceph-proxy/0']
        self.determine_api_port.return_value = 70
        expect = {
            'auth_supported': 'cephx',
            'hostname': 'testhost',
            'mon_hosts': '10.5.4.1 10.5.4.2 10.5.4.3',
            'old_auth': False,
            'systemd_rgw': True,
            'unit_public_ip': '10.255.255.255',
            'use_syslog': 'false',
            'loglevel': 1,
            'port': 70,
            'client_radosgw_gateway': {'rgw init timeout': 60},
            'ipv6': False,
            'rgw_zone': 'default',
            'fsid': 'testfsid',
            'rgw_swift_versioning': False,
            'frontend': 'beast',
            'relaxed_s3_bucket_names': False,
            'rgw_zonegroup': 'zonegroup1',
            'rgw_realm': 'realmX',
            'behind_https_proxy': False,
            'virtual_hosted_bucket_enabled': False,
        }
        self.assertEqual(expect, mon_ctxt())
        self.assertFalse(mock_ensure_rsv_v6.called)

        self.test_config.set('prefer-ipv6', True)
        addresses = ['10.5.4.1 10.5.4.2 10.5.4.3']
        expect['ipv6'] = True
        expect['port'] = "[::]:%s" % (70)
        self.assertEqual(expect, mon_ctxt())
        self.assertTrue(mock_ensure_rsv_v6.called)

    @patch.object(context, 'format_ipv6_addr', lambda *_: None)
    @patch('ceph_radosgw_context.https')
    @patch('charmhelpers.contrib.hahelpers.cluster.relation_ids')
    @patch('charmhelpers.contrib.hahelpers.cluster.config_get')
    @patch.object(ceph, 'config', lambda *args:
                  '{"client.radosgw.gateway": {"rgw init timeout": 60}}')
    def test_ctxt_missing_data(
        self, mock_config_get, mock_relation_ids, mock_https
    ):
        mock_https.return_value = True
        mock_relation_ids.return_value = []
        mock_config_get.side_effect = self.test_config.get
        self.socket.gethostname.return_value = 'testhost'
        mon_ctxt = context.MonContext()
        self.relation_get.return_value = None
        self.relation_ids.return_value = ['mon:6']
        self.related_units.return_value = ['ceph/0', 'ceph/1', 'ceph/2']
        self.assertEqual({}, mon_ctxt())

    @patch('charmhelpers.contrib.hahelpers.cluster.relation_ids')
    @patch('charmhelpers.contrib.hahelpers.cluster.config_get')
    @patch.object(ceph, 'config', lambda *args:
                  '{"client.radosgw.gateway": {"rgw init timeout": 60}}')
    def test_ctxt_inconsistent_auths(self, mock_config_get, mock_relation_ids):
        mock_relation_ids.return_value = []
        mock_config_get.side_effect = self.test_config.get
        self.socket.gethostname.return_value = 'testhost'
        mon_ctxt = context.MonContext()
        addresses = ['10.5.4.1', '10.5.4.2', '10.5.4.3']
        auths = ['cephx', 'cephy', 'cephz']

        def _relation_get(attr, unit, rid):
            if attr == 'ceph-public-address':
                return addresses.pop()
            elif attr == 'auth':
                return auths.pop()
            elif attr == 'rgw.testhost_key':
                return 'testkey'
            elif attr == 'fsid':
                return 'testfsid'

        self.relation_get.side_effect = _relation_get
        self.relation_ids.return_value = ['mon:6']
        self.related_units.return_value = ['ceph/0', 'ceph/1', 'ceph/2']
        self.multisite.plain_list = self.plain_list_stub
        self.determine_api_port.return_value = 70
        expect = {
            'auth_supported': 'none',
            'hostname': 'testhost',
            'mon_hosts': '10.5.4.1 10.5.4.2 10.5.4.3',
            'old_auth': False,
            'systemd_rgw': True,
            'unit_public_ip': '10.255.255.255',
            'use_syslog': 'false',
            'loglevel': 1,
            'port': 70,
            'client_radosgw_gateway': {'rgw init timeout': 60},
            'ipv6': False,
            'rgw_zone': 'default',
            'fsid': 'testfsid',
            'rgw_swift_versioning': False,
            'frontend': 'beast',
            'relaxed_s3_bucket_names': False,
            'rgw_zonegroup': 'zonegroup1',
            'rgw_realm': 'realmX',
            'behind_https_proxy': False,
            'virtual_hosted_bucket_enabled': False,
        }
        self.assertEqual(expect, mon_ctxt())

    @patch('charmhelpers.contrib.hahelpers.cluster.relation_ids')
    @patch('charmhelpers.contrib.hahelpers.cluster.config_get')
    @patch.object(ceph, 'config', lambda *args:
                  '{"client.radosgw.gateway": {"rgw init timeout": 60}}')
    def test_ctxt_consistent_auths(self, mock_config_get, mock_relation_ids):
        mock_relation_ids.return_value = []
        mock_config_get.side_effect = self.test_config.get
        self.socket.gethostname.return_value = 'testhost'
        mon_ctxt = context.MonContext()
        addresses = ['10.5.4.1', '10.5.4.2', '10.5.4.3']
        auths = ['cephx', 'cephx', 'cephx']

        def _relation_get(attr, unit, rid):
            if attr == 'ceph-public-address':
                return addresses.pop()
            elif attr == 'auth':
                return auths.pop()
            elif attr == 'rgw.testhost_key':
                return 'testkey'
            elif attr == 'fsid':
                return 'testfsid'

        self.relation_get.side_effect = _relation_get
        self.relation_ids.return_value = ['mon:6']
        self.related_units.return_value = ['ceph/0', 'ceph/1', 'ceph/2']
        self.determine_api_port.return_value = 70
        self.multisite.plain_list = self.plain_list_stub
        expect = {
            'auth_supported': 'cephx',
            'hostname': 'testhost',
            'mon_hosts': '10.5.4.1 10.5.4.2 10.5.4.3',
            'old_auth': False,
            'systemd_rgw': True,
            'unit_public_ip': '10.255.255.255',
            'use_syslog': 'false',
            'loglevel': 1,
            'port': 70,
            'client_radosgw_gateway': {'rgw init timeout': 60},
            'ipv6': False,
            'rgw_zone': 'default',
            'fsid': 'testfsid',
            'rgw_swift_versioning': False,
            'frontend': 'beast',
            'relaxed_s3_bucket_names': False,
            'rgw_zonegroup': 'zonegroup1',
            'rgw_realm': 'realmX',
            'behind_https_proxy': False,
            'virtual_hosted_bucket_enabled': False,
        }
        self.assertEqual(expect, mon_ctxt())

    def test_resolve_http_frontend(self):
        _test_version = '12.2.0'

        def _compare_version(package, version):
            return fetch.apt_pkg.version_compare(
                _test_version, version
            )

        # Older releases, default and invalid configuration
        self.cmp_pkgrevno.side_effect = _compare_version
        self.assertEqual('civetweb', context.resolve_http_frontend())

        # Default for Octopus but not Pacific
        _test_version = '15.2.0'
        self.assertEqual('beast', context.resolve_http_frontend())

        self.arch.return_value = 's390x'
        self.assertEqual('civetweb', context.resolve_http_frontend())

        # Default for Pacific and later
        _test_version = '16.2.0'
        self.assertEqual('beast', context.resolve_http_frontend())
        self.arch.return_value = 'amd64'
        self.assertEqual('beast', context.resolve_http_frontend())

    def test_validate_http_frontend(self):
        _test_version = '12.2.0'

        def _compare_version(package, version):
            return fetch.apt_pkg.version_compare(
                _test_version, version
            )

        self.cmp_pkgrevno.side_effect = _compare_version

        # Invalid configuration option
        with self.assertRaises(ValueError):
            context.validate_http_frontend('foobar')

        # beast config but ceph pre mimic
        with self.assertRaises(ValueError):
            context.validate_http_frontend('beast')

        # Mimic with valid configuration
        _test_version = '13.2.0'
        context.validate_http_frontend('beast')
        context.validate_http_frontend('civetweb')

        # beast config on unsupported s390x/octopus
        _test_version = '15.2.0'
        self.arch.return_value = 's390x'
        with self.assertRaises(ValueError):
            context.validate_http_frontend('beast')

        # beast config on s390x/pacific
        _test_version = '16.2.0'
        context.validate_http_frontend('beast')

    @patch('charmhelpers.contrib.hahelpers.cluster.relation_ids')
    @patch('charmhelpers.contrib.hahelpers.cluster.config_get')
    @patch.object(ceph, 'config', lambda *args:
                  '{"client.radosgw.gateway": {"rgw init timeout": 60}}')
    def test_ctxt_inconsistent_fsids(self, mock_config_get, mock_relation_ids):
        mock_relation_ids.return_value = []
        mock_config_get.side_effect = self.test_config.get
        self.socket.gethostname.return_value = 'testhost'
        mon_ctxt = context.MonContext()
        addresses = ['10.5.4.1', '10.5.4.2', '10.5.4.3']
        fsids = ['testfsid', 'testfsid', None]

        def _relation_get(attr, unit, rid):
            if attr == 'ceph-public-address':
                return addresses.pop()
            elif attr == 'auth':
                return 'cephx'
            elif attr == 'rgw.testhost_key':
                return 'testkey'
            elif attr == 'fsid':
                return fsids.pop()

        self.relation_get.side_effect = _relation_get
        self.relation_ids.return_value = ['mon:6']
        self.related_units.return_value = ['ceph/0', 'ceph/1', 'ceph/2']
        self.multisite.plain_list = self.plain_list_stub
        self.determine_api_port.return_value = 70
        expect = {
            'auth_supported': 'cephx',
            'hostname': 'testhost',
            'mon_hosts': '10.5.4.1 10.5.4.2 10.5.4.3',
            'old_auth': False,
            'systemd_rgw': True,
            'unit_public_ip': '10.255.255.255',
            'use_syslog': 'false',
            'loglevel': 1,
            'port': 70,
            'client_radosgw_gateway': {'rgw init timeout': 60},
            'ipv6': False,
            'rgw_zone': 'default',
            'fsid': 'testfsid',
            'rgw_swift_versioning': False,
            'frontend': 'beast',
            'relaxed_s3_bucket_names': False,
            'rgw_zonegroup': 'zonegroup1',
            'rgw_realm': 'realmX',
            'behind_https_proxy': False,
            'virtual_hosted_bucket_enabled': False,
        }
        self.assertEqual(expect, mon_ctxt())

    @patch('ceph_radosgw_context.https')
    @patch('charmhelpers.contrib.hahelpers.cluster.relation_ids')
    @patch('charmhelpers.contrib.hahelpers.cluster.config_get')
    @patch.object(ceph, 'config', lambda *args:
                  '{"client.radosgw.gateway": {"rgw init timeout": 60}}')
    def test_ctxt_virtual_hosted_bucket(self, mock_config_get,
                                        mock_relation_ids, mock_https):
        mock_https.return_value = False
        mock_relation_ids.return_value = []
        self.test_config.set('virtual-hosted-bucket-enabled', True)
        self.test_config.set('os-public-hostname', 'rgw.example.com')
        mock_config_get.side_effect = self.test_config.get
        self.socket.gethostname.return_value = 'testhost'
        mon_ctxt = context.MonContext()
        addresses = ['10.5.4.1', '10.5.4.2', '10.5.4.3']

        def _relation_get(attr, unit, rid):
            if attr == 'ceph-public-address':
                return addresses.pop()
            elif attr == 'auth':
                return 'cephx'
            elif attr == 'rgw.testhost_key':
                return 'testkey'
            elif attr == 'fsid':
                return 'testfsid'

        self.relation_get.side_effect = _relation_get
        self.relation_ids.return_value = ['mon:6']
        self.related_units.return_value = ['ceph/0', 'ceph/1', 'ceph/2']
        self.multisite.plain_list = self.plain_list_stub
        self.determine_api_port.return_value = 70
        expect = {
            'auth_supported': 'cephx',
            'hostname': 'testhost',
            'mon_hosts': '10.5.4.1 10.5.4.2 10.5.4.3',
            'old_auth': False,
            'systemd_rgw': True,
            'unit_public_ip': '10.255.255.255',
            'use_syslog': 'false',
            'loglevel': 1,
            'port': 70,
            'client_radosgw_gateway': {'rgw init timeout': 60},
            'ipv6': False,
            'rgw_zone': 'default',
            'fsid': 'testfsid',
            'rgw_swift_versioning': False,
            'frontend': 'beast',
            'relaxed_s3_bucket_names': False,
            'rgw_zonegroup': 'zonegroup1',
            'rgw_realm': 'realmX',
            'behind_https_proxy': False,
            'virtual_hosted_bucket_enabled': True,
            'public_hostname': 'rgw.example.com',
        }
        self.assertEqual(expect, mon_ctxt())


class ApacheContextTest(CharmTestCase):

    def setUp(self):
        super(ApacheContextTest, self).setUp(context, TO_PATCH)
        self.config.side_effect = self.test_config.get
