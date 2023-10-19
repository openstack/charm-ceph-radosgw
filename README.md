# Overview

[Ceph][ceph-upstream] is a unified, distributed storage system designed for
excellent performance, reliability, and scalability.

The ceph-radosgw charm deploys the RADOS Gateway, a S3 and Swift compatible
HTTP gateway. The deployment is done within the context of an existing Ceph
cluster.

# Usage

## Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

#### `pool-type`

The `pool-type` option dictates the storage pool type. See section 'Ceph pool
type' for more information.

#### `source`

The `source` option states the software sources. A common value is an OpenStack
UCA release (e.g. 'cloud:xenial-queens' or 'cloud:bionic-ussuri'). See [Ceph
and the UCA][cloud-archive-ceph]. The underlying host's existing apt sources
will be used if this option is not specified (this behaviour can be explicitly
chosen by using the value of 'distro').

## Ceph pool type

Ceph storage pools can be configured to ensure data resiliency either through
replication or by erasure coding. This charm supports both types via the
`pool-type` configuration option, which can take on the values of 'replicated'
and 'erasure-coded'. The default value is 'replicated'.

For this charm, the pool type will be associated with Object storage.

> **Note**: Erasure-coded pools are supported starting with Ceph Luminous.

### Replicated pools

Replicated pools use a simple replication strategy in which each written object
is copied, in full, to multiple OSDs within the cluster.

The `ceph-osd-replication-count` option sets the replica count for any object
stored within the rgw pools. Increasing this value increases data resilience at
the cost of consuming more real storage in the Ceph cluster. The default value
is '3'.

> **Important**: The `ceph-osd-replication-count` option must be set prior to
  adding the relation to the ceph-mon application. Otherwise, the pool's
  configuration will need to be set by interfacing with the cluster directly.

### Erasure coded pools

Erasure coded pools use a technique that allows for the same resiliency as
replicated pools, yet reduces the amount of space required. Written data is
split into data chunks and error correction chunks, which are both distributed
throughout the cluster.

> **Note**: Erasure coded pools require more memory and CPU cycles than
  replicated pools do.

When using erasure coded pools for Object storage multiple pools will be
created: one erasure coded pool ('rgw.buckets.data' for storing actual RGW
data) and several replicated pools (for storing RGW omap metadata). The
`ceph-osd-replication-count` configuration option only applies to the metadata
(replicated) pools.

Erasure coded pools can be configured via options whose names begin with the
`ec-` prefix.

> **Important**: It is strongly recommended to tailor the `ec-profile-k` and
  `ec-profile-m` options to the needs of the given environment. These latter
  options have default values of '1' and '2' respectively, which result in the
  same space requirements as those of a replicated pool.

See [Ceph Erasure Coding][cdg-ceph-erasure-coding] in the [OpenStack Charms
Deployment Guide][cdg] for more information.

## Ceph BlueStore compression

This charm supports [BlueStore inline compression][ceph-bluestore-compression]
for its associated Ceph storage pool(s). The feature is enabled by assigning a
compression mode via the `bluestore-compression-mode` configuration option. The
default behaviour is to disable compression.

The efficiency of compression depends heavily on what type of data is stored
in the pool and the charm provides a set of configuration options to fine tune
the compression behaviour.

> **Note**: BlueStore compression is supported starting with Ceph Mimic.

## Deployment

Ceph RADOS Gateway is often containerised. Here a single unit is deployed to a
new container on machine '1' within an existing Ceph cluster:

    juju deploy --to lxd:1 ceph-radosgw
    juju add-relation ceph-radosgw:mon ceph-mon:radosgw

If the RADOS Gateway is being integrated into OpenStack then a relation to the
keystone application is needed:

    juju add-relation ceph-radosgw:identity-service keystone:identity-service

Expose the service:

    juju expose ceph-radosgw

> **Note**: The `expose` command is only required if the backing cloud blocks
  traffic by default. In general, MAAS is the only cloud type that does not
  employ firewalling.

The Gateway can be accessed over port 80 (as per `juju status ceph-radosgw`
output).

## Multi-site replication

The charm supports native replication between multiple RADOS Gateway
deployments. This is documented under [Ceph RADOS Gateway multisite
replication][cdg-ceph-radosgw-multisite] in the [OpenStack Charms Deployment
Guide][cdg].

