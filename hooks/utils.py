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
import socket
import subprocess

from collections import OrderedDict
from copy import deepcopy

import boto3

import ceph_radosgw_context
import multisite

from charmhelpers.core.hookenv import (
    relation_get,
    relation_ids,
    related_units,
    application_version_set,
    config,
    leader_get,
    leader_set,
    log,
)
from charmhelpers.contrib.openstack import (
    context,
    templating,
)
from charmhelpers.contrib.openstack.utils import (
    make_assess_status_func,
    pause_unit,
    resume_unit,
)
from charmhelpers.contrib.hahelpers.cluster import (
    get_hacluster_config,
    https,
)
from charmhelpers.core.host import (
    cmp_pkgrevno,
    lsb_release,
    CompareHostReleases,
    init_is_systemd,
    service,
    service_running,
)
from charmhelpers.fetch import (
    apt_cache,
    apt_install,
    apt_pkg,
    apt_update,
    add_source,
    filter_installed_packages,
    get_upstream_version,
)
from charmhelpers.core import unitdata

# The interface is said to be satisfied if anyone of the interfaces in the
# list has a complete context.
REQUIRED_INTERFACES = {
    'mon': ['mon'],
}
CEPHRG_HA_RES = 'grp_cephrg_vips'
TEMPLATES_DIR = 'templates'
TEMPLATES = 'templates/'
HAPROXY_CONF = '/etc/haproxy/haproxy.cfg'
CEPH_DIR = '/etc/ceph'
CEPH_CONF = '{}/ceph.conf'.format(CEPH_DIR)

VERSION_PACKAGE = 'radosgw'

UNUSED_APACHE_SITE_FILES = ["/etc/apache2/sites-available/000-default.conf"]
APACHE_PORTS_FILE = "/etc/apache2/ports.conf"
APACHE_SITE_CONF = '/etc/apache2/sites-available/openstack_https_frontend'
APACHE_SITE_24_CONF = '/etc/apache2/sites-available/' \
    'openstack_https_frontend.conf'

BASE_RESOURCE_MAP = OrderedDict([
    (HAPROXY_CONF, {
        'contexts': [context.HAProxyContext(singlenode_mode=True),
                     ceph_radosgw_context.HAProxyContext()],
        'services': ['haproxy'],
    }),
    (CEPH_CONF, {
        'contexts': [ceph_radosgw_context.MonContext()],
        'services': [],
    }),
    (APACHE_SITE_CONF, {
        'contexts': [ceph_radosgw_context.ApacheSSLContext()],
        'services': ['apache2'],
    }),
    (APACHE_SITE_24_CONF, {
        'contexts': [ceph_radosgw_context.ApacheSSLContext()],
        'services': ['apache2'],
    }),
])


def listen_port():
    """Determine port to listen to.

    The value in configuration will be used if specified, otherwise the default
    will be determined based on presence of TLS configuration.

    :returns: Port number
    :rtype: int
    """
    if https():
        default_port = 443
    else:
        default_port = 80
    return config('port') or default_port


def resource_map():
    """Dynamically generate a map of resources.

    These will be managed for a single hook execution.
    """
    resource_map = deepcopy(BASE_RESOURCE_MAP)

    if not https():
        resource_map.pop(APACHE_SITE_CONF)
        resource_map.pop(APACHE_SITE_24_CONF)
    else:
        if os.path.exists('/etc/apache2/conf-available'):
            resource_map.pop(APACHE_SITE_CONF)
        else:
            resource_map.pop(APACHE_SITE_24_CONF)

    resource_map[CEPH_CONF]['services'] = [service_name()]
    return resource_map


def restart_map():
    return OrderedDict([(cfg, v['services'])
                        for cfg, v in resource_map().items()
                        if v['services']])


# Hardcoded to icehouse to enable use of charmhelper templating/context tools
# Ideally these function would support non-OpenStack services
def register_configs(release='icehouse'):
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)
    CONFIGS = resource_map()
    pkg = 'radosgw'
    if not filter_installed_packages([pkg]) and cmp_pkgrevno(pkg, '0.55') >= 0:
        # Add keystone configuration if found
        CONFIGS[CEPH_CONF]['contexts'].append(
            ceph_radosgw_context.IdentityServiceContext()
        )
    for cfg, rscs in CONFIGS.items():
        configs.register(cfg, rscs['contexts'])
    return configs


