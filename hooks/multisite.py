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

import json
import functools
import subprocess
import socket
import utils

import charmhelpers.core.hookenv as hookenv
import charmhelpers.core.decorators as decorators

RGW_ADMIN = 'radosgw-admin'

SYNC_POLICY_ENABLED = 'enabled'
SYNC_POLICY_ALLOWED = 'allowed'
SYNC_POLICY_FORBIDDEN = 'forbidden'
SYNC_POLICY_STATES = [
    SYNC_POLICY_ENABLED,
    SYNC_POLICY_ALLOWED,
    SYNC_POLICY_FORBIDDEN
]
SYNC_FLOW_DIRECTIONAL = 'directional'
SYNC_FLOW_SYMMETRICAL = 'symmetrical'
SYNC_FLOW_TYPES = [
    SYNC_FLOW_DIRECTIONAL,
    SYNC_FLOW_SYMMETRICAL,
]


class UnknownSyncPolicyState(Exception):
    """Raised when an unknown sync policy state is encountered"""
    pass


class UnknownSyncFlowType(Exception):
    """Raised when an unknown sync flow type is encountered"""
    pass


@decorators.retry_on_exception(num_retries=10, base_delay=5,
                               exc_type=subprocess.CalledProcessError)
def _check_output(cmd):
    """Logging wrapper for subprocess.check_ouput"""
    hookenv.log("Executing: {}".format(' '.join(cmd)), level=hookenv.DEBUG)
    return subprocess.check_output(cmd, stderr=subprocess.PIPE).decode('UTF-8')


@decorators.retry_on_exception(num_retries=5, base_delay=3,
                               exc_type=subprocess.CalledProcessError)
def _check_call(cmd):
    """Logging wrapper for subprocess.check_call"""
    hookenv.log("Executing: {}".format(' '.join(cmd)), level=hookenv.DEBUG)
    return subprocess.check_call(cmd)


def _call(cmd):
    """Logging wrapper for subprocess.call"""
    hookenv.log("Executing: {}".format(' '.join(cmd)), level=hookenv.DEBUG)
    return subprocess.call(cmd)


def _key_name():
    """Determine the name of the cephx key for the local unit"""
    if utils.request_per_unit_key():
        return 'rgw.{}'.format(socket.gethostname())
    else:
        return 'radosgw.gateway'


def _list(key):
    """
    Internal implementation for list_* functions

    :param key: string for required entity (zone, zonegroup, realm, user)
    :type key: str
    :return: List of specified entities found
    :rtype: list
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        key, 'list'
    ]
    try:
        result = json.loads(_check_output(cmd))
        hookenv.log("Results: {}".format(
            result),
            level=hookenv.DEBUG)
        if isinstance(result, dict):
            return result['{}s'.format(key)]
        else:
            return result
    except TypeError:
        return []


def plain_list(key):
    """Simple Implementation for list_*, where execution may fail expectedly.

    On failure, retries are not attempted and empty list is returned.

    :param key: string for required resource (zone, zonegroup, realm, user)
    :type key: str
    :return: list of specified entities found
    :rtype: list
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        key, 'list'
    ]
    try:
        result = json.loads(subprocess.check_output(
            cmd, stderr=subprocess.PIPE
        ).decode('UTF-8'))
        hookenv.log("Results: {}".format(result), level=hookenv.DEBUG)
        if isinstance(result, dict):
            return result['{}s'.format(key)]
        else:
            return result
    except subprocess.CalledProcessError:
        return []
    except TypeError:
        return []


@decorators.retry_on_exception(num_retries=5, base_delay=3,
                               exc_type=ValueError)
def list_zones(retry_on_empty=False):
    """
    List zones

    :param retry_on_empty: Whether to retry if no zones are returned.
    :type retry_on_empty: bool
    :return: List of specified entities found
    :rtype: list
    :raises: ValueError
    """
    _zones = _list('zone')
    if retry_on_empty and not _zones:
        hookenv.log("No zones found", level=hookenv.DEBUG)
        raise ValueError("No zones found")
    return _zones


list_realms = functools.partial(_list, 'realm')
list_zonegroups = functools.partial(_list, 'zonegroup')
list_users = functools.partial(_list, 'user')


