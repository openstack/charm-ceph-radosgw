#!/usr/bin/env python3
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
import os
import subprocess
import sys
import socket
import uuid

sys.path.append('lib')

import ceph_rgw as ceph
import charms_ceph.utils as ceph_utils
import multisite

from charmhelpers.core.hookenv import (
    ERROR,
    relation_get,
    relation_id as ch_relation_id,
    relation_ids,
    related_units,
    config,
    open_port,
    opened_ports,
    close_port,
    relation_set,
    log,
    DEBUG,
    WARNING,
    Hooks, UnregisteredHookError,
    status_set,
    is_leader,
    leader_set,
    leader_get,
    remote_service_name,
    WORKLOAD_STATES,
)
from charmhelpers.core.strutils import bool_from_string
from charmhelpers.fetch import (
    apt_update,
    apt_install,
    apt_purge,
    add_source,
    filter_installed_packages,
    filter_missing_packages,
)
from charmhelpers.payload.execd import execd_preinstall
from charmhelpers.core.host import (
    cmp_pkgrevno,
    service,
    service_pause,
    service_reload,
    service_restart,
    service_resume,
    service_stop,
)
from charmhelpers.contrib.network.ip import (
    get_relation_ip,
)
from charmhelpers.contrib.openstack.context import ADDRESS_TYPES
from charmhelpers.contrib.openstack.ip import (
    canonical_url,
    PUBLIC, INTERNAL, ADMIN,
)
from charmhelpers.contrib.storage.linux.ceph import (
    send_request_if_needed,
    is_request_complete,
)
from charmhelpers.contrib.openstack.utils import (
    is_unit_paused_set,
    pausable_restart_on_change as restart_on_change,
    series_upgrade_prepare,
    series_upgrade_complete,
)
from charmhelpers.contrib.openstack.ha.utils import (
    generate_ha_relation_data,
)
from utils import (
    assess_status,
    disable_unused_apache_sites,
    listen_port,
    multisite_deployment,
    pause_unit_helper,
    ready_for_service,
    register_configs,
    request_per_unit_key,
    restart_map,
    restart_nonce_changed,
    resume_unit_helper,
    service_name,
    services,
    setup_ipv6,
    systemd_based_radosgw,
)
from charmhelpers.contrib.charmsupport import nrpe
from charmhelpers.contrib.hardening.harden import harden

from charmhelpers.contrib.openstack.cert_utils import (
    get_certificate_request,
    process_certificates,
)

hooks = Hooks()
CONFIGS = register_configs()

PACKAGES = [
    'haproxy',
    'radosgw',
    'apache2'
]

APACHE_PACKAGES = [
    'libapache2-mod-fastcgi',
]

MULTISITE_SYSTEM_USER = 'multisite-sync'
MULTISITE_DEFAULT_SYNC_GROUP_ID = 'default'


def upgrade_available():
    """Check for upgrade for ceph

    :returns: whether an upgrade is available
    :rtype: boolean
    """
    c = config()
    old_version = ceph_utils.resolve_ceph_version(c.previous('source') or
                                                  'distro')
    new_version = ceph_utils.resolve_ceph_version(c.get('source'))
    if (old_version in ceph_utils.UPGRADE_PATHS and
            new_version == ceph_utils.UPGRADE_PATHS[old_version]):
        return True
    return False


def install_packages():
    """Installs necessary packages for the ceph-radosgw service.

    Calling this method when the source config value has changed
    will cause an upgrade of ceph packages to be performed.

    :returns: whether packages were installed or not
    :rtype: boolean
    """
    pkgs_installed = False
    c = config()
    if c.changed('source') or c.changed('key'):
        add_source(c.get('source'), c.get('key'))
        apt_update(fatal=True)

    # NOTE: just use full package list if we're in an upgrade
    #       config-changed execution
    pkgs = (
        PACKAGES if upgrade_available() else
        filter_installed_packages(PACKAGES)
    )
    if pkgs:
        status_set('maintenance', 'Installing radosgw packages')
        if ('apache2' in pkgs):
            # NOTE(lourot): Apache's default config makes it listen on port 80,
            # which will prevent HAProxy from listening on that same port. We
            # use Apache in this setup however for SSL (different port). We
            # need to let Apache free port 80 before we can install HAProxy
            # otherwise HAProxy will crash. See lp:1904411
            log('Installing Apache')
            apt_install(['apache2'], fatal=True)
            disable_unused_apache_sites()
        apt_install(pkgs, fatal=True)
        pkgs_installed = True

    pkgs = filter_missing_packages(APACHE_PACKAGES)
    if pkgs:
        apt_purge(pkgs)

    return pkgs_installed


