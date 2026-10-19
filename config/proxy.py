"""Exact proxy addresses shared by Django and Gunicorn; wildcards/CIDRs are rejected."""
from ipaddress import ip_address


def canonical_ip(value):
    if not isinstance(value, str) or '%' in value:
        raise ValueError('Invalid IP address')
    address = ip_address(value.strip())
    return str(getattr(address, 'ipv4_mapped', None) or address)


def trusted_proxy_ips(value):
    if not value.strip():
        return ()
    return tuple(dict.fromkeys(canonical_ip(part) for part in value.split(',')))