def list_buckets(zone, zonegroup):
    """List Buckets served under the provided zone and zonegroup pair.

    :param zonegroup: Parent zonegroup.
    :type zonegroup: str
    :param zone: Parent zone.
    :type zone: str
    :returns: List of buckets found
    :rtype: list
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'bucket', 'list',
        '--rgw-zone={}'.format(zone),
        '--rgw-zonegroup={}'.format(zonegroup),
    ]
    try:
        return json.loads(_check_output(cmd))
    except subprocess.CalledProcessError:
        hookenv.log("Bucket queried for incorrect zone({})-zonegroup({}) "
                    "pair".format(zone, zonegroup), level=hookenv.ERROR)
        return None
    except TypeError:
        return None


def create_realm(name, default=False):
    """
    Create a new RADOS Gateway Realm.

    :param name: name of realm to create
    :type name: str
    :param default: set new realm as the default realm
    :type default: boolean
    :return: realm configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'realm', 'create',
        '--rgw-realm={}'.format(name)
    ]
    if default:
        cmd += ['--default']
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def set_default_realm(name):
    """
    Set the default RADOS Gateway Realm

    :param name: name of realm to create
    :type name: str
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'realm', 'default',
        '--rgw-realm={}'.format(name)
    ]
    _check_call(cmd)


def create_zonegroup(name, endpoints, default=False, master=False, realm=None):
    """
    Create a new RADOS Gateway zone Group

    :param name: name of zonegroup to create
    :type name: str
    :param endpoints: list of URLs to endpoints for zonegroup
    :type endpoints: list[str]
    :param default: set new zonegroup as the default zonegroup
    :type default: boolean
    :param master: set new zonegroup as the master zonegroup
    :type master: boolean
    :param realm: realm to use for zonegroup
    :type realm: str
    :return: zonegroup configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zonegroup', 'create',
        '--rgw-zonegroup={}'.format(name),
        '--endpoints={}'.format(','.join(endpoints)),
    ]
    if realm:
        cmd.append('--rgw-realm={}'.format(realm))
    if default:
        cmd.append('--default')
    if master:
        cmd.append('--master')
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def modify_zonegroup(name, endpoints=None, default=False,
                     master=False, realm=None):
    """Modify an existing RADOS Gateway zonegroup

    An empty list of endpoints would cause NO-CHANGE in the configured
    endpoints for the zonegroup.

    :param name: name of zonegroup to modify
    :type name: str
    :param endpoints: list of URLs to endpoints for zonegroup
    :type endpoints: list[str]
    :param default: set zonegroup as the default zonegroup
    :type default: boolean
    :param master: set zonegroup as the master zonegroup
    :type master: boolean
    :param realm: realm name for provided zonegroup
    :type realm: str
    :return: zonegroup configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zonegroup', 'modify',
        '--rgw-zonegroup={}'.format(name),
    ]
    if realm:
        cmd.append('--rgw-realm={}'.format(realm))
    if endpoints:
        cmd.append('--endpoints={}'.format(','.join(endpoints)))
    if default:
        cmd.append('--default')
    if master:
        cmd.append('--master')
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def create_zone(name, endpoints, default=False, master=False, zonegroup=None,
                access_key=None, secret=None, readonly=False):
    """
    Create a new RADOS Gateway zone

    :param name: name of zone to create
    :type name: str
    :param endpoints: list of URLs to endpoints for zone
    :type endpoints: list[str]
    :param default: set new zone as the default zone
    :type default: boolean
    :param master: set new zone as the master zone
    :type master: boolean
    :param zonegroup: zonegroup to use for zone
    :type zonegroup: str
    :param access_key: access-key to use for the zone
    :type access_key: str
    :param secret: secret to use with access-key for the zone
    :type secret: str
    :param readonly: set zone as read only
    :type: readonly: boolean
    :return: dict of zone configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zone', 'create',
        '--rgw-zone={}'.format(name),
        '--endpoints={}'.format(','.join(endpoints)),
    ]
    if zonegroup:
        cmd.append('--rgw-zonegroup={}'.format(zonegroup))
    if default:
        cmd.append('--default')
    if master:
        cmd.append('--master')
    if access_key and secret:
        cmd.append('--access-key={}'.format(access_key))
        cmd.append('--secret={}'.format(secret))
    cmd.append('--read-only={}'.format(1 if readonly else 0))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def modify_zone(name, endpoints=None, default=False, master=False,
                access_key=None, secret=None, readonly=False,
                realm=None, zonegroup=None):
    """Modify an existing RADOS Gateway zone

    :param name: name of zone to create
    :type name: str
    :param endpoints: list of URLs to endpoints for zone
    :type endpoints: list[str]
    :param default: set zone as the default zone
    :type default: boolean
    :param master: set zone as the master zone
    :type master: boolean
    :param access_key: access-key to use for the zone
    :type access_key: str
    :param secret: secret to use with access-key for the zone
    :type secret: str
    :param readonly: set zone as read only
    :type readonly: boolean
    :param realm: realm to use for zone
    :type realm: str
    :param zonegroup: zonegroup to use for zone
    :type zonegroup: str
    :return: zone configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zone', 'modify',
        '--rgw-zone={}'.format(name),
    ]
    if realm:
        cmd.append('--rgw-realm={}'.format(realm))
    if zonegroup:
        cmd.append('--rgw-zonegroup={}'.format(zonegroup))
    if endpoints:
        cmd.append('--endpoints={}'.format(','.join(endpoints)))
    if access_key and secret:
        cmd.append('--access-key={}'.format(access_key))
        cmd.append('--secret={}'.format(secret))
    if master:
        cmd.append('--master')
    if default:
        cmd.append('--default')
    cmd.append('--read-only={}'.format(1 if readonly else 0))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def get_zone_info(name, zonegroup=None):
    """Fetch detailed info for the provided zone

    :param name: zone name
    :type name: str
    :param zonegroup: parent zonegroup name
    :type zonegroup: str
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zone', 'get',
        '--rgw-zone={}'.format(name),
    ]
    if zonegroup:
        cmd.append('--rgw-zonegroup={}'.format(zonegroup))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def remove_zone_from_zonegroup(zone, zonegroup):
    """Remove RADOS Gateway zone from provided parent zonegroup

    Removal is different from deletion, this operation removes zone/zonegroup
    affiliation but does not delete the actual zone.

    :param zonegroup: parent zonegroup name
    :type zonegroup: str
    :param zone: zone name
    :type zone: str
    :return: modified zonegroup config
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zonegroup', 'remove',
        '--rgw-zonegroup={}'.format(zonegroup),
        '--rgw-zone={}'.format(zone),
    ]
    try:
        result = _check_output(cmd)
        return json.loads(result)
    except (TypeError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "Error removing zone {} from zonegroup {}. Result: {}"
            .format(zone, zonegroup, result)) from exc


def add_zone_to_zonegroup(zone, zonegroup):
    """Add RADOS Gateway zone to provided zonegroup

    :param zonegroup: parent zonegroup name
    :type zonegroup: str
    :param zone: zone name
    :type zone: str
    :return: modified zonegroup config
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zonegroup', 'add',
        '--rgw-zonegroup={}'.format(zonegroup),
        '--rgw-zone={}'.format(zone),
    ]
    try:
        result = _check_output(cmd)
        return json.loads(result)
    except (TypeError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "Error adding zone {} from zonegroup {}. Result: {}"
            .format(zone, zonegroup, result)) from exc