@hooks.hook('install.real')
@harden()
def install():
    status_set('maintenance', 'Executing pre-install')
    execd_preinstall()
    install_packages()
    # hold the service down until we have keys from ceph
    log('Disable service "{}" until we have keys for it.'
        .format(service_name()), level=DEBUG)
    service_pause(service_name())
    if not os.path.exists('/etc/ceph'):
        os.makedirs('/etc/ceph')
    if is_leader():
        leader_set(namespace_tenants=config('namespace-tenants'))


@hooks.hook('object-store-relation-joined')
def object_store_joined(relation_id=None):
    relation_data = {
        'swift-url':
        "{}:{}".format(canonical_url(CONFIGS, INTERNAL), listen_port())
    }
    relation_set(relation_id=relation_id, relation_settings=relation_data)


@hooks.hook('upgrade-charm.real')
def upgrade_charm():
    if is_leader() and not leader_get('namespace_tenants') == 'True':
        leader_set(namespace_tenants=False)


@hooks.hook('config-changed')
@harden()
def config_changed():
    @restart_on_change(restart_map())
    def _config_changed():
        # if we are paused, delay doing any config changed hooks.
        # It is forced on the resume.
        if is_unit_paused_set():
            log("Unit is pause or upgrading. Skipping config_changed", "WARN")
            return

        # NOTE(wolsen) if an upgrade has been applied, then the radosgw
        # service needs to be restarted as the package doesn't do it by
        # itself. See LP#1906707
        if install_packages():
            log("Packages have been installed/upgraded... restarting", "INFO")
            service_restart(service_name())

        if config('prefer-ipv6'):
            status_set('maintenance', 'configuring ipv6')
            setup_ipv6()

        for r_id in relation_ids('identity-service'):
            identity_changed(relid=r_id)

        for r_id in relation_ids('cluster'):
            cluster_joined(rid=r_id)

        # NOTE(jamespage): Re-exec mon relation for any changes to
        #                  enable ceph pool permissions restrictions
        for r_id in relation_ids('mon'):
            for unit in related_units(r_id):
                mon_relation(r_id, unit)

        # Re-trigger hacluster relations to switch to ifaceless
        # vip configuration
        for r_id in relation_ids('ha'):
            ha_relation_joined(r_id)

        # Refire certificates relations for VIP changes
        for r_id in relation_ids('certificates'):
            certs_joined(r_id)

        # Refire object-store relations for VIP/port changes
        for r_id in relation_ids('object-store'):
            object_store_joined(r_id)

        for r_id in relation_ids('radosgw-user'):
            radosgw_user_changed(r_id)

        process_multisite_relations()

        CONFIGS.write_all()
        configure_https()

        update_nrpe_config()

        port = listen_port()
        open_port(port)
        for opened_port in opened_ports():
            opened_port_number = opened_port.split('/')[0]
            if str(opened_port_number) != str(port):
                close_port(opened_port_number)
                log('Closed port %s in favor of port %s' %
                    (opened_port_number, port))
    _config_changed()


@hooks.hook('mon-relation-departed',
            'mon-relation-changed')