def services():
    """Returns a list of services associate with this charm."""
    _services = []
    for v in resource_map().values():
        _services.extend(v.get('services', []))
    return list(set(_services))


def get_optional_interfaces():
    """Return the optional interfaces that should be checked if the relavent
    relations have appeared.
    :returns: {general_interface: [specific_int1, specific_int2, ...], ...}
    """
    optional_interfaces = {}
    if relation_ids('ha'):
        optional_interfaces['ha'] = ['cluster']
    if (cmp_pkgrevno('radosgw', '0.55') >= 0 and
            relation_ids('identity-service')):
        optional_interfaces['identity'] = ['identity-service']
    return optional_interfaces


def get_zones_zonegroups():
    """Get a tuple with lists of zones and zonegroups existing on site

    :rtype: tuple
    """
    return multisite.list_zones(), multisite.list_zonegroups()


def check_optional_config_and_relations(configs):
    """Check that if we have a relation_id for high availability that we can
    get the hacluster config.  If we can't then we are blocked.  This function
    is called from assess_status/set_os_workload_status as the charm_func and
    needs to return either 'unknown', '' if there is no problem or the status,
    message if there is a problem.

    :param configs: an OSConfigRender() instance.
    :return 2-tuple: (string, string) = (status, message)
    """
    if relation_ids('ha'):
        try:
            get_hacluster_config()
        except Exception:
            return ('blocked',
                    'hacluster missing configuration: '
                    'vip, vip_iface, vip_cidr')

    multisite_config = (config('realm'),
                        config('zonegroup'),
                        config('zone'))
    master_configured = (leader_get('access_key'),
                         leader_get('secret'),
                         leader_get('restart_nonce'))

    # An operator may have deployed both relations
    primary_rids = relation_ids('master') + relation_ids('primary')
    secondary_rids = relation_ids('slave') + relation_ids('secondary')
    multisite_rids = primary_rids + secondary_rids

    # Any realm or zonegroup config is present, multisite checks can be done.
    # zone config can't be used because it's used by default.
    if config('realm') or config('zonegroup') or multisite_rids:
        # All of Realm, zonegroup, and zone must be configured.
        if not all(multisite_config):
            return ('blocked',
                    'multi-site configuration incomplete '
                    '(realm={realm}, zonegroup={zonegroup}'
                    ', zone={zone})'.format(**config()))

        # Primary/Secondary Relation should be configured.
        if not multisite_rids:
            return ('blocked',
                    'multi-site configuration but primary/secondary '
                    'relation missing')

        # Primary site status check
        if primary_rids:
            # Migration: The system is not multisite already.
            if not multisite.is_multisite_configured(config('zone'),
                                                     config('zonegroup')):
                if multisite.check_cluster_has_buckets():
                    zones, zonegroups = get_zones_zonegroups()
                    status_msg = "Multiple zone or zonegroup configured, " \
                                 "use action 'config-multisite-values' to " \
                                 "resolve."
                    if (len(zonegroups) > 1 and
                            config('zonegroup') not in zonegroups):
                        return('blocked', status_msg)

                    if len(zones) > 1 and config('zone') not in zones:
                        return('blocked', status_msg)

                    if not all(master_configured):
                        return ('blocked', "Failure in Multisite migration, "
                                           "Refer to Logs.")
            # Non-Migration scenario.
            if not all(master_configured):
                return ('waiting',
                        'waiting for configuration of master zone')

        # Secondary site status check
        if secondary_rids:
            # Migration: The system is not multisite already.
            if not multisite.is_multisite_configured(config('zone'),
                                                     config('zonegroup')):
                if multisite.check_cluster_has_buckets():
                    return ('blocked',
                            "Non-Pristine RGW site can't be used as secondary")

            multisite_ready = False
            for rid in secondary_rids:
                for unit in related_units(rid):
                    if relation_get('url', unit=unit, rid=rid):
                        multisite_ready = True
                        continue
            if not multisite_ready:
                return ('waiting',
                        'multi-site master relation incomplete')

    # Check that provided Ceph BlueStoe configuration is valid.
    try:
        bluestore_compression = context.CephBlueStoreCompressionContext()
        bluestore_compression.validate()
    except ValueError as e:
        return ('blocked', 'Invalid configuration: {}'.format(str(e)))

    if (config('virtual-hosted-bucket-enabled') and
            not config('os-public-hostname')):
        return ('blocked', "os-public-hostname must have a value "
                           "when virtual hosted bucket is enabled")

    # return 'unknown' as the lowest priority to not clobber an existing
    # status.
    return 'unknown', ''