def update_period(fatal=True, zonegroup=None, zone=None, realm=None):
    """Update RADOS Gateway configuration period

    :param fatal: In failure case, whether CalledProcessError is to be raised.
    :type fatal: boolean
    :param zonegroup: zonegroup name
    :type zonegroup: str
    :param zone: zone name
    :type zone: str
    :param realm: realm name
    :type realm: str
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'period', 'update', '--commit'
    ]
    if zonegroup is not None:
        cmd.append('--rgw-zonegroup={}'.format(zonegroup))
    if zone is not None:
        cmd.append('--rgw-zone={}'.format(zone))
    if realm is not None:
        cmd.append('--rgw-realm={}'.format(realm))
    if fatal:
        _check_call(cmd)
    else:
        _call(cmd)


def tidy_defaults():
    """
    Purge any default zonegroup and zone definitions
    """
    if ('default' in list_zonegroups() and
            'default' in list_zones()):
        cmd = [
            RGW_ADMIN, '--id={}'.format(_key_name()),
            'zonegroup', 'remove',
            '--rgw-zonegroup=default',
            '--rgw-zone=default'
        ]
        _call(cmd)
        update_period()

    if 'default' in list_zones():
        cmd = [
            RGW_ADMIN, '--id={}'.format(_key_name()),
            'zone', 'delete',
            '--rgw-zone=default'
        ]
        _call(cmd)
        update_period()

    if 'default' in list_zonegroups():
        cmd = [
            RGW_ADMIN, '--id={}'.format(_key_name()),
            'zonegroup', 'delete',
            '--rgw-zonegroup=default'
        ]
        _call(cmd)
        update_period()


def get_user_creds(username):
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'user', 'info',
        '--uid={}'.format(username)
    ]
    result = json.loads(_check_output(cmd))
    return (result['keys'][0]['access_key'],
            result['keys'][0]['secret_key'])


def suspend_user(username):
    """
    Suspend a RADOS Gateway user

    :param username: username of user to create
    :type username: str
    """
    if username not in list_users():
        hookenv.log(
            "Cannot suspended user {}. User not found.".format(username),
            level=hookenv.DEBUG)
        return
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'user', 'suspend',
        '--uid={}'.format(username)
    ]
    _check_output(cmd)
    hookenv.log(
        "Suspended user {}".format(username),
        level=hookenv.DEBUG)


def create_user(username, system_user=False):
    """
    Create a RADOS Gateway user

    :param username: username of user to create
    :type username: str
    :param system_user: Whether to grant system user role
    :type system_user: bool
    :return: access key and secret
    :rtype: (str, str)
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'user', 'create',
        '--uid={}'.format(username),
        '--display-name=Synchronization User'
    ]
    if system_user:
        cmd.append('--system')
    try:
        result = json.loads(_check_output(cmd))
        return (result['keys'][0]['access_key'],
                result['keys'][0]['secret_key'])
    except TypeError:
        return (None, None)


