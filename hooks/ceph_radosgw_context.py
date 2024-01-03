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
import re
import socket
import tempfile
import shutil

import multisite
from charmhelpers.contrib.openstack import context
from charmhelpers.contrib.hahelpers.cluster import (
    determine_api_port,
    determine_apache_port,
    https,
)
from charmhelpers.core.host import (
    cmp_pkgrevno,
    arch,
)
from charmhelpers.core.hookenv import (
    DEBUG,
    WARNING,
    ERROR,
    config,
    log,
    related_units,
    relation_get,
    relation_ids,
    unit_public_ip,
    leader_get,
)
from charmhelpers.contrib.network.ip import (
    format_ipv6_addr,
    get_ipv6_addr,
)
from charmhelpers.contrib.storage.linux.ceph import CephConfContext

import utils


BEAST_FRONTEND = 'beast'
CIVETWEB_FRONTEND = 'civetweb'
SUPPORTED_FRONTENDS = (BEAST_FRONTEND, CIVETWEB_FRONTEND)
UNSUPPORTED_BEAST_ARCHS = ('s390x', 'riscv64')


class ApacheSSLContext(context.ApacheSSLContext):
    interfaces = ['https']
    service_namespace = 'ceph-radosgw'

    def __call__(self):
        self.external_ports = [utils.listen_port()]
        ctx = super(ApacheSSLContext, self).__call__()
        ctx['virtual_hosted_bucket_enabled'] = \
            config('virtual-hosted-bucket-enabled')
        return ctx


class HAProxyContext(context.HAProxyContext):

    def __call__(self):
        ctxt = super(HAProxyContext, self).__call__()
        port = utils.listen_port()
        service = 'cephradosgw-server'

        # Apache ports
        a_cephradosgw_api = determine_apache_port(port, singlenode_mode=True)

        port_mapping = {
            service: [port, a_cephradosgw_api]
        }

        ctxt['cephradosgw_bind_port'] = determine_api_port(
            port,
            singlenode_mode=True,
        )

        # for haproxy.conf
        backend_options = {
            service: [{
                'option': 'httpchk GET /swift/healthcheck',
            }]
        }

        ctxt['service_ports'] = port_mapping
        ctxt['backend_options'] = backend_options
        ctxt['https'] = https()

        return ctxt


class IdentityServiceContext(context.IdentityServiceContext):
    interfaces = ['identity-service']

    def __call__(self):
        ctxt = super(IdentityServiceContext, self).__call__()
        if not ctxt:
            return

        if cmp_pkgrevno('radosgw', "10.2.0") >= 0:
            ctxt['auth_keystone_v3_supported'] = True

        if (not ctxt.get('admin_domain_id') and
                float(ctxt.get('api_version', '2.0')) < 3):
            ctxt.pop('admin_domain_id')

        ctxt['auth_type'] = 'keystone'
        if cmp_pkgrevno('radosgw', '15.0.0') < 0:
            ctxt['keystone_revocation_parameter_supported'] = True
        if cmp_pkgrevno('radosgw', "11.0.0") >= 0:
            ctxt['user_roles'] = config('operator-roles')
            ctxt['admin_roles'] = config('admin-roles')
        else:
            ctxt['user_roles'] = config('operator-roles')
            if config('admin-roles'):
                ctxt['user_roles'] += (',' + config('admin-roles'))
        ctxt['cache_size'] = config('cache-size')
        ctxt['namespace_tenants'] = leader_get('namespace_tenants') == 'True'
        if self.context_complete(ctxt):
            return ctxt
        return {}