def mon_relation(rid=None, unit=None):
    @restart_on_change(restart_map())
    def _mon_relation():
        key_name = 'rgw.{}'.format(socket.gethostname())
        legacy = True
        if request_per_unit_key():
            legacy = False
            relation_set(relation_id=rid,
                         key_name=key_name)
        try:
            rq = ceph.get_create_rgw_pools_rq(
                prefix=config('zone') or config('pool-prefix'))
        except ValueError as e:
            # The end user has most likely provided a invalid value for
            # a configuration option. Just log the traceback here, the
            # end user will be notified by assess_status() called at
            # the end of the hook execution.
            log('Caught ValueError, invalid value provided for '
                'configuration?: "{}"'.format(str(e)),
                level=DEBUG)
            return

        if is_request_complete(rq, relation='mon'):
            log('Broker request complete', level=DEBUG)
            CONFIGS.write_all()
            # New style per unit keys
            key = relation_get(attribute='{}_key'.format(key_name),
                               rid=rid, unit=unit)
            if not key:
                # Fallback to old style global key
                key = relation_get(attribute='radosgw_key',
                                   rid=rid, unit=unit)
                key_name = None

            if key:
                new_keyring = ceph.import_radosgw_key(key,
                                                      name=key_name)
                # NOTE(jamespage):
                # Deal with switch from radosgw init script to
                # systemd named units for radosgw instances by
                # stopping and disabling the radosgw unit
                if systemd_based_radosgw():
                    service_stop('radosgw')
                    service('disable', 'radosgw')
                    # Update the nrpe config. If we wait for the below
                    # to be called elsewhere, there exists a period
                    # where nagios will report the radosgw service as
                    # down, and also not be monitoring the per
                    # host services.
                    update_nrpe_config(checks_to_remove=['radosgw'])

                # NOTE(jamespage):
                # Multi-site deployments need to defer restart as the
                # zone is not created until the primary relation is
                # joined; restarting here will cause a restart burst
                # in systemd and stop the process restarting once
                # zone configuration is complete.
                if (not is_unit_paused_set() and
                        new_keyring and
                        not multisite_deployment()):
                    log('Resume service "{}" as we now have keys for it.'
                        .format(service_name()), level=DEBUG)
                    service_resume(service_name())

            if multisite_deployment():
                process_multisite_relations()
            elif (ready_for_service(legacy=legacy) and is_leader() and
                  'mon' in CONFIGS.complete_contexts()):
                # In a non multi-site deployment create the
                # zone using the default zonegroup and restart the service
                internal_url = '{}:{}'.format(
                    canonical_url(CONFIGS, INTERNAL),
                    listen_port(),
                )
                endpoints = [internal_url]
                zonegroup = 'default'
                zone = config('zone')
                existing_zones = multisite.list_zones()
                log('Existing zones {}'.format(existing_zones), level=DEBUG)
                if zone not in existing_zones:
                    log("zone '{}' doesn't exist, creating".format(zone))
                    try:
                        multisite.create_zone(zone,
                                              endpoints=endpoints,
                                              default=True, master=True,
                                              zonegroup=zonegroup)
                    except subprocess.CalledProcessError as e:
                        if 'File exists' in e.stderr.decode('UTF-8'):
                            # NOTE(lourot): may have been created in the
                            # background by the Rados Gateway daemon, see
                            # lp:1856106
                            log("zone '{}' existed already after all".format(
                                zone))
                        else:
                            raise

                    existing_zones = multisite.list_zones(retry_on_empty=True)
                    log('Existing zones {}'.format(existing_zones),
                        level=DEBUG)
                    if zone not in existing_zones:
                        raise RuntimeError("Could not create zone '{}'".format(
                            zone))

                    service_restart(service_name())

            for r_id in relation_ids('radosgw-user'):
                radosgw_user_changed(r_id)

        else:
            send_request_if_needed(rq, relation='mon')
    _mon_relation()


@hooks.hook('gateway-relation-joined')
def gateway_relation():
    relation_set(hostname=get_relation_ip('gateway-relation'),
                 port=listen_port())


@hooks.hook('identity-service-relation-joined')
def identity_joined(relid=None):
    if cmp_pkgrevno('radosgw', '0.55') < 0:
        log('Integration with keystone requires ceph >= 0.55')
        sys.exit(1)

    port = listen_port()
    admin_url = '%s:%i/swift' % (canonical_url(CONFIGS, ADMIN), port)
    if leader_get('namespace_tenants') == 'True':
        internal_url = '%s:%s/swift/v1/AUTH_$(project_id)s' % \
            (canonical_url(CONFIGS, INTERNAL), port)
        public_url = '%s:%s/swift/v1/AUTH_$(project_id)s' % \
            (canonical_url(CONFIGS, PUBLIC), port)
    else:
        internal_url = '%s:%s/swift/v1' % \
            (canonical_url(CONFIGS, INTERNAL), port)
        public_url = '%s:%s/swift/v1' % \
            (canonical_url(CONFIGS, PUBLIC), port)
    roles = [x for x in [config('operator-roles'), config('admin-roles')] if x]
    requested_roles = ''
    if roles:
        requested_roles = ','.join(roles) if len(roles) > 1 else roles[0]
    # remove stale settings without service prefix left by old charms,
    # which cause the keystone charm to ignore new settings w/ prefix.
    relation_set(service='',
                 region='',
                 public_url='',
                 internal_url='',
                 admin_url='',
                 relation_id=relid)
    relation_set(swift_service='swift',
                 swift_region=config('region'),
                 swift_public_url=public_url,
                 swift_internal_url=internal_url,
                 swift_admin_url=admin_url,
                 requested_roles=requested_roles,
                 relation_id=relid)
    if cmp_pkgrevno('radosgw', '12.2') >= 0:
        relation_set(s3_service='s3',
                     s3_region=config('region'),
                     s3_public_url='{}:{}/'.format(
                         canonical_url(CONFIGS, PUBLIC), port),
                     s3_internal_url='{}:{}/'.format(
                         canonical_url(CONFIGS, INTERNAL), port),
                     s3_admin_url='{}:{}/'.format(
                         canonical_url(CONFIGS, ADMIN), port),
                     relation_id=relid)