def create_system_user(username):
    """
    Create a RADOS Gateway system user

    :param username: username of user to create
    :type username: str
    :return: access key and secret
    :rtype: (str, str)
    """
    return create_user(username, system_user=True)


def pull_realm(url, access_key, secret):
    """
    Pull in a RADOS Gateway Realm from a master RGW instance

    :param url: url of remote rgw deployment
    :type url: str
    :param access_key: access-key for remote rgw deployment
    :type access_key: str
    :param secret: secret for remote rgw deployment
    :type secret: str
    :return: realm configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'realm', 'pull',
        '--url={}'.format(url),
        '--access-key={}'.format(access_key),
        '--secret={}'.format(secret),
    ]
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def pull_period(url, access_key, secret):
    """
    Pull in a RADOS Gateway period from a master RGW instance

    :param url: url of remote rgw deployment
    :type url: str
    :param access_key: access-key for remote rgw deployment
    :type access_key: str
    :param secret: secret for remote rgw deployment
    :type secret: str
    :return: realm configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'period', 'pull',
        '--url={}'.format(url),
        '--access-key={}'.format(access_key),
        '--secret={}'.format(secret),
    ]
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def rename_zone(name, new_name, zonegroup):
    """Rename an existing RADOS Gateway zone

    If the command execution succeeds, 0 is returned, otherwise
    None is returned to the caller.

    :param name: current name for the zone being renamed
    :type name: str
    :param new_name: new name for the zone being renamed
    :type new_name: str
    :rtype: int
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zone', 'rename',
        '--rgw-zone={}'.format(name),
        '--zone-new-name={}'.format(new_name),
        '--rgw-zonegroup={}'.format(zonegroup)
    ]
    result = _call(cmd)
    return 0 if result == 0 else None


def rename_zonegroup(name, new_name):
    """Rename an existing RADOS Gateway zonegroup

    If the command execution succeeds, 0 is returned, otherwise
    None is returned to the caller.

    :param name: current name for the zonegroup being renamed
    :type name: str
    :param new_name: new name for the zonegroup being renamed
    :type new_name: str
    :rtype: int
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zonegroup', 'rename',
        '--rgw-zonegroup={}'.format(name),
        '--zonegroup-new-name={}'.format(new_name),
    ]
    result = _call(cmd)
    return 0 if result == 0 else None