def ensure_host_resolvable_v6(hostname):
    """Ensure that we can resolve our hostname to an IPv6 address by adding it
    to /etc/hosts if it is not already resolvable.
    """
    try:
        socket.getaddrinfo(hostname, None, socket.AF_INET6)
    except socket.gaierror:
        log("Host '%s' is not ipv6 resolvable - adding to /etc/hosts" %
            hostname, level=DEBUG)
    else:
        log("Host '%s' appears to be ipv6 resolvable" % (hostname),
            level=DEBUG)
        return

    # This must be the backend address used by haproxy
    host_addr = get_ipv6_addr(exc_list=[config('vip')])[0]
    dtmp = tempfile.mkdtemp()
    try:
        tmp_hosts = os.path.join(dtmp, 'hosts')
        shutil.copy('/etc/hosts', tmp_hosts)
        with open(tmp_hosts, 'a+') as fd:
            lines = fd.readlines()
            for line in lines:
                key = r"^%s\s+" % (host_addr)
                if re.search(key, line):
                    break
            else:
                fd.write("%s\t%s\n" % (host_addr, hostname))

            os.rename(tmp_hosts, '/etc/hosts')
    finally:
        shutil.rmtree(dtmp)


def resolve_http_frontend():
    """Automatically determine the HTTP frontend configuration

    Determines the best HTTP frontend configuration based
    on the Ceph release in use and the architecture of the
    machine being used.

    :returns http frontend configuration to use.
    :rtype: str
    """
    octopus_or_later = cmp_pkgrevno('radosgw', '15.2.0') >= 0
    pacific_or_later = cmp_pkgrevno('radosgw', '16.2.0') >= 0
    if octopus_or_later:
        # Pacific or later supports beast on all architectures
        # but octopus does not support s390x or riscv64
        if not pacific_or_later and arch() in UNSUPPORTED_BEAST_ARCHS:
            return CIVETWEB_FRONTEND
        else:
            return BEAST_FRONTEND
    return CIVETWEB_FRONTEND


def validate_http_frontend(frontend_config):
    """Validate HTTP frontend configuration

    :param frontend_config: user provided config value
    :type: str
    :raises: ValueError if the provided config is not valid
    """
    mimic_or_later = cmp_pkgrevno('radosgw', '13.2.0') >= 0
    pacific_or_later = cmp_pkgrevno('radosgw', '16.2.0') >= 0
    quincy_or_later = cmp_pkgrevno('radosgw', '17.0.0') >= 0
    if frontend_config not in SUPPORTED_FRONTENDS:
        e = ('Please provide either civetweb or beast for '
             'http-frontend configuration')
        log(e, level=ERROR)
        raise ValueError(e)
    if frontend_config == BEAST_FRONTEND:
        if not mimic_or_later:
            e = ('Use of the beast HTTP frontend requires Ceph '
                 'mimic or later.')
            log(e, level=ERROR)
            raise ValueError(e)
        if not pacific_or_later and arch() in UNSUPPORTED_BEAST_ARCHS:
            e = ('Use of the beast HTTP frontend on {} requires Ceph '
                 'pacific or later.'.format(arch()))
            log(e, level=ERROR)
            raise ValueError(e)
    if frontend_config == CIVETWEB_FRONTEND and quincy_or_later:
        e = 'Civetweb frontend is not supported after Ceph Pacific.'
        log(e, level=ERROR)
        raise ValueError(e)