def setup_ipv6():
    ubuntu_rel = lsb_release()['DISTRIB_CODENAME'].lower()
    if CompareHostReleases(ubuntu_rel) < "trusty":
        raise Exception("IPv6 is not supported in the charms for Ubuntu "
                        "versions less than Trusty 14.04")

    # Need haproxy >= 1.5.3 for ipv6 so for Trusty if we are <= Kilo we need to
    # use trusty-backports otherwise we can use the UCA.
    vc = apt_pkg.version_compare(get_pkg_version('haproxy'), '1.5.3')
    if ubuntu_rel == 'trusty' and vc == -1:
        add_source('deb http://archive.ubuntu.com/ubuntu trusty-backports '
                   'main')
        apt_update(fatal=True)
        apt_install('haproxy/trusty-backports', fatal=True)


def assess_status(configs):
    """Assess status of current unit.

    Decides what the state of the unit should be based on the current
    configuration.
    SIDE EFFECT: calls set_os_workload_status(...) which sets the workload
    status of the unit.
    Also calls status_set(...) directly if paused state isn't complete.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    assess_status_func(configs)()
    application_version_set(get_upstream_version(VERSION_PACKAGE))


def assess_status_func(configs):
    """Helper function to create the function that will assess_status() for
    the unit.
    Uses charmhelpers.contrib.openstack.utils.make_assess_status_func() to
    create the appropriate status function and then returns it.
    Used directly by assess_status() and also for pausing and resuming
    the unit.

    NOTE: REQUIRED_INTERFACES is augmented with the optional interfaces
    depending on the current config before being passed to the
    make_assess_status_func() function.

    NOTE(ajkavanagh) ports are not checked due to race hazards with services
    that don't behave sychronously w.r.t their service scripts.  e.g.
    apache2.
    @param configs: a templating.OSConfigRenderer() object
    @return f() -> None : a function that assesses the unit's workload status
    """
    required_interfaces = REQUIRED_INTERFACES.copy()
    required_interfaces.update(get_optional_interfaces())
    return make_assess_status_func(
        configs, required_interfaces,
        charm_func=check_optional_config_and_relations,
        services=services(), ports=None)


def pause_unit_helper(configs):
    """Helper function to pause a unit, and then call assess_status(...) in
    effect, so that the status is correctly updated.
    Uses charmhelpers.contrib.openstack.utils.pause_unit() to do the work.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    _pause_resume_helper(pause_unit, configs)


