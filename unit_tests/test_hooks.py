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
import json
import os
from unittest.mock import (
    patch, call, MagicMock, ANY
)

from test_utils import (
    CharmTestCase,
)
from charmhelpers.contrib.openstack.ip import PUBLIC

with patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    with patch('charmhelpers.fetch.apt_install'):
        with patch('utils.register_configs'):
            import hooks as ceph_hooks

TO_PATCH = [
    'CONFIGS',
    'add_source',
    'apt_update',
    'apt_install',
    'apt_purge',
    'config',
    'cmp_pkgrevno',
    'execd_preinstall',
    'listen_port',
    'log',
    'open_port',
    'opened_ports',
    'os',
    'relation_ids',
    'relation_set',
    'relation_get',
    'related_units',
    'remote_service_name',
    'status_set',
    'subprocess',
    'sys',
    'generate_ha_relation_data',
    'get_relation_ip',
    'disable_unused_apache_sites',
    'service_reload',
    'service_stop',
    'service_restart',
    'service_pause',
    'service_resume',
    'service',
    'service_name',
    'socket',
    'restart_map',
    'systemd_based_radosgw',
    'request_per_unit_key',
    'get_certificate_request',
    'process_certificates',
    'filter_installed_packages',
    'filter_missing_packages',
    'ceph_utils',
    'multisite_deployment',
    'multisite',
    'ready_for_service',
]


# Stub Methods
def get_zonegroup_stub():
    # populate dummy zones info
    zone_one = {}
    zone_one['id'] = "test_zone_id_one"
    zone_one['name'] = "testzone"

    zone_two = {}
    zone_two['id'] = "test_zone_id_two"
    zone_two['name'] = "testzone_two"

    # populate dummy zonegroup info
    zonegroup = {}
    zonegroup['name'] = "testzonegroup"
    zonegroup['master_zone'] = "test_zone_id_one"
    zonegroup['zones'] = [zone_one, zone_two]
    return zonegroup