@hooks.hook('identity-service-relation-changed')
def identity_changed(relid=None):
    @restart_on_change(restart_map())
    def _identity_changed():
        identity_joined(relid)
        CONFIGS.write_all()
    _identity_changed()


@hooks.hook('cluster-relation-joined')
def cluster_joined(rid=None):
    @restart_on_change(restart_map())
    def _cluster_joined():
        settings = {}

        for addr_type in ADDRESS_TYPES:
            address = get_relation_ip(
                addr_type,
                cidr_network=config('os-{}-network'.format(addr_type)))
            if address:
                settings['{}-address'.format(addr_type)] = address

        settings['private-address'] = get_relation_ip('cluster')

        relation_set(relation_id=rid, relation_settings=settings)
    _cluster_joined()


@hooks.hook('cluster-relation-changed')
def cluster_changed():
    @restart_on_change(restart_map())
    def _cluster_changed():
        CONFIGS.write_all()
        for r_id in relation_ids('identity-service'):
            identity_joined(relid=r_id)
        for r_id in relation_ids('certificates'):
            for unit in related_units(r_id):
                certs_changed(r_id, unit)
    _cluster_changed()


@hooks.hook('ha-relation-joined')
def ha_relation_joined(relation_id=None):
    settings = generate_ha_relation_data('cephrg')
    relation_set(relation_id=relation_id, **settings)


@hooks.hook('ha-relation-changed')
def ha_relation_changed():
    clustered = relation_get('clustered')
    if clustered:
        log('Cluster configured, notifying other services and'
            'updating keystone endpoint configuration')
        # Tell all related services to start using
        # the VIP instead
        for r_id in relation_ids('identity-service'):
            identity_joined(relid=r_id)


@hooks.hook('nrpe-external-master-relation-joined',
            'nrpe-external-master-relation-changed')
def update_nrpe_config(checks_to_remove=None):
    """
    Update the checks for the nagios plugin.

    :param checks_to_remove: list of short names of nrpe checks to
        remove. For example, pass ['radosgw'] to remove the check for
        the default systemd radosgw service, to make way for per host
        services.
    :type checks_to_remove: list

    """
    # python-dbus is used by check_upstart_job
    apt_install('python-dbus')
    hostname = nrpe.get_nagios_hostname()
    current_unit = nrpe.get_nagios_unit_name()
    nrpe_setup = nrpe.NRPE(hostname=hostname)
    nrpe.copy_nrpe_checks()
    if checks_to_remove is not None:
        log("Removing the following nrpe checks: {}".format(checks_to_remove),
            level=DEBUG)
        for svc in checks_to_remove:
            nrpe_setup.remove_check(shortname=svc)
    nrpe.add_init_service_checks(nrpe_setup, services(), current_unit)
    nrpe.add_haproxy_checks(nrpe_setup, current_unit)
    nrpe_setup.write()


def configure_https():
    '''Enables SSL API Apache config if appropriate and kicks
    identity-service and image-service with any required
    updates
    '''
    CONFIGS.write_all()
    if 'https' in CONFIGS.complete_contexts():
        cmd = ['a2ensite', 'openstack_https_frontend']
        subprocess.check_call(cmd)
    else:
        cmd = ['a2dissite', 'openstack_https_frontend']
        try:
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError:
            # The site is not yet enabled or
            # https is not configured
            pass

    # TODO: improve this by checking if local CN certs are available
    # first then checking reload status (see LP #1433114).
    if not is_unit_paused_set():
        service_reload('apache2', restart_on_failure=True)


@hooks.hook('update-status')
@harden()
def update_status():
    log('Updating status.')


@hooks.hook('pre-series-upgrade')
def pre_series_upgrade():
    log("Running prepare series upgrade hook", "INFO")
    series_upgrade_prepare(
        pause_unit_helper, CONFIGS)


@hooks.hook('post-series-upgrade')
def post_series_upgrade():
    log("Running complete series upgrade hook", "INFO")
    series_upgrade_complete(
        resume_unit_helper, CONFIGS)


