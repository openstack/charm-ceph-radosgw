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

from unittest import mock
from unittest.mock import patch

from test_utils import CharmTestCase

with patch('utils.register_configs') as configs:
    configs.return_value = 'test-config'
    import actions


class PauseTestCase(CharmTestCase):

    def setUp(self):
        super(PauseTestCase, self).setUp(
            actions, ["pause_unit_helper"])

    def test_pauses_services(self):
        actions.pause([])
        self.pause_unit_helper.assert_called_once_with('test-config')


class ResumeTestCase(CharmTestCase):

    def setUp(self):
        super(ResumeTestCase, self).setUp(
            actions, ["resume_unit_helper"])

    def test_pauses_services(self):
        actions.resume([])
        self.resume_unit_helper.assert_called_once_with('test-config')


class MainTestCase(CharmTestCase):

    def setUp(self):
        super(MainTestCase, self).setUp(actions, ["action_fail"])

    def test_invokes_action(self):
        dummy_calls = []

        def dummy_action(args):
            dummy_calls.append(True)

        with mock.patch.dict(actions.ACTIONS, {"foo": dummy_action}):
            actions.main(["foo"])
        self.assertEqual(dummy_calls, [True])

    def test_unknown_action(self):
        """Unknown actions aren't a traceback."""
        exit_string = actions.main(["foo"])
        self.assertEqual("Action foo undefined", exit_string)

    def test_failing_action(self):
        """Actions which traceback trigger action_fail() calls."""
        dummy_calls = []

        self.action_fail.side_effect = dummy_calls.append

        def dummy_action(args):
            raise ValueError("uh oh")

        with mock.patch.dict(actions.ACTIONS, {"foo": dummy_action}):
            actions.main(["foo"])
        self.assertEqual(dummy_calls, ["uh oh"])