class CephRadosGWTests(CharmTestCase):

    def setUp(self):
        super(CephRadosGWTests, self).setUp(ceph_hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.test_config.set('source', 'distro')
        self.test_config.set('key', 'secretkey')
        self.test_config.set('use-syslog', False)
        self.cmp_pkgrevno.return_value = 0
        self.service_name.return_value = 'radosgw'
        self.request_per_unit_key.return_value = False
        self.systemd_based_radosgw.return_value = False
        self.filter_installed_packages.side_effect = lambda pkgs: pkgs
        self.filter_missing_packages.side_effect = lambda pkgs: pkgs
        self.multisite_deployment.return_value = False

    def test_upgrade_available(self):
        _vers = {
            'distro': 'luminous',
            'cloud:bionic-rocky': 'mimic',
        }
        mock_config = MagicMock()
        self.test_config.set('source', 'cloud:bionic-rocky')
        mock_config.get.side_effect = self.test_config.get
        mock_config.previous.return_value = 'distro'
        self.config.side_effect = None
        self.config.return_value = mock_config
        self.ceph_utils.UPGRADE_PATHS = {
            'luminous': 'mimic',
        }
        self.ceph_utils.resolve_ceph_version.side_effect = (
            lambda v: _vers.get(v)
        )
        self.assertTrue(ceph_hooks.upgrade_available())

    @patch.object(ceph_hooks, 'upgrade_available')
    def test_install_packages(self, upgrade_available):
        mock_config = MagicMock()
        mock_config.get.side_effect = self.test_config.get
        mock_config.changed.return_value = True
        self.config.side_effect = None
        self.config.return_value = mock_config
        upgrade_available.return_value = False
        ceph_hooks.install_packages()
        self.add_source.assert_called_with('distro', 'secretkey')
        self.apt_update.assert_called_with(fatal=True)
        self.apt_purge.assert_called_with(ceph_hooks.APACHE_PACKAGES)
        self.apt_install.assert_called_with(ceph_hooks.PACKAGES,
                                            fatal=True)
        mock_config.changed.assert_called_with('source')
        self.filter_installed_packages.assert_called_with(
            ceph_hooks.PACKAGES
        )
        self.filter_missing_packages.assert_called_with(
            ceph_hooks.APACHE_PACKAGES
        )

    @patch.object(ceph_hooks, 'upgrade_available')
    def test_install_packages_upgrades(self, upgrade_available):
        mock_config = MagicMock()
        mock_config.get.side_effect = self.test_config.get
        mock_config.changed.return_value = True
        self.config.side_effect = None
        self.config.return_value = mock_config
        upgrade_available.return_value = True
        ceph_hooks.install_packages()
        self.add_source.assert_called_with('distro', 'secretkey')
        self.apt_update.assert_called_with(fatal=True)
        self.apt_purge.assert_called_with(ceph_hooks.APACHE_PACKAGES)
        self.apt_install.assert_called_with(ceph_hooks.PACKAGES,
                                            fatal=True)
        mock_config.changed.assert_called_with('source')
        self.filter_installed_packages.assert_not_called()
        self.filter_missing_packages.assert_called_with(
            ceph_hooks.APACHE_PACKAGES
        )

    @patch.object(ceph_hooks, 'leader_set')
    @patch.object(ceph_hooks, 'is_leader')
    def test_install(self, is_leader, leader_set):
        _install_packages = self.patch('install_packages')
        is_leader.return_value = True
        ceph_hooks.install()
        self.assertTrue(self.execd_preinstall.called)
        self.assertTrue(_install_packages.called)
        is_leader.assert_called_once()
        leader_set.assert_called_once_with(namespace_tenants=False)
        self.service_pause.assert_called_once_with('radosgw')

    @patch.object(ceph_hooks, 'leader_set')
    @patch.object(ceph_hooks, 'is_leader')
    def test_install_without_namespacing(self, is_leader, leader_set):
        _install_packages = self.patch('install_packages')
        is_leader.return_value = True
        self.test_config.set('namespace-tenants', True)
        ceph_hooks.install()
        self.assertTrue(self.execd_preinstall.called)
        self.assertTrue(_install_packages.called)
        is_leader.assert_called_once()
        leader_set.assert_called_once_with(namespace_tenants=True)
        self.service_pause.assert_called_once_with('radosgw')

    @patch.object(ceph_hooks, 'certs_joined')
    @patch.object(ceph_hooks, 'update_nrpe_config')
    def test_config_changed(self, update_nrpe_config, mock_certs_joined):
        _install_packages = self.patch('install_packages')
        _relations = {
            'certificates': ['certificates:1']
        }
        self.relation_ids.side_effect = lambda name: _relations.get(name, [])
        ceph_hooks.config_changed()
        self.assertTrue(_install_packages.called)
        self.CONFIGS.write_all.assert_called_with()
        update_nrpe_config.assert_called_with()
        mock_certs_joined.assert_called_once_with('certificates:1')

    @patch.object(ceph_hooks, 'service_name')
    @patch.object(ceph_hooks, 'service_restart')
    @patch.object(ceph_hooks, 'certs_joined')
    @patch.object(ceph_hooks, 'update_nrpe_config')
    def test_config_changed_upgrade(self, update_nrpe_config,
                                    mock_certs_joined, mock_service_restart,
                                    mock_service_name):
        _install_packages = self.patch('install_packages')
        _install_packages.return_value = True
        mock_service_name.return_value = 'radosgw@localhost'
        _relations = {
            'certificates': ['certificates:1']
        }
        self.relation_ids.side_effect = lambda name: _relations.get(name, [])
        ceph_hooks.config_changed()
        self.assertTrue(_install_packages.called)
        self.CONFIGS.write_all.assert_called_with()
        update_nrpe_config.assert_called_with()
        mock_certs_joined.assert_called_once_with('certificates:1')
        mock_service_restart.assert_called_once_with('radosgw@localhost')

    @patch.object(ceph_hooks, 'is_request_complete',
                  lambda *args, **kwargs: True)
    @patch.object(ceph_hooks, 'is_leader')
    @patch('charmhelpers.contrib.openstack.ip.resolve_address')
    @patch('charmhelpers.contrib.openstack.ip.config')
    def test_mon_relation(self, _config, _resolve_address, is_leader):
        _ceph = self.patch('ceph')
        _ceph.import_radosgw_key.return_value = True
        is_leader.return_value = True
        self.relation_get.return_value = 'seckey'
        self.multisite.list_zones.side_effect = [
            [],           # at first the default zone doesn't exist, then...
            ['default'],  # ... it got created
        ]
        self.socket.gethostname.return_value = 'testinghostname'
        ceph_hooks.mon_relation()
        self.relation_set.assert_not_called()
        self.service_resume.assert_called_once_with('radosgw')
        _ceph.import_radosgw_key.assert_called_with('seckey',
                                                    name='rgw.testinghostname')
        self.CONFIGS.write_all.assert_called_with()

    @patch.object(ceph_hooks, 'is_request_complete',
                  lambda *args, **kwargs: True)
    @patch.object(ceph_hooks, 'is_leader')
    @patch('charmhelpers.contrib.openstack.ip.resolve_address')
    @patch('charmhelpers.contrib.openstack.ip.config')
    def test_mon_relation_request_key(self, _config,
                                      _resolve_address, is_leader):
        _ceph = self.patch('ceph')
        _ceph.import_radosgw_key.return_value = True
        is_leader.return_value = True
        self.relation_get.return_value = 'seckey'
        self.multisite.list_zones.side_effect = [
            [],           # at first the default zone doesn't exist, then...
            ['default'],  # ... it got created
        ]
        self.socket.gethostname.return_value = 'testinghostname'
        self.request_per_unit_key.return_value = True
        ceph_hooks.mon_relation()
        self.relation_set.assert_called_with(
            relation_id=None,
            key_name='rgw.testinghostname'
        )
        self.service_resume.assert_called_once_with('radosgw')
        _ceph.import_radosgw_key.assert_called_with('seckey',
                                                    name='rgw.testinghostname')
        self.CONFIGS.write_all.assert_called_with()

    @patch.object(ceph_hooks, 'is_request_complete',
                  lambda *args, **kwargs: True)
    @patch.object(ceph_hooks, 'is_leader')
    @patch('charmhelpers.contrib.openstack.ip.resolve_address')
    @patch('charmhelpers.contrib.openstack.ip.config')
    def test_mon_relation_nokey(self, _config,
                                _resolve_address, is_leader):
        _ceph = self.patch('ceph')
        _ceph.import_radosgw_key.return_value = False
        self.relation_get.return_value = None
        is_leader.return_value = True
        self.multisite.list_zones.side_effect = [
            [],           # at first the default zone doesn't exist, then...
            ['default'],  # ... it got created
        ]
        ceph_hooks.mon_relation()
        self.assertFalse(_ceph.import_radosgw_key.called)
        self.service_resume.assert_not_called()
        self.CONFIGS.write_all.assert_called_with()

    @patch.object(ceph_hooks, 'send_request_if_needed')
    @patch.object(ceph_hooks, 'is_request_complete',
                  lambda *args, **kwargs: False)
    def test_mon_relation_send_broker_request(self,
                                              mock_send_request_if_needed):
        _ceph = self.patch('ceph')
        _ceph.import_radosgw_key.return_value = False
        self.relation_get.return_value = 'seckey'
        ceph_hooks.mon_relation()
        self.service_resume.assert_not_called()
        self.assertFalse(_ceph.import_radosgw_key.called)
        self.assertFalse(self.CONFIGS.called)
        self.assertTrue(mock_send_request_if_needed.called)

    def test_gateway_relation(self):
        self.get_relation_ip.return_value = '10.0.0.1'
        self.listen_port.return_value = 80
        ceph_hooks.gateway_relation()
        self.relation_set.assert_called_with(hostname='10.0.0.1', port=80)

    @patch.object(ceph_hooks, "canonical_url")
    def test_object_store_relation(self, _canonical_url):
        relation_data = {
            "swift-url": "http://radosgw:80"
        }
        self.listen_port.return_value = 80
        _canonical_url.return_value = "http://radosgw"
        ceph_hooks.object_store_joined()
        self.relation_set.assert_called_with(
            relation_id=None,
            relation_settings=relation_data)

    @patch.object(ceph_hooks, 'leader_get')
    @patch('charmhelpers.contrib.openstack.ip.service_name',
           lambda *args: 'ceph-radosgw')
    @patch('charmhelpers.contrib.openstack.ip.resolve_address',
           lambda *args: 'myserv')
    @patch('charmhelpers.contrib.openstack.ip.config')
    def test_identity_joined_early_version(self, _config, _leader_get):
        self.cmp_pkgrevno.return_value = -1
        _leader_get.return_value = 'False'
        self.listen_port.return_value = 80
        ceph_hooks.identity_joined()
        self.sys.exit.assert_called_with(1)

    @patch.object(ceph_hooks, 'leader_get')
    @patch('charmhelpers.contrib.openstack.ip.service_name',
           lambda *args: 'ceph-radosgw')
    @patch('charmhelpers.contrib.openstack.ip.resolve_address')
    @patch('charmhelpers.contrib.openstack.ip.config')
    def test_identity_joined(self, _config, _resolve_address, _leader_get):

        self.listen_port.return_value = 80

        def _test_identify_joined(expected):
            self.related_units = ['unit/0']
            self.cmp_pkgrevno.return_value = 1
            _resolve_address.return_value = 'myserv'
            _config.side_effect = self.test_config.get
            self.test_config.set('region', 'region1')
            _leader_get.return_value = 'False'
            ceph_hooks.identity_joined(relid='rid')
            self.relation_set.assert_has_calls([
                call(swift_service='swift',
                     swift_region='region1',
                     swift_public_url='http://myserv:80/swift/v1',
                     swift_internal_url='http://myserv:80/swift/v1',
                     swift_admin_url='http://myserv:80/swift',
                     requested_roles=expected,
                     relation_id='rid'),
                call(s3_service='s3',
                     s3_region='region1',
                     s3_public_url='http://myserv:80/',
                     s3_internal_url='http://myserv:80/',
                     s3_admin_url='http://myserv:80/',
                     relation_id='rid')
            ])

        inputs = [{'operator': 'foo', 'admin': 'bar', 'expected': 'foo,bar'},
                  {'operator': 'foo', 'expected': 'foo'},
                  {'admin': 'bar', 'expected': 'bar'},
                  {'expected': ''}]
        for input in inputs:
            self.test_config.set('operator-roles', input.get('operator', ''))
            self.test_config.set('admin-roles', input.get('admin', ''))
            _test_identify_joined(input['expected'])

    @patch.object(ceph_hooks, 'leader_get')
    @patch('charmhelpers.contrib.openstack.ip.service_name',
           lambda *args: 'ceph-radosgw')
    @patch('charmhelpers.contrib.openstack.ip.resolve_address')
    @patch('charmhelpers.contrib.openstack.ip.config')
    def test_identity_joined_namespaced(self, _config,
                                        _resolve_address, _leader_get):
        _leader_get.return_value = True

        def _test_identify_joined(expected):
            self.related_units = ['unit/0']
            self.cmp_pkgrevno.return_value = 1
            self.listen_port.return_value = 80
            _resolve_address.return_value = 'myserv'
            _config.side_effect = self.test_config.get
            self.test_config.set('region', 'region1')
            _leader_get.return_value = 'True'
            ceph_hooks.identity_joined(relid='rid')
            self.relation_set.assert_has_calls([
                call(swift_service='swift',
                     swift_region='region1',
                     swift_public_url=(
                         'http://myserv:80/swift/v1/AUTH_$(project_id)s'),
                     swift_internal_url=(
                         'http://myserv:80/swift/v1/AUTH_$(project_id)s'),
                     swift_admin_url='http://myserv:80/swift',
                     requested_roles=expected,
                     relation_id='rid'),
                call(s3_service='s3',
                     s3_region='region1',
                     s3_public_url='http://myserv:80/',
                     s3_internal_url='http://myserv:80/',
                     s3_admin_url='http://myserv:80/',
                     relation_id='rid')
            ])

        inputs = [{'operator': 'foo', 'admin': 'bar', 'expected': 'foo,bar'},
                  {'operator': 'foo', 'expected': 'foo'},
                  {'admin': 'bar', 'expected': 'bar'},
                  {'expected': ''}]
        for input in inputs:
            self.test_config.set('operator-roles', input.get('operator', ''))
            self.test_config.set('admin-roles', input.get('admin', ''))
            _test_identify_joined(input['expected'])

    @patch.object(ceph_hooks, 'leader_get')
    @patch('charmhelpers.contrib.openstack.ip.service_name',
           lambda *args: 'ceph-radosgw')
    @patch('charmhelpers.contrib.openstack.ip.is_clustered')
    @patch('charmhelpers.contrib.openstack.ip.unit_get')
    @patch('charmhelpers.contrib.openstack.ip.config')
    def test_identity_joined_public_name(self, _config, _unit_get,
                                         _is_clustered, _leader_get):
        self.related_units = ['unit/0']
        _config.side_effect = self.test_config.get
        self.test_config.set('os-public-hostname', 'files.example.com')
        _unit_get.return_value = 'myserv'
        _is_clustered.return_value = False
        _leader_get.return_value = 'False'
        self.listen_port.return_value = 80
        ceph_hooks.identity_joined(relid='rid')
        self.relation_set.assert_has_calls([
            call(swift_service='swift',
                 swift_region='RegionOne',
                 swift_public_url='http://files.example.com:80/swift/v1',
                 swift_internal_url='http://myserv:80/swift/v1',
                 swift_admin_url='http://myserv:80/swift',
                 requested_roles='Member,member,Admin',
                 relation_id='rid'),
            call(s3_service='s3',
                 s3_region='RegionOne',
                 s3_public_url='http://files.example.com:80/',
                 s3_internal_url='http://myserv:80/',
                 s3_admin_url='http://myserv:80/',
                 relation_id='rid')
        ])

    @patch.object(ceph_hooks, 'identity_joined')
    def test_identity_changed(self, mock_identity_joined):
        ceph_hooks.identity_changed()
        self.CONFIGS.write_all.assert_called_with()
        self.assertTrue(mock_identity_joined.called)

    @patch('charmhelpers.contrib.openstack.ip.is_clustered')
    @patch('charmhelpers.contrib.openstack.ip.unit_get')
    @patch('charmhelpers.contrib.openstack.ip.config')
    def test_canonical_url_ipv6(self, _config, _unit_get, _is_clustered):
        ipv6_addr = '2001:db8:85a3:8d3:1319:8a2e:370:7348'
        _config.side_effect = self.test_config.get
        _unit_get.return_value = ipv6_addr
        _is_clustered.return_value = False
        self.assertEqual(ceph_hooks.canonical_url({}, PUBLIC),
                         'http://[%s]' % ipv6_addr)

    def test_cluster_joined(self):
        self.get_relation_ip.side_effect = ['10.0.0.1',
                                            '10.0.1.1',
                                            '10.0.2.1',
                                            '10.0.3.1']
        self.test_config.set('os-public-network', '10.0.0.0/24')
        self.test_config.set('os-admin-network', '10.0.1.0/24')
        self.test_config.set('os-internal-network', '10.0.2.0/24')

        ceph_hooks.cluster_joined()
        self.relation_set.assert_has_calls(
            [call(relation_id=None,
                  relation_settings={
                      'admin-address': '10.0.0.1',
                      'public-address': '10.0.2.1',
                      'internal-address': '10.0.1.1',
                      'private-address': '10.0.3.1'})])

    @patch.object(ceph_hooks, 'certs_changed')
    def test_cluster_changed(self, mock_certs_changed):
        _id_joined = self.patch('identity_joined')
        _relations = {
            'identity-service': ['rid'],
            'certificates': ['certificates:1'],
        }
        self.relation_ids.side_effect = lambda name: _relations.get(name)
        self.related_units.return_value = ['vault/0', 'vault/1']
        ceph_hooks.cluster_changed()
        self.CONFIGS.write_all.assert_called_with()
        _id_joined.assert_called_with(relid='rid')
        mock_certs_changed.assert_has_calls([
            call('certificates:1', 'vault/0'),
            call('certificates:1', 'vault/1')
        ])

    def test_ha_relation_joined(self):
        self.generate_ha_relation_data.return_value = {
            'test': 'data'
        }
        ceph_hooks.ha_relation_joined(relation_id='ha:1')
        self.relation_set.assert_called_with(
            relation_id='ha:1',
            test='data'
        )

    def test_ha_relation_changed(self):
        _id_joined = self.patch('identity_joined')
        self.relation_get.return_value = True
        self.relation_ids.return_value = ['rid']
        ceph_hooks.ha_relation_changed()
        _id_joined.assert_called_with(relid='rid')

    def test_certs_joined(self):
        self.get_certificate_request.return_value = {'foo': 'baa'}
        ceph_hooks.certs_joined('certificates:1')
        self.relation_set.assert_called_once_with(
            relation_id='certificates:1',
            relation_settings={'foo': 'baa'}
        )
        self.get_certificate_request.assert_called_once_with()

    @patch.object(ceph_hooks, 'configure_https')
    def test_certs_changed(self, mock_configure_https):
        ceph_hooks.certs_changed('certificates:1', 'vault/0')
        self.process_certificates.assert_called_once_with(
            'ceph-radosgw',
            'certificates:1',
            'vault/0'
        )
        mock_configure_https.assert_called_once_with()

    @patch.object(ceph_hooks, 'canonical_url')
    @patch.object(ceph_hooks, 'is_leader')
    def test_radosgw_user_changed(self, is_leader, canonical_url):
        relation_data = {
            'radosgw-user:3': {'system-role': 'false'},
            'radosgw-user:5': {'system-role': 'true'}}
        user = {
            'juju-radosgw-user-3': ('access1', 'key1'),
            'juju-radosgw-user-5-system': ('access2', 'key2')}
        self.ready_for_service.return_value = True
        is_leader.return_value = True
        self.remote_service_name.return_value = 'ceph-dashboard'
        canonical_url.return_value = 'http://radosgw'
        self.listen_port.return_value = 80
        self.socket.gethostname.return_value = 'testinghostname'
        self.relation_ids.return_value = relation_data.keys()
        self.relation_get.side_effect = lambda rid, app: relation_data[rid]
        self.multisite.list_users.return_value = ['juju-radosgw-user-3']
        self.multisite.get_user_creds.side_effect = lambda u: user[u]
        self.multisite.create_user.side_effect = lambda u, system_user: user[u]
        ceph_hooks.radosgw_user_changed()
        expected = [
            call(
                app='ceph-dashboard',
                relation_id='radosgw-user:3',
                relation_settings={
                    'uid': 'juju-radosgw-user-3',
                    'access-key': 'access1',
                    'secret-key': 'key1'}),
            call(
                app='ceph-dashboard',
                relation_id='radosgw-user:5',
                relation_settings={
                    'uid': 'juju-radosgw-user-5-system',
                    'access-key': 'access2',
                    'secret-key': 'key2'}),
            call(
                relation_id='radosgw-user:3',
                relation_settings={
                    'internal-url': 'http://radosgw:80',
                    'daemon-id': 'testinghostname'}),
            call(
                relation_id='radosgw-user:5',
                relation_settings={
                    'internal-url': 'http://radosgw:80',
                    'daemon-id': 'testinghostname'})]
        self.relation_set.assert_has_calls(
            expected,
            any_order=True)


class MiscMultisiteTests(CharmTestCase):

    TO_PATCH = [
        'restart_nonce_changed',
        'relation_ids',
        'related_units',
        'leader_get',
        'is_leader',
        'primary_relation_joined',
        'primary_relation_changed',
        'secondary_relation_changed',
        'service_restart',
        'service_name',
        'multisite'
    ]

    _relation_ids = {
        'primary': ['primary:1'],
        'secondary': ['secondary:1'],
    }

    _related_units = {
        'primary:1': ['rgw/0', 'rgw/1'],
        'secondary:1': ['rgw-s/0', 'rgw-s/1'],
    }

    def setUp(self):
        super(MiscMultisiteTests, self).setUp(ceph_hooks,
                                              self.TO_PATCH)
        self.relation_ids.side_effect = (
            lambda endpoint: self._relation_ids.get(endpoint) or []
        )
        self.related_units.side_effect = (
            lambda rid: self._related_units.get(rid) or []
        )
        self.service_name.return_value = 'rgw@hostname'

    def test_leader_settings_changed(self):
        self.restart_nonce_changed.return_value = True
        self.is_leader.return_value = False
        ceph_hooks.leader_settings_changed()
        self.service_restart.assert_called_once_with('rgw@hostname')
        self.primary_relation_joined.assert_called_once_with('primary:1')

    def test_process_multisite_relations(self):
        ceph_hooks.process_multisite_relations()
        self.primary_relation_joined.assert_called_once_with('primary:1')
        self.assertEqual(self.primary_relation_changed.call_count, 2)
        self.primary_relation_changed.assert_has_calls([
            call('primary:1', 'rgw/0'),
            call('primary:1', 'rgw/1'),
        ])
        self.assertEqual(self.secondary_relation_changed.call_count, 2)
        self.secondary_relation_changed.assert_has_calls([
            call('secondary:1', 'rgw-s/0'),
            call('secondary:1', 'rgw-s/1'),
        ])


class CephRadosMultisiteTests(CharmTestCase):

    TO_PATCH = [
        'ready_for_service',
        'canonical_url',
        'relation_set',
        'relation_get',
        'leader_get',
        'listen_port',
        'config',
        'is_leader',
        'multisite',
        'leader_set',
        'service_restart',
        'service_name',
        'log',
        'multisite_deployment',
        'systemd_based_radosgw',
    ]

    def setUp(self):
        super(CephRadosMultisiteTests, self).setUp(ceph_hooks,
                                                   self.TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.ready_for_service.return_value = True
        self.canonical_url.return_value = 'http://rgw'
        self.service_name.return_value = 'rgw@hostname'
        self.multisite_deployment.return_value = True
        self.systemd_based_radosgw.return_value = True


class PrimaryMultisiteTests(CephRadosMultisiteTests):

    _complete_config = {
        'realm': 'testrealm',
        'zonegroup': 'testzonegroup',
        'zone': 'testzone',
    }

    _leader_data = {
        'access_key': 'mykey',
        'secret': 'mysecret',
    }

    _leader_data_done = {
        'access_key': 'mykey',
        'secret': 'mysecret',
        'restart_nonce': 'foobar',
    }

    def test_primary_relation_joined_missing_config(self):
        ceph_hooks.primary_relation_joined('primary:1')
        self.config.assert_has_calls([
            call('realm'),
            call('zonegroup'),
            call('zone'),
        ])
        self.relation_set.assert_not_called()

    def test_primary_relation_joined_create_everything(self):
        for k, v in self._complete_config.items():
            self.test_config.set(k, v)
        self.listen_port.return_value = 80
        self.is_leader.return_value = True
        self.leader_get.side_effect = lambda attr: self._leader_data.get(attr)
        self.multisite.list_realms.return_value = []
        self.multisite.list_zonegroups.return_value = []
        self.multisite.list_zones.return_value = []
        self.multisite.list_users.return_value = []
        self.multisite.create_system_user.return_value = (
            'mykey', 'mysecret',
        )
        ceph_hooks.primary_relation_joined('primary:1')
        self.config.assert_has_calls([
            call('realm'),
            call('zonegroup'),
            call('zone'),
        ])
        self.multisite.create_realm.assert_called_once_with(
            'testrealm',
            default=True,
        )
        self.multisite.create_zonegroup.assert_called_once_with(
            'testzonegroup',
            endpoints=['http://rgw:80'],
            default=True,
            master=True,
            realm='testrealm',
        )
        self.multisite.create_zone.assert_called_once_with(
            'testzone',
            endpoints=['http://rgw:80'],
            default=True,
            master=True,
            zonegroup='testzonegroup',
        )
        self.multisite.create_system_user.assert_called_once_with(
            ceph_hooks.MULTISITE_SYSTEM_USER
        )
        self.multisite.modify_zone.assert_called_once_with(
            'testzone',
            access_key='mykey',
            secret='mysecret',
        )
        self.multisite.update_period.assert_has_calls([
            call(fatal=False),
            call(zonegroup='testzonegroup', zone='testzone'),
        ])
        self.service_restart.assert_called_once_with('rgw@hostname')
        self.leader_set.assert_has_calls([
            call(access_key='mykey',
                 secret='mysecret'),
            call(restart_nonce=ANY),
        ])
        self.relation_set.assert_called_with(
            relation_id='primary:1',
            access_key='mykey',
            secret='mysecret',
        )

    def test_primary_relation_joined_create_nothing(self):
        for k, v in self._complete_config.items():
            self.test_config.set(k, v)
        self.is_leader.return_value = True
        self.leader_get.side_effect = (
            lambda attr: self._leader_data_done.get(attr)
        )
        self.multisite.list_realms.return_value = ['testrealm']
        self.multisite.list_zonegroups.return_value = ['testzonegroup']
        self.multisite.list_zones.return_value = ['testzone']
        self.multisite.list_users.return_value = [
            ceph_hooks.MULTISITE_SYSTEM_USER
        ]
        ceph_hooks.primary_relation_joined('primary:1')
        self.multisite.create_realm.assert_not_called()
        self.multisite.create_zonegroup.assert_not_called()
        self.multisite.create_zone.assert_not_called()
        self.multisite.create_system_user.assert_not_called()
        self.multisite.update_period.assert_not_called()
        self.service_restart.assert_not_called()
        self.leader_set.assert_not_called()

    def test_primary_relation_joined_not_leader(self):
        for k, v in self._complete_config.items():
            self.test_config.set(k, v)
        self.listen_port.return_value = 80
        self.is_leader.return_value = False
        self.leader_get.side_effect = lambda attr: self._leader_data.get(attr)
        ceph_hooks.primary_relation_joined('primary:1')
        self.relation_set.assert_called_once_with(
            relation_id='primary:1',
            realm='testrealm',
            zonegroup='testzonegroup',
            url='http://rgw:80',
            access_key='mykey',
            secret='mysecret',
        )
        self.multisite.list_realms.assert_not_called()

    def test_primary_relation_changed_sync_policy_state_unset(self):
        self.is_leader.return_value = True
        self.test_config.set('sync-policy-state', '')

        ceph_hooks.primary_relation_changed('primary:1')

        self.is_leader.assert_called_once()
        self.ready_for_service.assert_called_once_with(legacy=False)
        self.config.assert_called_once_with('sync-policy-state')

    def test_primary_relation_changed_sync_rel_data_incomplete(self):
        self.is_leader.return_value = True
        self.test_config.set('sync-policy-state', 'allowed')
        self.relation_get.return_value = {'zone': 'secondary'}

        ceph_hooks.primary_relation_changed('primary:1', 'rgw/0')

        self.is_leader.assert_called_once()
        self.ready_for_service.assert_called_once_with(legacy=False)
        self.config.assert_called_once_with('sync-policy-state')
        self.relation_get.assert_called_once_with(rid='primary:1',
                                                  unit='rgw/0')

    def test_primary_relation_changed(self):
        self.is_leader.return_value = True
        configs = {
            'sync-policy-state': 'allowed',
            'zonegroup': 'testzonegroup',
            'zone': 'zone_a',
        }
        for k, v in configs.items():
            self.test_config.set(k, v)
        self.relation_get.return_value = {
            'zone': 'zone_b',
            'sync_policy_flow_type': 'symmetrical',
            # this should force flow type to directional, and ignore the value
            # from the relation data.
            'zone_tier_type': 'cloud',
        }
        self.multisite.is_sync_group_update_needed.return_value = True
        group_test_data_file = os.path.join(
            os.path.dirname(__file__), 'testdata', 'test_get_sync_group.json')
        with open(group_test_data_file, 'r') as f:
            self.multisite.get_sync_group.return_value = json.loads(f.read())

        ceph_hooks.primary_relation_changed('primary:1', 'rgw/0')

        self.is_leader.assert_called_once()
        self.ready_for_service.assert_called_once_with(legacy=False)
        self.config.assert_has_calls([
            call('sync-policy-state'),
            call('zonegroup'),
            call('zone'),
        ])
        self.relation_get.assert_called_once_with(rid='primary:1',
                                                  unit='rgw/0')
        self.multisite.is_sync_group_update_needed.assert_called_once_with(
            group_id=ceph_hooks.MULTISITE_DEFAULT_SYNC_GROUP_ID,
            flow_id='zone_a-zone_b',
            pipe_id='zone_a-zone_b',
            source_zone='zone_a',
            dest_zone='zone_b',
            desired_status='allowed',
            desired_flow_type=self.multisite.SYNC_FLOW_DIRECTIONAL)
        self.multisite.create_sync_group.assert_called_once_with(
            group_id=ceph_hooks.MULTISITE_DEFAULT_SYNC_GROUP_ID,
            status='allowed')
        self.multisite.create_sync_group_flow.assert_called_once_with(
            group_id=ceph_hooks.MULTISITE_DEFAULT_SYNC_GROUP_ID,
            flow_id='zone_a-zone_b',
            flow_type=self.multisite.SYNC_FLOW_DIRECTIONAL,
            source_zone='zone_a', dest_zone='zone_b')
        self.multisite.create_sync_group_pipe.assert_called_once_with(
            group_id=ceph_hooks.MULTISITE_DEFAULT_SYNC_GROUP_ID,
            pipe_id='zone_a-zone_b',
            source_zones=['zone_a'], dest_zones=['zone_b'])
        self.multisite.update_period.assert_called_once_with(
            zonegroup='testzonegroup', zone='zone_a')
        self.service_restart.assert_called_once_with('rgw@hostname')
        self.leader_set.assert_called_once_with(restart_nonce=ANY)

    @patch.object(json, 'loads')
    def test_multisite_relation_departed(self, json_loads):
        for k, v in self._complete_config.items():
            self.test_config.set(k, v)
        self.is_leader.return_value = True
        # Multisite is configured at first but then disabled.
        self.multisite.is_multisite_configured.side_effect = [True, False]
        self.multisite.get_zonegroup_info.return_value = get_zonegroup_stub()
        # json.loads() raises TypeError for mock objects.
        json_loads.returnvalue = []
        ceph_hooks.multisite_relation_departed()

        self.multisite.modify_zone.assert_called_once_with(
            'testzone', default=True, master=True, zonegroup='testzonegroup'
        )
        self.multisite.update_period.assert_called_once_with(
            fatal=True, zonegroup='testzonegroup',
            zone='testzone', realm='testrealm'
        )


class SecondaryMultisiteTests(CephRadosMultisiteTests):

    _complete_config = {
        'realm': 'testrealm',
        'zonegroup': 'testzonegroup',
        'zone': 'testzone2',
        'sync-policy-flow-type': 'symmetrical',
    }

    _test_relation = {
        'realm': 'testrealm',
        'zonegroup': 'testzonegroup',
        'access_key': 'anotherkey',
        'secret': 'anothersecret',
        'url': 'http://primary:80'
    }

    _test_bad_relation = {
        'realm': 'anotherrealm',
        'zonegroup': 'anotherzg',
        'access_key': 'anotherkey',
        'secret': 'anothersecret',
        'url': 'http://primary:80'
    }

    def test_secondary_relation_changed(self):
        for k, v in self._complete_config.items():
            self.test_config.set(k, v)
        self.is_leader.return_value = True
        self.listen_port.return_value = 80
        self.leader_get.return_value = None
        self.relation_get.return_value = self._test_relation
        self.multisite.list_realms.return_value = []
        self.multisite.list_zones.return_value = []
        self.multisite.check_cluster_has_buckets.return_value = False
        ceph_hooks.secondary_relation_changed('secondary:1', 'rgw/0')
        self.config.assert_has_calls([
            call('realm'),
            call('zonegroup'),
            call('zone'),
        ])
        self.multisite.pull_realm.assert_called_once_with(
            url=self._test_relation['url'],
            access_key=self._test_relation['access_key'],
            secret=self._test_relation['secret'],
        )
        self.multisite.pull_period.assert_called_with(
            url=self._test_relation['url'],
            access_key=self._test_relation['access_key'],
            secret=self._test_relation['secret'],
        )
        self.multisite.set_default_realm.assert_called_once_with(
            'testrealm'
        )
        self.multisite.create_zone.assert_called_once_with(
            'testzone2',
            endpoints=['http://rgw:80'],
            default=False,
            master=False,
            zonegroup='testzonegroup',
            access_key=self._test_relation['access_key'],
            secret=self._test_relation['secret'],
        )
        self.multisite.update_period.assert_has_calls([
            call(fatal=False),
            call(zonegroup='testzonegroup', zone='testzone2'),
        ])
        self.service_restart.assert_called_once()
        self.leader_set.assert_called_once_with(restart_nonce=ANY)
        self.relation_set.assert_has_calls([
            call(
                relation_id='secondary:1',
                sync_policy_flow_type='symmetrical',
            ),
            call(
                relation_id='secondary:1',
                zone='testzone2',
            ),
        ])

    def test_secondary_relation_changed_incomplete_relation(self):
        for k, v in self._complete_config.items():
            self.test_config.set(k, v)
        self.is_leader.return_value = True
        self.relation_get.return_value = {}
        ceph_hooks.secondary_relation_changed('secondary:1', 'rgw/0')
        self.config.assert_not_called()
        self.relation_set.assert_not_called()

    def test_secondary_relation_changed_mismatching_config(self):
        for k, v in self._complete_config.items():
            self.test_config.set(k, v)
        self.is_leader.return_value = True
        self.relation_get.return_value = self._test_bad_relation
        ceph_hooks.secondary_relation_changed('secondary:1', 'rgw/0')
        self.config.assert_has_calls([
            call('realm'),
            call('zonegroup'),
            call('zone'),
        ])
        self.multisite.list_realms.assert_not_called()
        self.relation_set.assert_not_called()

    def test_secondary_relation_changed_not_leader(self):
        self.is_leader.return_value = False
        ceph_hooks.secondary_relation_changed('secondary:1', 'rgw/0')
        self.relation_get.assert_not_called()
        self.relation_set.assert_not_called()

    @patch.object(ceph_hooks, 'apt_install')
    @patch.object(ceph_hooks, 'services')
    @patch.object(ceph_hooks, 'nrpe')
    def test_update_nrpe_config(self, nrpe, services, apt_install):
        # Setup Mocks
        nrpe.get_nagios_hostname.return_value = 'foo'
        nrpe.get_nagios_unit_name.return_value = 'bar'
        nrpe_setup = MagicMock()
        nrpe.NRPE.return_value = nrpe_setup
        services.return_value = ['baz', 'qux']

        # Call the routine
        ceph_hooks.update_nrpe_config()

        # Verify calls
        apt_install.assert_called()
        nrpe.get_nagios_hostname.assert_called()
        nrpe.get_nagios_unit_name.assert_called()
        nrpe.copy_nrpe_checks.assert_called()
        nrpe.remove_check.assert_not_called()
        nrpe.add_init_service_checks.assert_called_with(nrpe_setup,
                                                        ['baz', 'qux'], 'bar')
        nrpe.add_haproxy_checks.assert_called_with(nrpe_setup, 'bar')
        nrpe_setup.write.assert_called()

        # Verify that remove_check is called appropriately if we pass
        # checks_to_remove
        ceph_hooks.update_nrpe_config(checks_to_remove=['quux', 'quuux'])
        nrpe_setup.remove_check.assert_has_calls([call(shortname='quux'),
                                                  call(shortname='quuux')])