@hooks.hook('certificates-relation-joined')
def certs_joined(relation_id=None):
    cert_req_obj = get_certificate_request()
    if config('virtual-hosted-bucket-enabled'):
        cert_req = json.loads(cert_req_obj["cert_requests"])
        for cn in cert_req.keys():
            if cn == config('os-public-hostname'):
                log("Adding wildcard hostname for virtual hosted buckets",
                    "INFO")
                cert_req[cn]["sans"].append("*."+config('os-public-hostname'))
                cert_req_obj['cert_requests'] = json.dumps(cert_req,
                                                           sort_keys=True)
    log("Cert request: {}".format(cert_req_obj), "INFO")
    relation_set(
        relation_id=relation_id,
        relation_settings=cert_req_obj)


@hooks.hook('certificates-relation-changed')
def certs_changed(relation_id=None, unit=None):
    @restart_on_change(restart_map(), stopstart=True)
    def _certs_changed():
        process_certificates('ceph-radosgw', relation_id, unit)
        configure_https()
    _certs_changed()
    for r_id in relation_ids('identity-service'):
        identity_joined(relid=r_id)


def get_radosgw_username(r_id):
    """Generate a username based on a relation id"""
    gw_user = 'juju-' + r_id.replace(":", "-")
    return gw_user


def get_radosgw_system_username(r_id):
    """Generate a username for a system user based on a relation id"""
    gw_user = get_radosgw_username(r_id)
    # There is no way to switch a user from being a system user to a
    # non-system user, so add the '-system' suffix to ensure there is
    # no clash if the user request is updated in the future.
    gw_user = gw_user + "-system"
    return gw_user


@hooks.hook('radosgw-user-relation-departed')
def radosgw_user_departed():
    # If there are no related units then the last unit
    # is currently departing.
    if not related_units():
        r_id = ch_relation_id()
        for user in [get_radosgw_system_username(r_id),
                     get_radosgw_username(r_id)]:
            multisite.suspend_user(user)


@hooks.hook('radosgw-user-relation-changed')
def radosgw_user_changed(relation_id=None):
    if not ready_for_service(legacy=False):
        log('unit not ready, deferring radosgw_user configuration')
        return
    if relation_id:
        r_ids = [relation_id]
    else:
        r_ids = relation_ids('radosgw-user')
    # The leader manages the users and sets the credentials using the
    # the application relation data bag.
    if is_leader():
        for r_id in r_ids:
            remote_app = remote_service_name(r_id)
            relation_data = relation_get(
                rid=r_id,
                app=remote_app)
            if 'system-role' not in relation_data:
                log('system-role not in relation data, cannot create user',
                    level=DEBUG)
                return
            system_user = bool_from_string(
                relation_data.get('system-role', 'false'))
            if system_user:
                gw_user = get_radosgw_system_username(r_id)
                # If there is a pre-existing non-system user then ensure it is
                # suspended
                multisite.suspend_user(get_radosgw_username(r_id))
            else:
                gw_user = get_radosgw_username(r_id)
                # If there is a pre-existing system user then ensure it is
                # suspended
                multisite.suspend_user(get_radosgw_system_username(r_id))
            if gw_user in multisite.list_users():
                (access_key, secret_key) = multisite.get_user_creds(gw_user)
            else:
                (access_key, secret_key) = multisite.create_user(
                    gw_user,
                    system_user=system_user)
            relation_set(
                app=remote_app,
                relation_id=r_id,
                relation_settings={
                    'uid': gw_user,
                    'access-key': access_key,
                    'secret-key': secret_key})
    # Each unit publishes its own endpoint data and daemon id using the
    # unit relation data bag.
    for r_id in r_ids:
        relation_set(
            relation_id=r_id,
            relation_settings={
                'internal-url': "{}:{}".format(
                    canonical_url(CONFIGS, INTERNAL),
                    listen_port()),
                'daemon-id': socket.gethostname()})