class MultisiteActionsTestCase(CharmTestCase):

    TO_PATCH = [
        'action_fail',
        'action_get',
        'action_set',
        'multisite',
        'config',
        'is_leader',
        'leader_set',
        'service_name',
        'service_restart',
        'log',
    ]

    def setUp(self):
        super(MultisiteActionsTestCase, self).setUp(actions,
                                                    self.TO_PATCH)
        self.config.side_effect = self.test_config.get

    def test_promote(self):
        self.is_leader.return_value = True
        self.test_config.set('zone', 'testzone')
        self.test_config.set('zonegroup', 'testzonegroup')
        actions.promote([])
        self.multisite.modify_zone.assert_called_once_with(
            'testzone',
            default=True,
            master=True,
        )
        self.multisite.update_period.assert_called_once_with(
            zonegroup='testzonegroup', zone='testzone'
        )

    def test_promote_unconfigured(self):
        self.is_leader.return_value = True
        self.test_config.set('zone', None)
        self.test_config.set('zonegroup', None)
        actions.promote([])
        self.action_fail.assert_called_once()

    def test_readonly(self):
        self.test_config.set('zone', 'testzone')
        actions.readonly([])
        self.multisite.modify_zone.assert_called_once_with(
            'testzone',
            readonly=True,
        )
        self.multisite.update_period.assert_called_once_with()

    def test_readonly_unconfigured(self):
        self.test_config.set('zone', None)
        actions.readonly([])
        self.action_fail.assert_called_once()

    def test_readwrite(self):
        self.test_config.set('zone', 'testzone')
        actions.readwrite([])
        self.multisite.modify_zone.assert_called_once_with(
            'testzone',
            readonly=False,
        )
        self.multisite.update_period.assert_called_once_with()

    def test_readwrite_unconfigured(self):
        self.test_config.set('zone', None)
        actions.readwrite([])
        self.action_fail.assert_called_once()

    def test_tidydefaults(self):
        self.test_config.set('zone', 'testzone')
        actions.tidydefaults([])
        self.multisite.tidy_defaults.assert_called_once_with()

    def test_tidydefaults_unconfigured(self):
        self.test_config.set('zone', None)
        actions.tidydefaults([])
        self.action_fail.assert_called_once()

    def test_enable_buckets_sync(self):
        self.multisite.is_multisite_configured.return_value = True
        self.multisite.get_zonegroup_info.return_value = {
            'master_zone': 'test-zone-id',
        }
        self.multisite.get_zone_info.return_value = {
            'id': 'test-zone-id',
        }
        self.is_leader.return_value = True
        self.action_get.return_value = 'testbucket1,testbucket2,non-existent'
        self.test_config.set('zone', 'testzone')
        self.test_config.set('zonegroup', 'testzonegroup')
        self.test_config.set('realm', 'testrealm')
        self.multisite.list_buckets.return_value = ['testbucket1',
                                                    'testbucket2']

        actions.enable_buckets_sync([])

        self.multisite.is_multisite_configured.assert_called_once()
        self.multisite.get_zonegroup_info.assert_called_once_with(
            'testzonegroup',
        )
        self.multisite.get_zone_info.assert_called_once_with(
            'testzone',
        )
        self.action_get.assert_called_once_with('buckets')
        self.multisite.list_buckets.assert_called_once_with(
            zonegroup='testzonegroup', zone='testzone',
        )
        self.assertEqual(self.multisite.create_sync_group.call_count, 2)
        self.multisite.create_sync_group.assert_has_calls([
            mock.call(bucket='testbucket1',
                      group_id='default',
                      status=self.multisite.SYNC_POLICY_ENABLED),
            mock.call(bucket='testbucket2',
                      group_id='default',
                      status=self.multisite.SYNC_POLICY_ENABLED),
        ])
        self.assertEqual(self.multisite.create_sync_group_pipe.call_count, 2)
        self.multisite.create_sync_group_pipe.assert_has_calls([
            mock.call(bucket='testbucket1',
                      group_id='default',
                      pipe_id='default',
                      source_zones=['*'],
                      dest_zones=['*']),
            mock.call(bucket='testbucket2',
                      group_id='default',
                      pipe_id='default',
                      source_zones=['*'],
                      dest_zones=['*']),
        ])
        expected_messages = [
            'Updated "testbucket1" bucket sync policy to "{}"'.format(
                self.multisite.SYNC_POLICY_ENABLED),
            'Updated "testbucket2" bucket sync policy to "{}"'.format(
                self.multisite.SYNC_POLICY_ENABLED),
            ('Bucket "non-existent" does not exist in the zonegroup '
             '"testzonegroup" and zone "testzone"'),
        ]
        self.assertEqual(self.log.call_count, 3)
        self.log.assert_has_calls([
            mock.call(expected_messages[0]),
            mock.call(expected_messages[1]),
            mock.call(expected_messages[2]),
        ])
        self.action_set.assert_called_once_with(
            values={
                'message': '\n'.join(expected_messages),
            })

    def test_disable_buckets_sync(self):
        self.multisite.is_multisite_configured.return_value = True
        self.multisite.get_zonegroup_info.return_value = {
            'master_zone': 'test-zone-id',
        }
        self.multisite.get_zone_info.return_value = {
            'id': 'test-zone-id',
        }
        self.is_leader.return_value = True
        self.action_get.return_value = 'testbucket1,non-existent'
        self.test_config.set('zone', 'testzone')
        self.test_config.set('zonegroup', 'testzonegroup')
        self.test_config.set('realm', 'testrealm')
        self.multisite.list_buckets.return_value = ['testbucket1']

        actions.disable_buckets_sync([])

        self.multisite.is_multisite_configured.assert_called_once()
        self.multisite.get_zonegroup_info.assert_called_once_with(
            'testzonegroup',
        )
        self.multisite.get_zone_info.assert_called_once_with(
            'testzone',
        )
        self.action_get.assert_called_once_with('buckets')
        self.multisite.list_buckets.assert_called_once_with(
            zonegroup='testzonegroup', zone='testzone',
        )
        self.multisite.create_sync_group.assert_called_once_with(
            bucket='testbucket1',
            group_id='default',
            status=self.multisite.SYNC_POLICY_FORBIDDEN,
        )
        self.multisite.create_sync_group_pipe.assert_called_once_with(
            bucket='testbucket1',
            group_id='default',
            pipe_id='default',
            source_zones=['*'],
            dest_zones=['*'],
        )
        expected_messages = [
            'Updated "testbucket1" bucket sync policy to "{}"'.format(
                self.multisite.SYNC_POLICY_FORBIDDEN),
            ('Bucket "non-existent" does not exist in the zonegroup '
             '"testzonegroup" and zone "testzone"'),
        ]
        self.assertEqual(self.log.call_count, 2)
        self.log.assert_has_calls([
            mock.call(expected_messages[0]),
            mock.call(expected_messages[1]),
        ])
        self.action_set.assert_called_once_with(
            values={
                'message': '\n'.join(expected_messages),
            })

    def test_reset_buckets_sync(self):
        self.multisite.is_multisite_configured.return_value = True
        self.multisite.get_zonegroup_info.return_value = {
            'master_zone': 'test-zone-id',
        }
        self.multisite.get_zone_info.return_value = {
            'id': 'test-zone-id',
        }
        self.is_leader.return_value = True
        self.action_get.return_value = 'testbucket1,non-existent'
        self.test_config.set('zone', 'testzone')
        self.test_config.set('zonegroup', 'testzonegroup')
        self.test_config.set('realm', 'testrealm')
        self.multisite.list_buckets.return_value = ['testbucket1']

        actions.reset_buckets_sync([])

        self.multisite.is_multisite_configured.assert_called_once()
        self.multisite.get_zonegroup_info.assert_called_once_with(
            'testzonegroup',
        )
        self.multisite.get_zone_info.assert_called_once_with(
            'testzone',
        )
        self.action_get.assert_called_once_with('buckets')
        self.multisite.list_buckets.assert_called_once_with(
            zonegroup='testzonegroup', zone='testzone',
        )
        self.multisite.remove_sync_group.assert_called_once_with(
            bucket='testbucket1',
            group_id='default',
        )
        expected_messages = [
            'Reset "testbucket1" bucket sync policy',
            ('Bucket "non-existent" does not exist in the zonegroup '
             '"testzonegroup" and zone "testzone"'),
        ]
        self.assertEqual(self.log.call_count, 2)
        self.log.assert_has_calls([
            mock.call(expected_messages[0]),
            mock.call(expected_messages[1]),
        ])
        self.action_set.assert_called_once_with(
            values={
                'message': '\n'.join(expected_messages),
            })
