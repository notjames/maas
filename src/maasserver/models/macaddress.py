# Copyright 2012-2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""MACAddress model and friends."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = [
    'MACAddress',
    ]


import re

from django.db.models import (
    ForeignKey,
    ManyToManyField,
    )
from maasserver import DefaultMeta
from maasserver.enum import IPADDRESS_TYPE
from maasserver.exceptions import (
    StaticIPAddressOutOfRange,
    StaticIPAddressTypeClash,
    )
from maasserver.fields import (
    MAC,
    MACAddressField,
    )
from maasserver.models.cleansave import CleanSave
from maasserver.models.managers import BulkManager
from maasserver.models.nodegroupinterface import NodeGroupInterface
from maasserver.models.timestampedmodel import TimestampedModel
from netaddr import IPAddress
from provisioningserver.logger import get_maas_logger


mac_re = re.compile(r'^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$')
maaslog = get_maas_logger("macaddress")


def find_cluster_interface_responsible_for_ip(cluster_interfaces, ip_address):
    """Pick the cluster interface whose network contains `ip_address`.

    :param cluster_interfaces: An iterable of `NodeGroupInterface`.
    :param ip_address: An `IPAddress`.
    :return: The cluster interface from `cluster_interfaces` whose subnet
        contains `ip_address`, or `None`.
    """
    for interface in cluster_interfaces:
        if ip_address in interface.network:
            return interface
    return None