@hooks.hook('primary-relation-joined')
def primary_relation_joined(relation_id=None):
    if not ready_for_service(legacy=False):
        log('unit not ready, deferring multisite configuration')
        return

    public_url = '{}:{}'.format(
        canonical_url(CONFIGS, PUBLIC),
        listen_port(),
    )
    endpoints = [public_url]
    realm = config('realm')
    zonegroup = config('zonegroup')
    zone = config('zone')
    access_key = leader_get('access_key')
    secret = leader_get('secret')

    if not all((realm, zonegroup, zone)):
        log('Cannot setup multisite configuration, required config is '
            'missing. realm, zonegroup and zone charm config options must all '
            'be set',
            'WARN')
        return

    relation_set(relation_id=relation_id,
                 realm=realm,
                 zonegroup=zonegroup,
                 url=endpoints[0],
                 access_key=access_key,
                 secret=secret)

    if not is_leader():
        log('Cannot setup multisite configuration, this unit is not the '
            'leader')
        return

    if not leader_get('restart_nonce'):
        log('No restart_nonce found')
        # NOTE(jamespage):
        # This is an ugly kludge to force creation of the required data
        # items in the .rgw.root pool prior to the radosgw process being
        # started; radosgw-admin does not currently have a way of doing
        # this operation but a period update will force it to be created.
        multisite.update_period(fatal=False)

    mutation = False

    if realm not in multisite.list_realms():
        log('Realm {} not found, creating now'.format(realm))
        multisite.create_realm(realm, default=True)
        mutation = True

    # Migration if primary site has buckets configured.
    # Migration involves renaming existing zone/zongroups such that existing
    # buckets and their objects can be preserved on the primary site.
    if multisite.check_cluster_has_buckets() is True:
        log('Migrating to multisite with zone ({}) and zonegroup ({})'
            .format(zone, zonegroup), level=DEBUG)
        zones = multisite.list_zones()
        zonegroups = multisite.list_zonegroups()

        if (len(zonegroups) > 1) and (zonegroup not in zonegroups):
            log('Multiple zonegroups found {}, aborting.'
                .format(zonegroups), level=ERROR)
            return

        if (len(zones) > 1) and (zone not in zones):
            log('Multiple zones found {}, aborting.'
                .format(zones), level=ERROR)
            return

        rename_result = multisite.rename_multisite_config(
            zonegroups, zonegroup,
            zones, zone
        )
        if rename_result is None:
            return

        modify_result = multisite.modify_multisite_config(
            zone, zonegroup,
            endpoints=endpoints,
            realm=realm
        )
        if modify_result is None:
            return
        mutation = True

    if zonegroup not in multisite.list_zonegroups():
        log('zonegroup {} not found, creating now'.format(zonegroup))
        multisite.create_zonegroup(zonegroup,
                                   endpoints=endpoints,
                                   default=True, master=True,
                                   realm=realm)
        mutation = True

    if zone not in multisite.list_zones():
        log('zone {} not found, creating now'.format(zone))
        multisite.create_zone(zone,
                              endpoints=endpoints,
                              default=True, master=True,
                              zonegroup=zonegroup)
        mutation = True

    if MULTISITE_SYSTEM_USER not in multisite.list_users():
        log('User {} not found, creating now'.format(MULTISITE_SYSTEM_USER))
        access_key, secret = multisite.create_system_user(
            MULTISITE_SYSTEM_USER
        )
        multisite.modify_zone(zone,
                              access_key=access_key,
                              secret=secret)
        leader_set(access_key=access_key,
                   secret=secret)
        mutation = True

    if mutation:
        log(
            'Mutation detected. Restarting {}.'.format(service_name()),
            'INFO')
        multisite.update_period(zonegroup=zonegroup, zone=zone)
        CONFIGS.write_all()
        service_restart(service_name())
        leader_set(restart_nonce=str(uuid.uuid4()))
    else:
        log('No mutation detected.', 'INFO')

    relation_set(relation_id=relation_id,
                 access_key=access_key,
                 secret=secret)