class MonContext(context.CephContext):
    interfaces = ['mon']

    def __call__(self):
        if not relation_ids(self.interfaces[0]):
            return {}

        host = socket.gethostname()
        systemd_rgw = False

        mon_hosts = []
        auths = []
        fsid = None

        for rid in relation_ids(self.interfaces[0]):
            for unit in related_units(rid):
                if fsid is None:
                    fsid = relation_get('fsid', rid=rid, unit=unit)
                _auth = relation_get('auth', rid=rid, unit=unit)
                if _auth:
                    auths.append(_auth)

                ceph_pub_addr = relation_get('ceph-public-address', rid=rid,
                                             unit=unit)
                unit_priv_addr = relation_get('private-address', rid=rid,
                                              unit=unit)
                ceph_addr = ceph_pub_addr or unit_priv_addr
                ceph_addr = format_ipv6_addr(ceph_addr) or ceph_addr
                if ceph_addr:
                    mon_hosts.append(ceph_addr)
                if relation_get('rgw.{}_key'.format(host), rid=rid, unit=unit):
                    systemd_rgw = True

        if len(set(auths)) != 1:
            e = ("Inconsistent or absent auth returned by mon units. Setting "
                 "auth_supported to 'none'")
            log(e, level=WARNING)
            auth = 'none'
        else:
            auth = auths[0]

        # /etc/init.d/radosgw mandates that a dns name is used for this
        # parameter so ensure that address is resolvable
        if config('prefer-ipv6'):
            ensure_host_resolvable_v6(host)

        port = determine_api_port(utils.listen_port(), singlenode_mode=True)
        if config('prefer-ipv6'):
            port = "[::]:%s" % (port)

        http_frontend = config('http-frontend')
        if not http_frontend:
            http_frontend = resolve_http_frontend()
        else:
            validate_http_frontend(http_frontend)

        mon_hosts.sort()
        ctxt = {
            'auth_supported': auth,
            'mon_hosts': ' '.join(mon_hosts),
            'hostname': host,
            'old_auth': cmp_pkgrevno('radosgw', "0.51") < 0,
            'systemd_rgw': systemd_rgw,
            'use_syslog': str(config('use-syslog')).lower(),
            'loglevel': config('loglevel'),
            'port': port,
            'ipv6': config('prefer-ipv6'),
            'virtual_hosted_bucket_enabled':
                config('virtual-hosted-bucket-enabled'),
            # The public unit IP is only used in case the authentication is
            # *Not* keystone - in which case it is used to make sure the
            # storage endpoint returned by the built-in auth is the HAproxy
            # (since it defaults to the port the service runs on, and that is
            # not available externally). ~tribaal
            'unit_public_ip': unit_public_ip(),
            'fsid': fsid,
            'rgw_swift_versioning': config('rgw-swift-versioning-enabled'),
            'relaxed_s3_bucket_names': config('relaxed-s3-bucket-names'),
            'frontend': http_frontend,
            'behind_https_proxy': https(),
        }
        if config('virtual-hosted-bucket-enabled'):
            if config('os-public-hostname'):
                ctxt['public_hostname'] = config('os-public-hostname')
            else:
                log("When virtual_hosted_bucket_enabled is true, "
                    "os_public_hostname must have a value.", level=WARNING)

        # NOTE(dosaboy): these sections must correspond to what is supported in
        #                the config template.
        sections = ['global', 'client.radosgw.gateway']
        user_provided = CephConfContext(permitted_sections=sections)()
        user_provided = {k.replace('.', '_'): user_provided[k]
                         for k in user_provided}
        ctxt.update(user_provided)

        if self.context_complete(ctxt):
            # Multi-site zone configuration is optional,
            # so add after assessment
            zone = config('zone')
            zonegroup = config('zonegroup')
            realm = config('realm')
            log("config: zone {} zonegroup {} realm {}"
                .format(zone, zonegroup, realm), level=DEBUG)
            if zone in multisite.plain_list('zone'):
                ctxt['rgw_zone'] = zone
            if zonegroup in multisite.plain_list('zonegroup'):
                ctxt['rgw_zonegroup'] = zonegroup
            if realm in multisite.plain_list('realm'):
                ctxt['rgw_realm'] = realm
            return ctxt

        return {}

    def context_complete(self, ctxt):
        """Overridden here to ensure the context is actually complete.

        We set `key` and `auth` to None here, by default, to ensure
        that the context will always evaluate to incomplete until the
        Ceph relation has actually sent these details; otherwise,
        there is a potential race condition between the relation
        appearing and the first unit actually setting this data on the
        relation.

        :param ctxt: The current context members
        :type ctxt: Dict[str, ANY]
        :returns: True if the context is complete
        :rtype: bool
        """
        if 'fsid' not in ctxt:
            return False
        return context.OSContextGenerator.context_complete(self, ctxt)