## Tenant namespacing

By default, Ceph RADOS Gateway puts all tenant buckets into the same global
namespace, disallowing multiple tenants to have buckets with the same name.
Tenant namespacing can be enabled in this charm by deploying with configuration
like:

    ceph-radosgw:
      charm: cs:ceph-radosgw
      num_units: 1
      options:
        namespace-tenants: True

Enabling tenant namespacing will place all tenant buckets into their own
namespace under their tenant id, as well as adding the tenant's ID parameter to
the Keystone endpoint registration to allow seamless integration with OpenStack.
Tenant namespacing cannot be toggled on in an existing installation as it will
remove tenant access to existing buckets. Toggling this option on an already
deployed RADOS Gateway will have no effect.

## Access

For security reasons the charm is not designed to administer the Ceph cluster.
A user (e.g. 'ubuntu') for the Ceph Object Gateway service will need to be
created manually:

    juju ssh ceph-mon/0 'sudo radosgw-admin user create \
       --uid="ubuntu" --display-name="Charmed Ceph"'

## Keystone integration (Swift)

Ceph RGW supports Keystone authentication of Swift requests. This is enabled
by adding a relation to an existing keystone application:

    juju add-relation ceph-radosgw:identity-service keystone:identity-service

## High availability

When more than one unit is deployed with the [hacluster][hacluster-charm]
application the charm will bring up an HA active/active cluster.

There are two mutually exclusive high availability options: using virtual IP(s)
or DNS. In both cases the hacluster subordinate charm is used to provide the
Corosync and Pacemaker backend HA functionality.

See [OpenStack high availability][cdg-ha-apps] in the [OpenStack Charms
Deployment Guide][cdg] for details.

## Network spaces

This charm supports the use of Juju [network spaces][juju-docs-spaces] (Juju
`v.2.0`). This feature optionally allows specific types of the application's
network traffic to be bound to subnets that the underlying hardware is
connected to.

> **Note**: Spaces must be configured in the backing cloud prior to deployment.

API endpoints can be bound to distinct network spaces supporting the network
separation of public, internal and admin endpoints.

For example, providing that spaces 'public-space', 'internal-space', and
'admin-space' exist, the deploy command above could look like this:

    juju deploy ceph-radosgw \
       --bind "public=public-space internal=internal-space admin=admin-space"

Alternatively, configuration can be provided as part of a bundle:

```yaml
    ceph-radosgw:
      charm: cs:ceph-radosgw
      num_units: 1
      bindings:
        public: public-space
        internal: internal-space
        admin: admin-space
```

> **Note**: Existing ceph-radosgw units configured with the `os-admin-network`,
  `os-internal-network`, `os-public-network`, `os-public-hostname`,
  `os-internal-hostname`, or `os-admin-hostname` options will continue to
  honour them. Furthermore, these options override any space bindings, if set.

## Actions

This section lists Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions ceph-radosgw`. If the charm is
not deployed then see file `actions.yaml`.

* `pause`
* `promote`
* `readonly`
* `readwrite`
* `resume`
* `tidydefaults`
* `enable-buckets-sync`
* `disable-buckets-sync`
* `reset-buckets-sync`

# Documentation

The OpenStack Charms project maintains two documentation guides:

* [OpenStack Charm Guide][cg]: for project information, including development
  and support notes
* [OpenStack Charms Deployment Guide][cdg]: for charm usage information

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ceph-radosgw].

<!-- LINKS -->

[juju-docs-actions]: https://jaas.ai/docs/actions
[ceph-upstream]: https://ceph.io
[hacluster-charm]: https://jaas.ai/hacluster
[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide
[cdg-ha-apps]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-ha.html#ha-applications
[cloud-archive-ceph]: https://wiki.ubuntu.com/OpenStack/CloudArchive#Ceph_and_the_UCA
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[cdg-ceph-erasure-coding]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-erasure-coding.html
[lp-bugs-charm-ceph-radosgw]: https://bugs.launchpad.net/charm-ceph-radosgw/+filebug
[juju-docs-spaces]: https://jaas.ai/docs/spaces
[cdg-ceph-radosgw-multisite]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-rgw-multisite.html
[ceph-bluestore-compression]: https://docs.ceph.com/en/latest/rados/configuration/bluestore-config-ref/#inline-compression