@hooks.hook('primary-relation-changed')
def primary_relation_changed(relation_id=None, unit=None):
    if not is_leader():
        log('Cannot setup multisite configuration, this unit is not the '
            'leader')
        return
    if not ready_for_service(legacy=False):
        log('unit not ready, deferring multisite configuration')
        return

    sync_policy_state = config('sync-policy-state')
    if not sync_policy_state:
        log("The config sync-policy-state is not set. Skipping zone group "
            "default sync policy configuration")
        return

    secondary_data = relation_get(rid=relation_id, unit=unit)
    if not all((secondary_data.get('zone'),
                secondary_data.get('sync_policy_flow_type'))):
        log("Defer processing until secondary RGW has provided required data")
        return

    zonegroup = config('zonegroup')
    primary_zone = config('zone')
    secondary_zone = secondary_data['zone']
    sync_flow_type = secondary_data['sync_policy_flow_type']

    if (secondary_data.get('zone_tier_type') == 'cloud' and
            sync_flow_type != multisite.SYNC_FLOW_DIRECTIONAL):
        log("The secondary zone is set with cloud tier type. Ignoring "
            "configured {} sync policy flow, and using {}.".format(
                sync_flow_type,
                multisite.SYNC_FLOW_DIRECTIONAL),
            level=WARNING)
        sync_flow_type = multisite.SYNC_FLOW_DIRECTIONAL

    flow_id = '{}-{}'.format(primary_zone, secondary_zone)
    pipe_id = '{}-{}'.format(primary_zone, secondary_zone)

    mutation = multisite.is_sync_group_update_needed(
        group_id=MULTISITE_DEFAULT_SYNC_GROUP_ID,
        flow_id=flow_id,
        pipe_id=pipe_id,
        source_zone=primary_zone,
        dest_zone=secondary_zone,
        desired_status=sync_policy_state,
        desired_flow_type=sync_flow_type,
    )

    if mutation:
        multisite.create_sync_group(
            group_id=MULTISITE_DEFAULT_SYNC_GROUP_ID,
            status=sync_policy_state)
        multisite.create_sync_group_flow(
            group_id=MULTISITE_DEFAULT_SYNC_GROUP_ID,
            flow_id=flow_id,
            flow_type=sync_flow_type,
            source_zone=primary_zone,
            dest_zone=secondary_zone)
        source_zones = [primary_zone, secondary_zone]
        dest_zones = [primary_zone, secondary_zone]
        if sync_flow_type == multisite.SYNC_FLOW_DIRECTIONAL:
            source_zones = [primary_zone]
            dest_zones = [secondary_zone]
        multisite.create_sync_group_pipe(
            group_id=MULTISITE_DEFAULT_SYNC_GROUP_ID,
            pipe_id=pipe_id,
            source_zones=source_zones,
            dest_zones=dest_zones)
        log(
            'Mutation detected. Restarting {}.'.format(service_name()),
            'INFO')
        multisite.update_period(zonegroup=zonegroup, zone=primary_zone)
        CONFIGS.write_all()
        service_restart(service_name())
        leader_set(restart_nonce=str(uuid.uuid4()))
    else:
        log('No mutation detected.', 'INFO')


@hooks.hook('primary-relation-departed')
@hooks.hook('secondary-relation-departed')
def multisite_relation_departed():
    if not is_leader():
        log('Cannot remove multisite relation, this unit is not the leader')
        return

    if not ready_for_service(legacy=False):
        raise RuntimeError("Leader unit not ready for service.")

    zone = config('zone')
    zonegroup = config('zonegroup')
    realm = config('realm')

    # If config zone/zonegroup not present on site,
    # remove-relation is called prematurely
    if not multisite.is_multisite_configured(zone=zone,
                                             zonegroup=zonegroup):
        log('Multisite is not configured, skipping scaledown.')
        return

    zonegroup_info = multisite.get_zonegroup_info(zonegroup)
    # remove other zones from zonegroup
    for zone_info in zonegroup_info['zones']:
        if zone_info['name'] is not zone:
            multisite.remove_zone_from_zonegroup(
                zone_info['name'], zonegroup
            )

    # modify self as master zone.
    multisite.modify_zone(zone, default=True, master=True,
                          zonegroup=zonegroup)

    # Update period.
    multisite.update_period(
        fatal=True, zonegroup=zonegroup,
        zone=zone, realm=realm
    )

    # Verify multisite is not configured.
    if multisite.is_multisite_configured(zone=zone,
                                         zonegroup=zonegroup):
        status_set(WORKLOAD_STATES.BLOCKED,
                   "Failed to do a clean scaledown.")
        raise RuntimeError("Residual multisite config at local site.")