class MACAddress(CleanSave, TimestampedModel):
    """A `MACAddress` represents a `MAC address`_ attached to a :class:`Node`.

    :ivar mac_address: The MAC address.
    :ivar node: The :class:`Node` related to this `MACAddress`.
    :ivar networks: The networks related to this `MACAddress`.

    .. _MAC address: http://en.wikipedia.org/wiki/MAC_address
    """
    mac_address = MACAddressField(unique=True)
    node = ForeignKey('Node', editable=False)

    networks = ManyToManyField('maasserver.Network', blank=True)

    ip_addresses = ManyToManyField(
        'maasserver.StaticIPAddress',
        through='maasserver.MACStaticIPAddressLink', blank=True)

    # Will be set only once we know on which cluster interface this MAC
    # is connected, normally after the first DHCPLease appears.
    cluster_interface = ForeignKey(
        'NodeGroupInterface', editable=False, blank=True, null=True,
        default=None)

    # future columns: tags, nic_name, metadata, bonding info

    objects = BulkManager()

    class Meta(DefaultMeta):
        verbose_name = "MAC address"
        verbose_name_plural = "MAC addresses"
        ordering = ('created', )

    def __unicode__(self):
        address = self.mac_address
        if isinstance(address, MAC):
            address = address.get_raw()
        if isinstance(address, bytes):
            address = address.decode('utf-8')
        return address

    def unique_error_message(self, model_class, unique_check):
        if unique_check == ('mac_address',):
                return "This MAC address is already registered."
        return super(
            MACAddress, self).unique_error_message(model_class, unique_check)

    def get_networks(self):
        """Return networks to which this MAC is connected, sorted by name."""
        return self.networks.all().order_by('name')

    def get_cluster_interfaces(self):
        """Return all cluster interfaces to which this MAC connects.

        This is at least its `cluster_interface`, if it is set.  But if so,
        there may also be an IPv6 cluster interface attached to the same
        network interface.
        """
        # XXX jtv 2014-08-18 bug=1358130: cluster_interface should probably be
        # an m:n relationship.  Andres came up with a simpler scheme for the
        # short term: "for IPv6, use whatever network interface on the cluster
        # also manages the node's IPv4 address."
        if self.cluster_interface is None:
            # No known cluster interface.  Nothing we can do.
            maaslog.error(
                "%s: Tried to allocate an IP to MAC %s but its cluster "
                "interface is not known", self.node.hostname, self)
            return []
        else:
            return NodeGroupInterface.objects.filter(
                nodegroup=self.cluster_interface.nodegroup,
                interface=self.cluster_interface.interface)

    def _map_allocated_addresses(self, cluster_interfaces):
        """Gather already allocated static IP addresses for this MAC.

        :param cluster_interfaces: Iterable of `NodeGroupInterface` where we
            may have allocated addresses.
        :return: A dict mapping each of the cluster interfaces to the MAC's
            `StaticIPAddress` on that interface (which may be `None`).
        """
        allocations = {
            interface: None
            for interface in cluster_interfaces
            }
        for sip in self.ip_addresses.all():
            interface = find_cluster_interface_responsible_for_ip(
                cluster_interfaces, IPAddress(sip.ip))
            if interface is not None:
                allocations[interface] = sip
        return allocations

    def _allocate_static_address(self, cluster_interface, alloc_type,
                                 requested_address=None):
        """Allocate a `StaticIPAddress` for this MAC."""
        # Avoid circular imports.
        from maasserver.models import (
            MACStaticIPAddressLink,
            StaticIPAddress,
            )

        new_sip = StaticIPAddress.objects.allocate_new(
            cluster_interface.static_ip_range_low,
            cluster_interface.static_ip_range_high,
            alloc_type, requested_address=requested_address)
        MACStaticIPAddressLink(mac_address=self, ip_address=new_sip).save()
        return new_sip

    def claim_static_ips(self, alloc_type=IPADDRESS_TYPE.AUTO,
                         requested_address=None):
        """Assign static IP addresses to this MAC.

        Allocates one address per managed cluster interface connected to this
        MAC.  Typically this will be either just one IPv4 address, or an IPv4
        address and an IPv6 address.

        It is the caller's responsibility to create a celery Task that will
        write the dhcp host.  It is not done here because celery doesn't
        guarantee job ordering, and if the host entry is written after
        the host boots it is too late.

        :param alloc_type: See :class:`StaticIPAddress`.alloc_type.
            This parameter musn't be IPADDRESS_TYPE.USER_RESERVED.
        :param requested_address: Optional IP address to claim.  Must be in
            the range defined on some cluster interface to which this
            MACAddress is related.  If given, no allocations will be made on
            any other cluster interfaces the MAC may be connected to.
        :return: A list of :class:`StaticIPAddress`.  Returns empty if
            the cluster_interface is not yet known, or the
            static_ip_range_low/high values values are not set on the
            cluster_interface.  If an IP address was already allocated, the
            function will return it rather than allocate a new one.
        :raises: StaticIPAddressExhaustion if there are not enough IPs left.
        :raises: StaticIPAddressTypeClash if an IP already exists with a
            different type.
        :raises: StaticIPAddressOutOfRange if the requested_address is not in
            the cluster interface's defined range.
        :raises: StaticIPAddressUnavailable if the requested_address is already
            allocated.
        """
        # This method depends on a database isolation level of SERIALIZABLE
        # (or perhaps REPEATABLE READ) to avoid race conditions.

        # Every IP address we allocate is managed by one cluster interface.
        # We're only interested in cluster interfaces with a static range.
        # The check for a static range is deliberately kept vague; Django uses
        # different representations for "none" values in IP addresses.
        cluster_interfaces = [
            interface
            for interface in self.get_cluster_interfaces()
            if interface.get_static_ip_range()
            ]
        if len(cluster_interfaces) == 0:
            # There were cluster interfaces, but none of them had a static
            # range.  Can't allocate anything.
            return []

        if requested_address is not None:
            # A specific IP address was requested.  We restrict our attention
            # to the cluster interface that is responsible for that address.
            cluster_interface = find_cluster_interface_responsible_for_ip(
                cluster_interfaces, IPAddress(requested_address))
            if cluster_interface is None:
                raise StaticIPAddressOutOfRange(
                    "Requested IP address %s is not in a subnet managed by "
                    "any cluster interface." % requested_address)
            cluster_interfaces = [cluster_interface]

        allocations = self._map_allocated_addresses(cluster_interfaces)

        if None not in allocations.values():
            # We already have a full complement of static IP addresses
            # allocated.  Check for a clash.
            types = [sip.alloc_type for sip in allocations.values()]
            if alloc_type not in types:
                # None of the prior allocations are for the same type that's
                # being requested now.  This is a complete clash.
                raise StaticIPAddressTypeClash(
                    "MAC address %s already has IP adresses of different "
                    "types than the ones requested." % self)

        # Allocate IP addresses on all relevant cluster interfaces where this
        # MAC does not have any address allocated yet.
        for interface in cluster_interfaces:
            if allocations[interface] is None:
                # No IP address yet on this cluster interface.  Get one.
                allocations[interface] = self._allocate_static_address(
                    interface, alloc_type, requested_address)

        # We now have a static IP allocated to each of our cluster interfaces.
        # Ignore the clashes.  Return the ones that have the right type: those
        # are either matching pre-existing allocations or fresh ones.
        return [
            sip
            for sip in allocations.values()
            if sip.alloc_type == alloc_type
            ]
