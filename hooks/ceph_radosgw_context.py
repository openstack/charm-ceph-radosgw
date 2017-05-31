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

from charmhelpers.contrib.openstack import context
from charmhelpers.contrib.hahelpers.cluster import (
    determine_api_port,
    determine_apache_port,
)
from charmhelpers.core.host import cmp_pkgrevno
from charmhelpers.core.hookenv import (
    DEBUG,
    WARNING,
    config,
    log,
    related_units,
    relation_get,
    relation_ids,
    unit_public_ip,
)
from charmhelpers.contrib.network.ip import (
    format_ipv6_addr,
    get_ipv6_addr,
)
from charmhelpers.contrib.storage.linux.ceph import CephConfContext


class HAProxyContext(context.HAProxyContext):

    def __call__(self):
        ctxt = super(HAProxyContext, self).__call__()
        port = config('port')

        # Apache ports
        a_cephradosgw_api = determine_apache_port(port, singlenode_mode=True)

        port_mapping = {
            'cephradosgw-server': [port, a_cephradosgw_api]
        }

        ctxt['cephradosgw_bind_port'] = determine_api_port(
            port,
            singlenode_mode=True,
        )

        # for haproxy.conf
        ctxt['service_ports'] = port_mapping
        return ctxt


class IdentityServiceContext(context.IdentityServiceContext):
    interfaces = ['identity-service']

    def __call__(self):
        ctxt = super(IdentityServiceContext, self).__call__()
        if not ctxt:
            return

        ctxt['admin_token'] = None
        for relid in relation_ids('identity-service'):
            for unit in related_units(relid):
                if not ctxt.get('admin_token'):
                    ctxt['admin_token'] = \
                        relation_get('admin_token', unit, relid)

        if cmp_pkgrevno('radosgw', "10.2.0") >= 0:
            ctxt['auth_keystone_v3_supported'] = True
        ctxt['auth_type'] = 'keystone'
        ctxt['user_roles'] = config('operator-roles')
        ctxt['cache_size'] = config('cache-size')
        ctxt['revocation_check_interval'] = config('revocation-check-interval')
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
                key = "^%s\s+" % (host_addr)
                if re.search(key, line):
                    break
            else:
                fd.write("%s\t%s\n" % (host_addr, hostname))

            os.rename(tmp_hosts, '/etc/hosts')
    finally:
        shutil.rmtree(dtmp)


class MonContext(context.CephContext):
    interfaces = ['ceph-radosgw']

    def __call__(self):
        if not relation_ids('mon'):
            return {}
        mon_hosts = []
        auths = []

        for rid in relation_ids('mon'):
            for unit in related_units(rid):
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

        if len(set(auths)) != 1:
            e = ("Inconsistent or absent auth returned by mon units. Setting "
                 "auth_supported to 'none'")
            log(e, level=WARNING)
            auth = 'none'
        else:
            auth = auths[0]

        # /etc/init.d/radosgw mandates that a dns name is used for this
        # parameter so ensure that address is resolvable
        host = socket.gethostname()
        if config('prefer-ipv6'):
            ensure_host_resolvable_v6(host)

        port = determine_apache_port(config('port'), singlenode_mode=True)
        if config('prefer-ipv6'):
            port = "[::]:%s" % (port)

        mon_hosts.sort()
        ctxt = {
            'auth_supported': auth,
            'mon_hosts': ' '.join(mon_hosts),
            'hostname': host,
            'old_auth': cmp_pkgrevno('radosgw', "0.51") < 0,
            'use_syslog': str(config('use-syslog')).lower(),
            'loglevel': config('loglevel'),
            'port': port,
            'ipv6': config('prefer-ipv6'),
            # The public unit IP is only used in case the authentication is
            # *Not* keystone - in which case it is used to make sure the
            # storage endpoint returned by the built-in auth is the HAproxy
            # (since it defaults to the port the service runs on, and that is
            # not available externally). ~tribaal
            'unit_public_ip': unit_public_ip(),
        }

        certs_path = '/var/lib/ceph/nss'
        paths = [os.path.join(certs_path, 'ca.pem'),
                 os.path.join(certs_path, 'signing_certificate.pem')]
        if all([os.path.isfile(p) for p in paths]):
            ctxt['cms'] = True

        # NOTE(dosaboy): these sections must correspond to what is supported in
        #                the config template.
        sections = ['global', 'client.radosgw.gateway']
        user_provided = CephConfContext(permitted_sections=sections)()
        user_provided = {k.replace('.', '_'): user_provided[k]
                         for k in user_provided}
        ctxt.update(user_provided)

        if self.context_complete(ctxt):
            return ctxt

        return {}