@hooks.hook('secondary-relation-changed')
def secondary_relation_changed(relation_id=None, unit=None):
    if not is_leader():
        log('Cannot setup multisite configuration, this unit is not the '
            'leader')
        return
    if not ready_for_service(legacy=False):
        log('unit not ready, deferring multisite configuration')
        return

    master_data = relation_get(rid=relation_id, unit=unit)
    if not all((master_data.get('realm'),
                master_data.get('zonegroup'),
                master_data.get('access_key'),
                master_data.get('secret'),
                master_data.get('url'))):
        log("Defer processing until primary RGW has provided required data")
        return

    public_url = '{}:{}'.format(
        canonical_url(CONFIGS, PUBLIC),
        listen_port(),
    )
    endpoints = [public_url]

    realm = config('realm')
    zonegroup = config('zonegroup')
    zone = config('zone')

    if (realm, zonegroup) != (master_data['realm'],
                              master_data['zonegroup']):
        log("Mismatched configuration so stop multi-site configuration now")
        return

    if not leader_get('restart_nonce'):
        log('No restart_nonce found')
        # NOTE(jamespage):
        # This is an ugly kludge to force creation of the required data
        # items in the .rgw.root pool prior to the radosgw process being
        # started; radosgw-admin does not currently have a way of doing
        # this operation but a period update will force it to be created.
        multisite.update_period(fatal=False)

    relation_set(relation_id=relation_id,
                 sync_policy_flow_type=config('sync-policy-flow-type'))

    mutation = False

    # NOTE(utkarshbhatthere):
    # A site with existing data can create inconsistencies when added as a
    # secondary site for RGW. Hence it must be pristine.
    if multisite.check_cluster_has_buckets():
        log("Non-Pristine site can't be used as secondary", level=ERROR)
        return

    if realm not in multisite.list_realms():
        log('Realm {} not found, pulling now'.format(realm))
        multisite.pull_realm(url=master_data['url'],
                             access_key=master_data['access_key'],
                             secret=master_data['secret'])
        multisite.pull_period(url=master_data['url'],
                              access_key=master_data['access_key'],
                              secret=master_data['secret'])
        multisite.set_default_realm(realm)
        mutation = True

    if zone not in multisite.list_zones():
        log('zone {} not found, creating now'.format(zone))
        multisite.pull_period(url=master_data['url'],
                              access_key=master_data['access_key'],
                              secret=master_data['secret'])
        multisite.create_zone(zone,
                              endpoints=endpoints,
                              default=False, master=False,
                              zonegroup=zonegroup,
                              access_key=master_data['access_key'],
                              secret=master_data['secret'])
        mutation = True

    if mutation:
        log(
            'Mutation detected. Restarting {}.'.format(service_name()),
            'INFO')
        multisite.update_period(zonegroup=zonegroup, zone=zone)
        CONFIGS.write_all()
        service_restart(service_name())
        leader_set(restart_nonce=str(uuid.uuid4()))
    else:
        log('No mutation detected.', 'INFO')

    relation_set(relation_id=relation_id, zone=zone)


@hooks.hook('master-relation-departed')
@hooks.hook('slave-relation-departed')
def master_slave_relation_departed():
    log("departed relation is deprecated", "WARN")
    multisite_relation_departed()


@hooks.hook('master-relation-joined')
def master_relation_joined(relation_id=None):
    log("This relation is deprecated, use primary-secondary relation instead",
        "WARN")
    primary_relation_joined(relation_id)


@hooks.hook('slave-relation-changed')
def slave_relation_changed(relation_id=None, unit=None):
    log("This relation is deprecated, use primary-secondary relation instead",
        "WARN")
    secondary_relation_changed(relation_id, unit)


@hooks.hook('leader-settings-changed')
def leader_settings_changed():
    # NOTE: leader unit will only ever set leader storage
    #       data when multi-site realm, zonegroup, zone or user
    #       data has been created/changed - trigger restarts
    #       of rgw services.
    if restart_nonce_changed(leader_get('restart_nonce')):
        service_restart(service_name())
    if not is_leader():
        # Deprecated Master/Slave relation
        for r_id in relation_ids('master'):
            master_relation_joined(r_id)
        # Primary/Secondary relation
        for r_id in relation_ids('primary'):
            primary_relation_joined(r_id)
            for unit in related_units(r_id):
                primary_relation_changed(r_id, unit)
        for r_id in relation_ids('radosgw-user'):
            radosgw_user_changed(r_id)


def process_multisite_relations():
    """Re-trigger any pending multisite relations"""
    # Deprecated Master/Slave relation
    for r_id in relation_ids('master'):
        master_relation_joined(r_id)
    for r_id in relation_ids('slave'):
        for unit in related_units(r_id):
            slave_relation_changed(r_id, unit)
    # Primary/Secondary relation
    for r_id in relation_ids('primary'):
        primary_relation_joined(r_id)
        for unit in related_units(r_id):
            primary_relation_changed(r_id, unit)
    for r_id in relation_ids('secondary'):
        for unit in related_units(r_id):
            secondary_relation_changed(r_id, unit)


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))
    except ValueError as e:
        # Handle any invalid configuration values
        status_set(WORKLOAD_STATES.BLOCKED, str(e))
    else:
        assess_status(CONFIGS)