def get_zonegroup_info(zonegroup):
    """Fetch detailed info for the provided zonegroup

    :param zonegroup: zonegroup Name for detailed query
    :type zonegroup: str
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zonegroup', 'get',
        '--rgw-zonegroup={}'.format(zonegroup),
    ]
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def get_sync_status():
    """
    Get sync status
    :returns: Sync Status Report from radosgw-admin
    :rtype: str
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'sync', 'status',
    ]
    try:
        return _check_output(cmd)
    except subprocess.CalledProcessError:
        hookenv.log("Failed to fetch sync status", level=hookenv.ERROR)
        return None


def is_multisite_configured(zone, zonegroup):
    """Check if system is already multisite configured

    Checks if zone and zonegroup are configured appropriately and
    remote data sync source is detected in sync status

    :rtype: Boolean
    """
    local_zones = list_zones()
    if zone not in local_zones:
        hookenv.log("zone {} not found in local zones {}"
                    .format(zone, local_zones), level=hookenv.ERROR)
        return False

    local_zonegroups = list_zonegroups()
    if zonegroup not in local_zonegroups:
        hookenv.log("zonegroup {} not found in local zonegroups {}"
                    .format(zonegroup, local_zonegroups), level=hookenv.ERROR)
        return False

    sync_status = get_sync_status()
    hookenv.log("Multisite sync status {}".format(sync_status),
                level=hookenv.DEBUG)
    if sync_status is not None:
        return ('data sync source:' in sync_status)

    return False


def get_local_zone(zonegroup):
    """Get local zone to provided parent zonegroup.

    In multisite systems, zonegroup contains both local and remote zone info
    this method is used to fetch the zone local to querying site.

    :param zonegroup: parent zonegroup name.
    :type zonegroup: str
    :returns: tuple with parent zonegroup and local zone name
    :rtype: tuple
    """
    local_zones = list_zones()
    zonegroup_info = get_zonegroup_info(zonegroup)

    if zonegroup_info is None:
        hookenv.log("Failed to fetch zonegroup ({}) info".format(zonegroup),
                    level=hookenv.ERROR)
        return None, None

    # zonegroup info always contains self name and zones list so fetching
    # directly is safe.
    master_zonegroup = zonegroup_info['name']
    for zone_info in zonegroup_info['zones']:
        zone = zone_info['name']
        if zone in local_zones:
            return zone, master_zonegroup

    hookenv.log(
        "No local zone configured for zonegroup ({})".format(zonegroup),
        level=hookenv.ERROR
    )
    return None, None


def rename_multisite_config(zonegroups, new_zonegroup_name,
                            zones, new_zone_name):
    """Rename zone and zonegroup to provided new names.

    If zone list (zones) or zonegroup list (zonegroups) contain 1 element
    rename the only element present in the list to provided (new_) value.

    :param zonegroups: List of zonegroups available at site.
    :type zonegroups: list[str]
    :param new_zonegroup_name: Desired new name for master zonegroup.
    :type new_zonegroup_name: str
    :param zones: List of zones available at site.
    :type zones: list[str]
    :param new_zonegroup_name: Desired new name for master zone.
    :type new_zonegroup_name: str

    :return: Whether any of the zone or zonegroup is renamed.
    :rtype: Boolean
    """
    mutation = False
    if (len(zonegroups) == 1) and (len(zones) == 1):
        if new_zonegroup_name not in zonegroups:
            result = rename_zonegroup(zonegroups[0], new_zonegroup_name)
            if result is None:
                hookenv.log(
                    "Failed renaming zonegroup from {} to {}"
                    .format(zonegroups[0], new_zonegroup_name),
                    level=hookenv.ERROR
                )
                return None
            mutation = True

        if new_zone_name not in zones:
            result = rename_zone(zones[0], new_zone_name, new_zonegroup_name)
            if result is None:
                hookenv.log(
                    "Failed renaming zone from {} to {}"
                    .format(zones[0], new_zone_name), level=hookenv.ERROR
                )
                return None
            mutation = True

    if mutation:
        hookenv.log("Renamed zonegroup {} to {}, and zone {} to {}".format(
                    zonegroups[0], new_zonegroup_name,
                    zones[0], new_zone_name))
        return True

    return False


