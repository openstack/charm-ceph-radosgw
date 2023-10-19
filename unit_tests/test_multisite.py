# Copyright 2019 Canonical Ltd
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

import inspect
import os
import json
from unittest import mock

import multisite

from test_utils import CharmTestCase


def whoami():
    return inspect.stack()[1][3]


def get_zonegroup_stub():
    # populate dummy zone info
    zone = {}
    zone['id'] = "test_zone_id"
    zone['name'] = "test_zone"

    # populate dummy zonegroup info
    zonegroup = {}
    zonegroup['name'] = "test_zonegroup"
    zonegroup['master_zone'] = "test_zone_id"
    zonegroup['zones'] = [zone]
    return zonegroup


class TestMultisiteHelpers(CharmTestCase):

    TO_PATCH = [
        'subprocess',
        'socket',
        'hookenv',
        'utils',
    ]

    def setUp(self):
        super(TestMultisiteHelpers, self).setUp(multisite, self.TO_PATCH)
        self.socket.gethostname.return_value = 'testhost'
        self.utils.request_per_unit_key.return_value = True

    def _testdata(self, funcname):
        return os.path.join(os.path.dirname(__file__),
                            'testdata',
                            '{}.json'.format(funcname))

    def test___key_name(self):
        self.assertEqual(
            multisite._key_name(),
            'rgw.testhost')
        self.utils.request_per_unit_key.return_value = False
        self.assertEqual(
            multisite._key_name(),
            'radosgw.gateway')

    def test_create_realm(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.create_realm('beedata', default=True)
            self.assertEqual(result['name'], 'beedata')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'realm', 'create',
                '--rgw-realm=beedata', '--default'
            ])

    def test_list_realms(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.list_realms()
            self.assertTrue('beedata' in result)

    def test_set_default_zone(self):
        multisite.set_default_realm('newrealm')
        self.subprocess.check_call.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'realm', 'default',
            '--rgw-realm=newrealm'
        ])

    def test_create_user(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            access_key, secret = multisite.create_user(
                'mrbees',
            )
            self.assertEqual(
                access_key,
                '41JJQK1HN2NAE5DEZUF9')
            self.assertEqual(
                secret,
                '1qhCgxmUDAJI9saFAVdvUTG5MzMjlpMxr5agaaa4')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'user', 'create',
                '--uid=mrbees',
                '--display-name=Synchronization User',
            ])

    def test_create_system_user(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            access_key, secret = multisite.create_system_user(
                'mrbees',
            )
            self.assertEqual(
                access_key,
                '41JJQK1HN2NAE5DEZUF9')
            self.assertEqual(
                secret,
                '1qhCgxmUDAJI9saFAVdvUTG5MzMjlpMxr5agaaa4')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'user', 'create',
                '--uid=mrbees',
                '--display-name=Synchronization User',
                '--system'
            ])

    def test_create_zonegroup(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.create_zonegroup(
                'brundall',
                endpoints=['http://localhost:80'],
                master=True,
                default=True,
                realm='beedata',
            )
            self.assertEqual(result['name'], 'brundall')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'zonegroup', 'create',
                '--rgw-zonegroup=brundall',
                '--endpoints=http://localhost:80',
                '--rgw-realm=beedata',
                '--default',
                '--master'
            ])

    def test_list_zonegroups(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.list_zonegroups()
            self.assertTrue('brundall' in result)

    def test_create_zone(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.create_zone(
                'brundall-east',
                endpoints=['http://localhost:80'],
                master=True,
                default=True,
                zonegroup='brundall',
                access_key='mykey',
                secret='mypassword',
            )
            self.assertEqual(result['name'], 'brundall-east')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'zone', 'create',
                '--rgw-zone=brundall-east',
                '--endpoints=http://localhost:80',
                '--rgw-zonegroup=brundall',
                '--default', '--master',
                '--access-key=mykey',
                '--secret=mypassword',
                '--read-only=0',
            ])

    def test_modify_zone(self):
        multisite.modify_zone(
            'brundall-east',
            endpoints=['http://localhost:80', 'https://localhost:443'],
            access_key='mykey',
            secret='secret',
            readonly=True
        )
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'modify',
            '--rgw-zone=brundall-east',
            '--endpoints=http://localhost:80,https://localhost:443',
            '--access-key=mykey', '--secret=secret',
            '--read-only=1',
        ])

    def test_modify_zone_promote_master(self):
        multisite.modify_zone(
            'brundall-east',
            default=True,
            master=True,
        )
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'modify',
            '--rgw-zone=brundall-east',
            '--master',
            '--default',
            '--read-only=0',
        ])

    def test_modify_zone_partial_credentials(self):
        multisite.modify_zone(
            'brundall-east',
            endpoints=['http://localhost:80', 'https://localhost:443'],
            access_key='mykey',
        )
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'modify',
            '--rgw-zone=brundall-east',
            '--endpoints=http://localhost:80,https://localhost:443',
            '--read-only=0',
        ])

    def test_list_zones(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.list_zones()
            self.assertTrue('brundall-east' in result)

    def test_update_period(self):
        multisite.update_period()
        self.subprocess.check_call.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'period', 'update', '--commit'
        ])

    @mock.patch.object(multisite, 'list_zonegroups')
    @mock.patch.object(multisite, 'list_zones')
    @mock.patch.object(multisite, 'update_period')
    def test_tidy_defaults(self,
                           mock_update_period,
                           mock_list_zones,
                           mock_list_zonegroups):
        mock_list_zones.return_value = ['default']
        mock_list_zonegroups.return_value = ['default']
        multisite.tidy_defaults()
        self.subprocess.call.assert_has_calls([
            mock.call(['radosgw-admin', '--id=rgw.testhost',
                       'zonegroup', 'remove',
                       '--rgw-zonegroup=default', '--rgw-zone=default']),
            mock.call(['radosgw-admin', '--id=rgw.testhost',
                       'zone', 'delete',
                       '--rgw-zone=default']),
            mock.call(['radosgw-admin', '--id=rgw.testhost',
                       'zonegroup', 'delete',
                       '--rgw-zonegroup=default'])
        ])
        mock_update_period.assert_called_with()

    @mock.patch.object(multisite, 'list_zonegroups')
    @mock.patch.object(multisite, 'list_zones')
    @mock.patch.object(multisite, 'update_period')
    def test_tidy_defaults_noop(self,
                                mock_update_period,
                                mock_list_zones,
                                mock_list_zonegroups):
        mock_list_zones.return_value = ['brundall-east']
        mock_list_zonegroups.return_value = ['brundall']
        multisite.tidy_defaults()
        self.subprocess.call.assert_not_called()
        mock_update_period.assert_not_called()

    def test_pull_realm(self):
        multisite.pull_realm(url='http://master:80',
                             access_key='testkey',
                             secret='testsecret')
        self.subprocess.check_output.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'realm', 'pull',
            '--url=http://master:80',
            '--access-key=testkey', '--secret=testsecret',
        ])

    def test_pull_period(self):
        multisite.pull_period(url='http://master:80',
                              access_key='testkey',
                              secret='testsecret')
        self.subprocess.check_output.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'period', 'pull',
            '--url=http://master:80',
            '--access-key=testkey', '--secret=testsecret',
        ])

    def test_list_buckets(self):
        self.subprocess.CalledProcessError = BaseException
        multisite.list_buckets('default', 'default')
        self.subprocess.check_output.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'bucket', 'list', '--rgw-zone=default',
            '--rgw-zonegroup=default'
        ])

    def test_rename_zonegroup(self):
        multisite.rename_zonegroup('default', 'test_zone_group')
        self.subprocess.call.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zonegroup', 'rename', '--rgw-zonegroup=default',
            '--zonegroup-new-name=test_zone_group'
        ])

    def test_rename_zone(self):
        multisite.rename_zone('default', 'test_zone', 'test_zone_group')
        self.subprocess.call.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'rename', '--rgw-zone=default',
            '--zone-new-name=test_zone',
            '--rgw-zonegroup=test_zone_group'
        ])

    def test_get_zonegroup(self):
        multisite.get_zonegroup_info('test_zone')
        self.subprocess.check_output.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zonegroup', 'get', '--rgw-zonegroup=test_zone'
        ])

    def test_modify_zonegroup_migrate(self):
        multisite.modify_zonegroup('test_zonegroup',
                                   endpoints=['http://localhost:80'],
                                   default=True, master=True,
                                   realm='test_realm')
        self.subprocess.check_output.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zonegroup', 'modify',
            '--rgw-zonegroup=test_zonegroup', '--rgw-realm=test_realm',
            '--endpoints=http://localhost:80', '--default', '--master',
        ])

    def test_modify_zone_migrate(self):
        multisite.modify_zone('test_zone', default=True, master=True,
                              endpoints=['http://localhost:80'],
                              zonegroup='test_zonegroup', realm='test_realm')
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'modify',
            '--rgw-zone=test_zone', '--rgw-realm=test_realm',
            '--rgw-zonegroup=test_zonegroup',
            '--endpoints=http://localhost:80',
            '--master', '--default', '--read-only=0',
        ])

    @mock.patch.object(multisite, 'list_zones')
    @mock.patch.object(multisite, 'get_zonegroup_info')
    def test_get_local_zone(self, mock_get_zonegroup_info, mock_list_zones):
        mock_get_zonegroup_info.return_value = get_zonegroup_stub()
        mock_list_zones.return_value = ['test_zone']
        zone, _zonegroup = multisite.get_local_zone('test_zonegroup')
        self.assertEqual(
            zone,
            'test_zone'
        )

    def test_rename_multisite_config_zonegroup_fail(self):
        self.assertEqual(
            multisite.rename_multisite_config(
                ['default'], 'test_zonegroup',
                ['default'], 'test_zone'
            ),
            None
        )

        self.subprocess.call.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zonegroup', 'rename', '--rgw-zonegroup=default',
            '--zonegroup-new-name=test_zonegroup'
        ])

    def test_modify_multisite_config_zonegroup_fail(self):
        self.assertEqual(
            multisite.modify_multisite_config(
                'test_zone', 'test_zonegroup',
                endpoints=['http://localhost:80'],
                realm='test_realm'
            ),
            None
        )

        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zonegroup', 'modify', '--rgw-zonegroup=test_zonegroup',
            '--rgw-realm=test_realm',
            '--endpoints=http://localhost:80', '--default',
            '--master',
        ])

    @mock.patch.object(multisite, 'modify_zonegroup')
    def test_modify_multisite_config_zone_fail(self, mock_modify_zonegroup):
        mock_modify_zonegroup.return_value = True
        self.assertEqual(
            multisite.modify_multisite_config(
                'test_zone', 'test_zonegroup',
                endpoints=['http://localhost:80'],
                realm='test_realm'
            ),
            None
        )

        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'modify',
            '--rgw-zone=test_zone',
            '--rgw-realm=test_realm',
            '--rgw-zonegroup=test_zonegroup',
            '--endpoints=http://localhost:80',
            '--master', '--default', '--read-only=0',
        ])

    @mock.patch.object(multisite, 'rename_zonegroup')
    def test_rename_multisite_config_zone_fail(self, mock_rename_zonegroup):
        mock_rename_zonegroup.return_value = True
        self.assertEqual(
            multisite.rename_multisite_config(
                ['default'], 'test_zonegroup',
                ['default'], 'test_zone'
            ),
            None
        )

        self.subprocess.call.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'rename', '--rgw-zone=default',
            '--zone-new-name=test_zone',
            '--rgw-zonegroup=test_zonegroup',
        ])

    @mock.patch.object(json, 'loads')
    def test_remove_zone_from_zonegroup(self, json_loads):
        # json.loads() raises TypeError for mock objects.
        json_loads.returnvalue = []
        multisite.remove_zone_from_zonegroup(
            'test_zone', 'test_zonegroup',
        )

        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zonegroup', 'remove', '--rgw-zonegroup=test_zonegroup',
            '--rgw-zone=test_zone',
        ])

    @mock.patch.object(json, 'loads')
    def test_add_zone_from_zonegroup(self, json_loads):
        # json.loads() raises TypeError for mock objects.
        json_loads.returnvalue = []
        multisite.add_zone_to_zonegroup(
            'test_zone', 'test_zonegroup',
        )

        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zonegroup', 'add', '--rgw-zonegroup=test_zonegroup',
            '--rgw-zone=test_zone',
        ])

    @mock.patch.object(multisite, 'list_zonegroups')
    @mock.patch.object(multisite, 'get_local_zone')
    @mock.patch.object(multisite, 'list_buckets')
    def test_check_zone_has_buckets(self, mock_list_zonegroups,
                                    mock_get_local_zone,
                                    mock_list_buckets):
        mock_list_zonegroups.return_value = ['test_zonegroup']
        mock_get_local_zone.return_value = 'test_zone', 'test_zonegroup'
        mock_list_buckets.return_value = ['test_bucket_1', 'test_bucket_2']
        self.assertEqual(
            multisite.check_cluster_has_buckets(),
            True
        )

    def test_get_zone_info(self):
        multisite.get_zone_info('test_zone', 'test_zonegroup')
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'get',
            '--rgw-zone=test_zone', '--rgw-zonegroup=test_zonegroup',
        ])

    def test_sync_group_exists(self):
        groups = [
            {'key': 'group1'},
            {'key': 'group2'},
        ]
        self.subprocess.check_output.return_value = json.dumps(groups).encode()
        self.assertTrue(multisite.sync_group_exists('group1'))
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'sync', 'group', 'get',
        ])

    def test_bucket_sync_group_exists(self):
        with open(self._testdata('test_list_sync_groups'), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            self.assertTrue(multisite.sync_group_exists('default',
                                                        bucket='test'))
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'sync', 'group', 'get',
                '--bucket=test',
            ])

    def test_sync_group_does_not_exists(self):
        with open(self._testdata('test_list_sync_groups'), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            self.assertFalse(multisite.sync_group_exists('group-non-existent'))
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'sync', 'group', 'get',
            ])

    def test_get_sync_group(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.get_sync_group('default')
            self.assertEqual(result['id'], 'default')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'sync', 'group', 'get',
                '--group-id=default',
            ])

    def test_create_sync_group(self):
        test_group_json = json.dumps({"id": "default"}).encode()
        self.subprocess.check_output.return_value = test_group_json
        result = multisite.create_sync_group(
            group_id='default',
            status=multisite.SYNC_POLICY_ENABLED,
        )
        self.assertEqual(result['id'], 'default')
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'sync', 'group', 'create',
            '--group-id=default',
            '--status={}'.format(multisite.SYNC_POLICY_ENABLED),
        ])

    def test_create_sync_group_wrong_status(self):
        self.assertRaises(
            multisite.UnknownSyncPolicyState,
            multisite.create_sync_group, "default", "wrong_status",
        )

    def test_remove_sync_group(self):
        multisite.remove_sync_group('default')
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'sync', 'group', 'remove',
            '--group-id=default',
        ])

    @mock.patch.object(multisite, 'get_sync_group')
    @mock.patch.object(multisite, 'sync_group_exists')
    def test_is_sync_group_update_needed(self, mock_sync_group_exists,
                                         mock_get_sync_group):
        mock_sync_group_exists.return_value = True
        with open(self._testdata('test_get_sync_group'), 'r') as f:
            mock_get_sync_group.return_value = json.loads(f.read())

        result = multisite.is_sync_group_update_needed(
            group_id='default',
            flow_id='zone_a-zone_b',
            pipe_id='zone_a-zone_b',
            source_zone='zone_a',
            dest_zone='zone_b',
            desired_status=multisite.SYNC_POLICY_ALLOWED,
            desired_flow_type=multisite.SYNC_FLOW_SYMMETRICAL,
        )

        mock_sync_group_exists.assert_called_with('default')
        mock_get_sync_group.assert_called_with('default')
        self.assertFalse(result)

    def test_is_sync_group_flow_update_needed(self):
        with open(self._testdata('test_get_sync_group'), 'r') as f:
            sync_group = json.loads(f.read())
            result = multisite.is_sync_group_flow_update_needed(
                sync_group,
                flow_id='zone_a-zone_b',
                source_zone='zone_a', dest_zone='zone_b',
                desired_flow_type=multisite.SYNC_FLOW_SYMMETRICAL,
            )
            self.assertFalse(result)

    @mock.patch.object(multisite, 'remove_sync_group_flow')
    def test_is_sync_group_flow_update_needed_flow_type_change(
            self, mock_remove_sync_group_flow):
        with open(self._testdata('test_get_sync_group'), 'r') as f:
            sync_group = json.loads(f.read())
            result = multisite.is_sync_group_flow_update_needed(
                sync_group,
                flow_id='zone_a-zone_b',
                source_zone='zone_a', dest_zone='zone_b',
                desired_flow_type=multisite.SYNC_FLOW_DIRECTIONAL,
            )
            mock_remove_sync_group_flow.assert_called_with(
                group_id='default',
                flow_id='zone_a-zone_b',
                flow_type=multisite.SYNC_FLOW_SYMMETRICAL,
                source_zone='zone_a', dest_zone='zone_b',
            )
            self.assertTrue(result)

    def test_create_sync_group_flow_symmetrical(self):
        with open(self._testdata('test_create_sync_group_flow'), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.create_sync_group_flow(
                group_id='default',
                flow_id='flow_id',
                flow_type=multisite.SYNC_FLOW_SYMMETRICAL,
                source_zone='zone_a',
                dest_zone='zone_b',
            )
            self.assertEqual(result['groups'][0]['id'], 'default')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'sync', 'group', 'flow', 'create',
                '--group-id=default',
                '--flow-id=flow_id',
                '--flow-type=symmetrical',
                '--zones=zone_a,zone_b',
            ])

    def test_create_sync_group_flow_directional(self):
        with open(self._testdata('test_create_sync_group_flow'), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.create_sync_group_flow(
                group_id='default',
                flow_id='flow_id',
                flow_type=multisite.SYNC_FLOW_DIRECTIONAL,
                source_zone='zone_a',
                dest_zone='zone_b',
            )
            self.assertEqual(result['groups'][0]['id'], 'default')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'sync', 'group', 'flow', 'create',
                '--group-id=default',
                '--flow-id=flow_id',
                '--flow-type=directional',
                '--source-zone=zone_a', '--dest-zone=zone_b',
            ])

    def test_create_sync_group_flow_wrong_type(self):
        self.assertRaises(
            multisite.UnknownSyncFlowType,
            multisite.create_sync_group_flow,
            group_id='default', flow_id='flow_id', flow_type='wrong_type',
            source_zone='zone_a', dest_zone='zone_b',
        )

    def test_remove_sync_group_flow_symmetrical(self):
        multisite.remove_sync_group_flow(
            group_id='default',
            flow_id='flow_id',
            flow_type=multisite.SYNC_FLOW_SYMMETRICAL,
        )
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'sync', 'group', 'flow', 'remove',
            '--group-id=default',
            '--flow-id=flow_id',
            '--flow-type=symmetrical',
        ])

    def test_remove_sync_group_flow_directional(self):
        multisite.remove_sync_group_flow(
            group_id='default',
            flow_id='flow_id',
            flow_type=multisite.SYNC_FLOW_DIRECTIONAL,
            source_zone='zone_a',
            dest_zone='zone_b',
        )
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'sync', 'group', 'flow', 'remove',
            '--group-id=default',
            '--flow-id=flow_id',
            '--flow-type=directional',
            '--source-zone=zone_a', '--dest-zone=zone_b',
        ])

    def test_create_sync_group_pipe(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.create_sync_group_pipe(
                group_id='default',
                pipe_id='pipe_id',
                source_zones=['zone_a', 'zone_b'],
                dest_zones=['zone_c', 'zone_d'],
            )
            self.assertEqual(result['groups'][0]['id'], 'default')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'sync', 'group', 'pipe', 'create',
                '--group-id=default',
                '--pipe-id=pipe_id',
                '--source-zones=zone_a,zone_b', '--source-bucket=*',
                '--dest-zones=zone_c,zone_d', '--dest-bucket=*',
            ])
