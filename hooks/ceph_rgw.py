#
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

import os
import subprocess

import charmhelpers.contrib.openstack.context as ch_context

from charmhelpers.core.hookenv import (
    config,
    service_name,
)

from charmhelpers.core.host import (
    mkdir,
    symlink,
)
from charmhelpers.contrib.storage.linux.ceph import (
    CephBrokerRq,
)

CEPH_DIR = '/etc/ceph'
CEPH_RADOSGW_DIR = '/var/lib/ceph/radosgw'
_radosgw_keyring = "keyring.rados.gateway"
CEPH_POOL_APP_NAME = 'rgw'


def import_radosgw_key(key, name=None):
    if name:
        keyring_path = os.path.join(CEPH_RADOSGW_DIR,
                                    'ceph-{}'.format(name),
                                    'keyring')
        link_path = os.path.join(CEPH_DIR,
                                 'ceph.client.{}.keyring'.format(name))
        owner = group = 'ceph'
    else:
        keyring_path = os.path.join(CEPH_DIR, _radosgw_keyring)
        link_path = None
        owner = group = 'root'

    exists = os.path.exists(keyring_path)
    if not exists:
        mkdir(path=os.path.dirname(keyring_path),
              owner=owner, group=group, perms=0o750)

    cmd = ['ceph-authtool', keyring_path]
    if not exists:
        cmd.append('--create-keyring')
    cmd.extend([
        '--name=client.{}'.format(name or 'radosgw.gateway'),
        '--add-key={}'.format(key)
    ])
    subprocess.check_call(cmd)
    if not exists:
        cmd = [
            'chown',
            '{}:{}'.format(owner, group),
            keyring_path
        ]
        subprocess.check_call(cmd)
        # NOTE: add a link to the keyring in /var/lib/ceph
        # to /etc/ceph so we can use it for radosgw-admin
        # operations for multi-site configuration
        if link_path:
            symlink(keyring_path, link_path)

    return not exists


def normalize_pool_name(pool):
    return pool[1:] if pool.startswith('.') else pool


def get_create_rgw_pools_rq(prefix=None):
    """Pre-create RGW pools so that they have the correct settings.

    If a prefix is provided it will be prepended to each pool name.

    When RGW creates its own pools it will create them with non-optimal
    settings (LP: #1476749).

    NOTE: see http://docs.ceph.com/docs/master/radosgw/config-ref/#pools and
          http://docs.ceph.com/docs/master/radosgw/config/#create-pools for
          list of supported/required pools.
    """
    def _add_light_pool(rq, pool, pg_num, prefix=None):
        # Per the Ceph PG Calculator, all of the lightweight pools get 0.10%
        # of the data by default and only the .rgw.buckets.* get higher values
        weights = {
            '.rgw.buckets.index': 3.00,
            '.rgw.buckets.extra': 1.00
        }
        w = weights.get(pool, 0.10)
        if prefix:
            pool = "{prefix}{pool}".format(prefix=prefix, pool=pool)

        pool = normalize_pool_name(pool)
        if pg_num > 0:
            rq.add_op_create_pool(name=pool, replica_count=replicas,
                                  pg_num=pg_num, group='objects',
                                  app_name=CEPH_POOL_APP_NAME)
        else:
            rq.add_op_create_pool(name=pool, replica_count=replicas,
                                  weight=w, group='objects',
                                  app_name=CEPH_POOL_APP_NAME)

    rq = CephBrokerRq()
    replicas = config('ceph-osd-replication-count')

    prefix = prefix or 'default'
    # Buckets likely to contain the most data and therefore
    # requiring the most PGs
    heavy = [
        '.rgw.buckets.data'
    ]
    bucket_weight = config('rgw-buckets-pool-weight')
    bluestore_compression = ch_context.CephBlueStoreCompressionContext()

    if config('pool-type') == 'erasure-coded':
        # General EC plugin config
        plugin = config('ec-profile-plugin')
        technique = config('ec-profile-technique')
        device_class = config('ec-profile-device-class')
        bdm_k = config('ec-profile-k')
        bdm_m = config('ec-profile-m')
        # LRC plugin config
        bdm_l = config('ec-profile-locality')
        crush_locality = config('ec-profile-crush-locality')
        # SHEC plugin config
        bdm_c = config('ec-profile-durability-estimator')
        # CLAY plugin config
        bdm_d = config('ec-profile-helper-chunks')
        scalar_mds = config('ec-profile-scalar-mds')
        # Profile name
        service = service_name()
        profile_name = (
            config('ec-profile-name') or "{}-profile".format(service)
        )
        rq.add_op_create_erasure_profile(
            name=profile_name,
            k=bdm_k, m=bdm_m,
            lrc_locality=bdm_l,
            lrc_crush_locality=crush_locality,
            shec_durability_estimator=bdm_c,
            clay_helper_chunks=bdm_d,
            clay_scalar_mds=scalar_mds,
            device_class=device_class,
            erasure_type=plugin,
            erasure_technique=technique
        )

        for pool in heavy:
            pool = "{prefix}{pool}".format(prefix=prefix, pool=pool)
            # NOTE(fnordahl): once we deprecate Python 3.5 support we can do
            # the unpacking of the BlueStore compression arguments as part of
            # the function arguments. Until then we need to build the dict
            # prior to the function call.
            kwargs = {
                'name': normalize_pool_name(pool),
                'erasure_profile': profile_name,
                'weight': bucket_weight,
                'group': "objects",
                'app_name': CEPH_POOL_APP_NAME,
            }
            kwargs.update(bluestore_compression.get_kwargs())
            rq.add_op_create_erasure_pool(**kwargs)
    else:
        for pool in heavy:
            pool = "{prefix}{pool}".format(prefix=prefix, pool=pool)
            # NOTE(fnordahl): once we deprecate Python 3.5 support we can do
            # the unpacking of the BlueStore compression arguments as part of
            # the function arguments. Until then we need to build the dict
            # prior to the function call.
            kwargs = {
                'name': normalize_pool_name(pool),
                'replica_count': replicas,
                'weight': bucket_weight,
                'group': 'objects',
                'app_name': CEPH_POOL_APP_NAME,
            }
            kwargs.update(bluestore_compression.get_kwargs())
            rq.add_op_create_replicated_pool(**kwargs)

    # NOTE: we want these pools to have a smaller pg_num/pgp_num than the
    # others since they are not expected to contain as much data
    light = [
        '.rgw.control',
        '.rgw.data.root',
        '.rgw.gc',
        '.rgw.log',
        '.rgw.intent-log',
        '.rgw.meta',
        '.rgw.otp',
        '.rgw.usage',
        '.rgw.users.keys',
        '.rgw.users.email',
        '.rgw.users.swift',
        '.rgw.users.uid',
        '.rgw.buckets.extra',
        '.rgw.buckets.index',
    ]
    pg_num = config('rgw-lightweight-pool-pg-num')
    for pool in light:
        _add_light_pool(rq, pool, pg_num, prefix)

    # RadosGW creates this pool automatically from Quincy on.
    # _add_light_pool(rq, '.rgw.root', pg_num)

    if config('restrict-ceph-pools'):
        rq.add_op_request_access_to_group(name="objects",
                                          permission='rwx',
                                          key_name='radosgw.gateway')

    return rq