def modify_multisite_config(zone, zonegroup, endpoints=None, realm=None):
    """Configure zone and zonegroup as master for multisite system.

    :param zonegroup: zonegroup name being configured for multisite
    :type zonegroup: str
    :param zone: zone name being configured for multisite
    :type zone: str
    :param endpoints: list of URLs to RGW endpoints
    :type endpoints: list[str]
    :param realm: realm to use for multisite
    :type realm: str
    :rtype: Boolean
    """
    if modify_zonegroup(zonegroup, endpoints=endpoints, default=True,
                        master=True, realm=realm) is None:
        hookenv.log(
            "Failed configuring zonegroup {}".format(zonegroup),
            level=hookenv.ERROR
        )
        return None

    if modify_zone(zone, endpoints=endpoints, default=True,
                   master=True, zonegroup=zonegroup, realm=realm) is None:
        hookenv.log(
            "Failed configuring zone {}".format(zone), level=hookenv.ERROR
        )
        return None

    update_period(zonegroup=zonegroup, zone=zone)
    hookenv.log("Configured zonegroup {}, and zone {} for multisite".format(
                zonegroup, zone))
    return True


def check_zone_has_buckets(zone, zonegroup):
    """Checks whether provided zone-zonegroup pair contains any bucket.

    :param zone: zone name to query buckets in.
    :type zone: str
    :param zonegroup: Parent zonegroup of zone.
    :type zonegroup: str
    :rtype: Boolean
    """
    buckets = list_buckets(zone, zonegroup)
    if buckets is not None:
        return (len(buckets) > 0)
    hookenv.log(
        "Failed to query buckets for zone {} zonegroup {}"
        .format(zone, zonegroup),
        level=hookenv.WARNING
    )
    return False


def check_zonegroup_has_buckets(zonegroup):
    """Checks whether any bucket exists in the master zone of a zonegroup.

    :param zone: zonegroup name to query buckets.
    :type zone: str
    :rtype: Boolean
    """
    # NOTE(utkarshbhatthere): sometimes querying against a particular
    # zonegroup results in info of an entirely different zonegroup, thus to
    # prevent a query against an incorrect pair in such cases, both zone and
    # zonegroup names are taken from zonegroup info.
    master_zone, master_zonegroup = get_local_zone(zonegroup)

    # If master zone is not configured for zonegroup
    if master_zone is None:
        hookenv.log("No master zone configured for zonegroup {}"
                    .format(master_zonegroup), level=hookenv.WARNING)
        return False
    return check_zone_has_buckets(master_zone, master_zonegroup)


def check_cluster_has_buckets():
    """Iteratively check if ANY zonegroup has buckets on cluster.

    :rtype: Boolean
    """
    for zonegroup in list_zonegroups():
        if check_zonegroup_has_buckets(zonegroup):
            return True
    return False