def resume_unit_helper(configs):
    """Helper function to resume a unit, and then call assess_status(...) in
    effect, so that the status is correctly updated.
    Uses charmhelpers.contrib.openstack.utils.resume_unit() to do the work.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    _pause_resume_helper(resume_unit, configs)


def _pause_resume_helper(f, configs):
    """Helper function that uses the make_assess_status_func(...) from
    charmhelpers.contrib.openstack.utils to create an assess_status(...)
    function that can be used with the pause/resume of the unit
    @param f: the function to be used with the assess_status(...) function
    @returns None - this function is executed for its side-effect
    """
    # TODO(ajkavanagh) - ports= has been left off because of the race hazard
    # that exists due to service_start()
    f(assess_status_func(configs),
      services=services(),
      ports=None)


def get_pkg_version(name):
    pkg = apt_cache()[name]
    version = None
    if pkg.current_ver:
        version = apt_pkg.upstream_version(pkg.current_ver.ver_str)
    return version


def disable_unused_apache_sites():
    """Ensure that unused apache configurations are disabled to prevent them
    from conflicting with the charm-provided version.
    """
    log('Disabling unused Apache sites')
    for apache_site_file in UNUSED_APACHE_SITE_FILES:
        apache_site = apache_site_file.split('/')[-1].split('.')[0]
        if os.path.exists(apache_site_file):
            try:
                # Try it cleanly
                subprocess.check_call(['a2dissite', apache_site])
            except subprocess.CalledProcessError:
                # Remove the file
                os.remove(apache_site_file)

    with open(APACHE_PORTS_FILE, 'w') as ports:
        ports.write("")

    if service_running('apache2'):
        log('Restarting Apache')
        service('restart', 'apache2')


def systemd_based_radosgw():
    """Determine if install should use systemd based radosgw instances"""
    host = socket.gethostname()
    for rid in relation_ids('mon'):
        for unit in related_units(rid):
            if relation_get('rgw.{}_key'.format(host), rid=rid, unit=unit):
                return True
    return False


def request_per_unit_key():
    """Determine if a per-unit cephx key should be requested"""
    return (cmp_pkgrevno('radosgw', '12.2.0') >= 0 and init_is_systemd())


def service_name():
    """Determine the name of the RADOS Gateway service

    :return: service name to use
    :rtype: str
    """
    if systemd_based_radosgw():
        return 'ceph-radosgw@rgw.{}'.format(socket.gethostname())
    else:
        return 'radosgw'


def ready_for_service(legacy=True):
    """
    Determine when local unit is ready to service requests determined
    by presentation of required cephx keys on the mon relation and
    presence of the associated keyring in /etc/ceph.

    :param legacy: whether to check for legacy key support
    :type legacy: boolean
    :return: whether unit is ready
    :rtype: boolean
    """
    name = 'rgw.{}'.format(socket.gethostname())
    for rid in relation_ids('mon'):
        for unit in related_units(rid):
            if (relation_get('{}_key'.format(name),
                             rid=rid, unit=unit) and
                    os.path.exists(
                        os.path.join(
                            CEPH_DIR,
                            'ceph.client.{}.keyring'.format(name)
                        ))):
                return True
            if (legacy and
                    relation_get('radosgw_key',
                                 rid=rid, unit=unit) and
                    os.path.exists(
                        os.path.join(
                            CEPH_DIR,
                            'keyring.rados.gateway'
                        ))):
                return True
    return False


def restart_nonce_changed(nonce):
    """
    Determine whether the restart nonce provided has changed
    since this function was last invoked.

    :param nonce: value to confirm has changed against the
                  remembered value for restart_nonce.
    :type nonce: str
    :return: whether nonce has changed value
    :rtype: boolean
    """
    db = unitdata.kv()
    nonce_key = 'restart_nonce'
    if nonce != db.get(nonce_key):
        db.set(nonce_key, nonce)
        db.flush()
        return True
    return False


def multisite_deployment():
    """Determine if deployment is multi-site

    :returns: whether multi-site deployment is configured
    :rtype: boolean
    """
    return all((config('zone'),
                config('zonegroup'),
                config('realm')))


def boto_client(access_key, secret_key, endpoint):
    return boto3.resource("s3",
                          verify=False,
                          endpoint_url=endpoint,
                          aws_access_key_id=access_key,
                          aws_secret_access_key=secret_key)


def set_s3_app(app, bucket, access_key, secret_key):
    """Store known s3 app info."""
    apps = all_s3_apps()
    if app not in apps:
        apps[app] = {
            "bucket": bucket,
            "access-key": access_key,
            "secret-key": secret_key,
        }
        leader_set({"s3-apps": json.dumps(apps)})


def s3_app(app):
    """Return s3 app info."""
    apps = all_s3_apps()
    return apps.get(app)


def all_s3_apps():
    """Return all s3 app info."""
    apps = leader_get("s3-apps")
    if not apps:
        return {}
    return json.loads(apps)


def clear_s3_app(app):
    """Delete s3 app info if present."""
    apps = all_s3_apps()
    if app in apps:
        del apps[app]
        leader_set({"s3-apps": json.dumps(apps)})