def list_sync_groups(bucket=None):
    """List sync policy groups.

    :param bucket: Bucket name. If this this given, the bucket level group
        policies are listed.
    :type bucket: str

    :return: List of sync policy groups.
    :rtype: list
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'sync', 'group', 'get',
    ]
    if bucket:
        cmd.append('--bucket={}'.format(bucket))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return []


def sync_group_exists(group_id, bucket=None):
    """Check if the sync policy group exists.

    :param group_id: Sync policy group id.
    :type group_id: str
    :param bucket: Bucket name. If this this given, the bucket level group
        policy is checked.
    :type bucket: str

    :rtype: Boolean
    """
    for group in list_sync_groups(bucket=bucket):
        if group['key'] == group_id:
            return True
    return False


def get_sync_group(group_id, bucket=None):
    """Get the sync policy group configuration.

    :param group_id: Sync policy group id.
    :type group_id: str
    :param bucket: Bucket name. If this this given, the bucket level group
        policy is returned.
    :type bucket: str

    :return: Sync policy group configuration.
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'sync', 'group', 'get',
        '--group-id={}'.format(group_id),
    ]
    if bucket:
        cmd.append('--bucket={}'.format(bucket))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def create_sync_group(group_id, status, bucket=None):
    """Create a sync policy group.

    :param group_id: ID of the sync policy group to be created.
    :type group_id: str
    :param status: Status of the sync policy group to be created. Must be one
        of the following: 'enabled', 'allowed', 'forbidden'.
    :type status: str
    :param bucket: Bucket name. If this this given, the bucket level group
        policy is created.
    :type bucket: str

    :raises UnknownSyncPolicyState: if the provided status is not one of the
        allowed values.

    :return: Sync policy group configuration.
    :rtype: dict
    """
    if status not in SYNC_POLICY_STATES:
        raise UnknownSyncPolicyState(
            'Unknown sync policy state: {}'.format(status))
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'sync', 'group', 'create',
        '--group-id={}'.format(group_id),
        '--status={}'.format(status),
    ]
    if bucket:
        cmd.append('--bucket={}'.format(bucket))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def remove_sync_group(group_id, bucket=None):
    """Remove a sync group with the given group ID and optional bucket.

    :param group_id: The ID of the sync group to remove.
    :type group_id: str
    :param bucket: Bucket name. If this this given, the bucket level group
        policy is removed.
    :type bucket: str

    :return: The output of the command as a dict.
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'sync', 'group', 'remove',
        '--group-id={}'.format(group_id),
    ]
    if bucket:
        cmd.append('--bucket={}'.format(bucket))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def is_sync_group_update_needed(group_id, flow_id, pipe_id, source_zone,
                                dest_zone, desired_status, desired_flow_type):
    """Check if the sync group (with the given ID) needs updating.

    :param group_id: The ID of the sync group to check.
    :type group_id: str
    :param flow_id: The ID of the sync group flow to check.
    :type flow_id: str
    :param pipe_id: The ID of the sync group pipe to check.
    :type pipe_id: str
    :param source_zone: Source zone of the sync group flow to check.
    :type source_zone: str
    :param dest_zone: Dest zone of the sync group flow to check.
    :type dest_zone: str
    :param desired_status: Desired status of the sync group.
    :type desired_status: str
    :param desired_flow_type: Desired flow type of the sync group data flow.
    :type desired_flow_type: str

    :rtype: Boolean
    """
    # Check if sync group exists.
    if not sync_group_exists(group_id):
        hookenv.log('Sync group "{}" not configured yet'.format(group_id))
        return True
    group = get_sync_group(group_id)

    # Check sync group status.
    if group.get('status') != desired_status:
        hookenv.log('Sync group "{}" status changed to "{}"'.format(
            group["id"], desired_status))
        return True

    # Check if data flow needs to be created or updated.
    if is_sync_group_flow_update_needed(group=group,
                                        flow_id=flow_id,
                                        source_zone=source_zone,
                                        dest_zone=dest_zone,
                                        desired_flow_type=desired_flow_type):
        return True

    # Check if data pipe needs to be created.
    pipes = group.get('pipes', [])
    pipes_ids = [pipe['id'] for pipe in pipes]
    if pipe_id not in pipes_ids:
        hookenv.log('Sync group pipe "{}" not created yet'.format(pipe_id))
        return True

    # Sync group configuration is up-to-date.
    return False


def create_sync_group_flow(group_id, flow_id, flow_type, source_zone,
                           dest_zone):
    """Create a new sync group data flow with the given parameters.

    :param group_id: The ID of the sync group to create the data flow for.
    :type group_id: str
    :param flow_id: The ID of the new data flow.
    :type flow_id: str
    :param flow_type: The type of the new data flow.
    :type flow_type: str
    :param source_zone: The source zone for the new data flow.
    :type source_zone: str
    :param dest_zone: The destination zone for the new data flow.
    :type dest_zone: str

    :raises UnknownSyncFlowType: If an unknown sync flow type is provided.

    :return: Sync group data flow configuration.
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'sync', 'group', 'flow', 'create',
        '--group-id={}'.format(group_id),
        '--flow-id={}'.format(flow_id),
        '--flow-type={}'.format(flow_type),
    ]
    if flow_type == SYNC_FLOW_SYMMETRICAL:
        cmd.append('--zones={},{}'.format(source_zone, dest_zone))
    elif flow_type == SYNC_FLOW_DIRECTIONAL:
        cmd.append('--source-zone={}'.format(source_zone))
        cmd.append('--dest-zone={}'.format(dest_zone))
    else:
        raise UnknownSyncFlowType(
            'Unknown sync flow type {}'.format(flow_type))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def remove_sync_group_flow(group_id, flow_id, flow_type, source_zone=None,
                           dest_zone=None):
    """Remove a sync group data flow.

    :param group_id: The ID of the sync group.
    :type group_id: str
    :param flow_id: The ID of the flow to remove.
    :type flow_id: str
    :param flow_type: The type of the flow to remove.
    :type flow_type: str
    :param source_zone: The source zone of the flow to remove (only for
        directional flows).
    :type source_zone: str
    :param dest_zone: The destination zone of the flow to remove (only for
        directional flows).
    :type dest_zone: str

    :return: The output of the command as a dict.
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'sync', 'group', 'flow', 'remove',
        '--group-id={}'.format(group_id),
        '--flow-id={}'.format(flow_id),
        '--flow-type={}'.format(flow_type),
    ]
    if flow_type == SYNC_FLOW_DIRECTIONAL:
        cmd.append('--source-zone={}'.format(source_zone))
        cmd.append('--dest-zone={}'.format(dest_zone))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def create_sync_group_pipe(group_id, pipe_id, source_zones, dest_zones,
                           source_bucket='*', dest_bucket='*', bucket=None):
    """Create a sync group pipe between source and destination zones.

    :param group_id: The ID of the sync group.
    :type group_id: str
    :param pipe_id: The ID of the sync group pipe.
    :type pipe_id: str
    :param source_zones: A list of source zones.
    :type source_zones: list
    :param dest_zones: A list of destination zones.
    :type dest_zones: list
    :param source_bucket: The source bucket name. Default is '*'.
    :type source_bucket: str
    :param dest_bucket: The destination bucket name. Default is '*'.
    :type dest_bucket: str
    :param bucket: The bucket name. If specified, the sync group pipe will be
        created for this bucket only.
    :type bucket: str

    :return: Sync group pipe configuration.
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'sync', 'group', 'pipe', 'create',
        '--group-id={}'.format(group_id),
        '--pipe-id={}'.format(pipe_id),
        '--source-zones={}'.format(','.join(source_zones)),
        '--source-bucket={}'.format(source_bucket),
        '--dest-zones={}'.format(','.join(dest_zones)),
        '--dest-bucket={}'.format(dest_bucket),
    ]
    if bucket:
        cmd.append('--bucket={}'.format(bucket))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def is_sync_group_flow_update_needed(group, flow_id, source_zone, dest_zone,
                                     desired_flow_type):
    """Check if the given sync group flow needs updating.

    :param group: The sync policy group configuration.
    :type group: dict
    :param flow_id: The ID of the sync group flow to check.
    :type flow_id: str
    :param source_zone: Source zone of the sync group flow to check.
    :type source_zone: str
    :param dest_zone: Dest zone of the sync group flow to check.
    :type dest_zone: str
    :param desired_flow_type: Desired flow type of the sync group data flow.
    :type desired_flow_type: str

    :rtype: Boolean
    """
    symmetrical_flows = group['data_flow'].get('symmetrical', [])
    symmetrical_flows_ids = [flow['id'] for flow in symmetrical_flows]

    directional_flows = group['data_flow'].get('directional', [])
    directional_flows_ids = [
        # NOTE: Directional flows IDs are not present in the sync group
        # configuration. We assume that the ID is a concatenation of the source
        # zone and destination zone, as currently configured by the charm code.
        # This is a safe assumption, because there are unique directional
        # flows for each pair of zones.
        "{}-{}".format(flow['source_zone'], flow['dest_zone'])
        for flow in directional_flows
    ]

    data_flows_ids = symmetrical_flows_ids + directional_flows_ids
    if flow_id not in data_flows_ids:
        hookenv.log('Data flow "{}" not configured yet'.format(flow_id))
        return True

    # Check if the flow type is consistent with the current configuration.
    is_symmetrical = (desired_flow_type == SYNC_FLOW_SYMMETRICAL and
                      flow_id in symmetrical_flows_ids)
    is_directional = (desired_flow_type == SYNC_FLOW_DIRECTIONAL and
                      flow_id in directional_flows_ids)
    if is_symmetrical or is_directional:
        # Data flow is consistent with the current configuration.
        return False

    # Data flow type has changed. We need to remove the old data flow.
    hookenv.log('Data flow "{}" type changed to "{}"'.format(
        flow_id, desired_flow_type))
    old_flow_type = (
        SYNC_FLOW_SYMMETRICAL if desired_flow_type == SYNC_FLOW_DIRECTIONAL
        else SYNC_FLOW_DIRECTIONAL)
    hookenv.log(
        'Removing old data flow "{}" before configuring the new one'.format(
            flow_id))
    remove_sync_group_flow(
        group_id=group["id"], flow_id=flow_id, flow_type=old_flow_type,
        source_zone=source_zone, dest_zone=dest_zone)
    return True
