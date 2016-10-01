#!/usr/bin/env python3

"""CloudFlare Dynamic DNS updater

Use the Cloudflare API to keep records up to date with your current IP address(es).
"""

import argparse
import logging.handlers
import os
import socket
import sys

import requests.adapters

__author__ = '@maedox'


class Cloudflare:

    def __init__(self, name, log_level='INFO'):
        log_path = os.path.join(os.path.expanduser("~"), ".cf-ddns.log")
        log_file = logging.handlers.RotatingFileHandler(
            filename=log_path, maxBytes=1000000, backupCount=1, encoding="utf-8")
        log_stdout = logging.StreamHandler(sys.stdout)
        log_format = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s")
        log_file.setFormatter(log_format)
        log_stdout.setFormatter(log_format)

        log = logging.getLogger(__name__)
        log.setLevel(log_level)
        log.addHandler(log_file)
        if os.isatty:
            log.addHandler(log_stdout)
        self.log = log

        self.base_url = 'https://api.cloudflare.com/client/v4'
        email = os.getenv('CF_EMAIL')
        token = os.getenv('CF_TOKEN')
        if not email or not token:
            raise EnvironmentError('CF_EMAIL and CF_TOKEN envvars must be set.')
        self.headers = {
            'X-Auth-Email': email,
            'X-Auth-Key': token,
        }

        # Create a 'Session' to enable setting 'max_retries'
        self.api = requests.Session()
        a = requests.adapters.HTTPAdapter(max_retries=3)
        self.api.mount('https://', a)

        self.zone_id = self._get_zone_id(name)

    @staticmethod
    def _is_ipv4(ip_address):
        """Determine if IP address is IPv4
        """
        try:
            return socket.inet_pton(socket.AF_INET, ip_address)
        except socket.error:
            return False

    @staticmethod
    def _is_ipv6(ip_address):
        """Determine if IP address is IPv6
        """
        try:
            return socket.inet_pton(socket.AF_INET6, ip_address)
        except socket.error:
            return False

    def _get_record_type(self, ip_addr):
        """Determine if IP address is v4 or v6 and return correct record type
        """
        self.log.debug('%s: Getting record type ...', ip_addr)
        if self._is_ipv4(ip_addr):
            return 'A'
        elif self._is_ipv6(ip_addr):
            return 'AAAA'
        else:
            return None

    def _call_api(self, method, req_path, params, data=None):
        """Call the Cloudflare API
        """
        self.log.debug('API request: %s %s params:%s data:%s ...', method, self.base_url + req_path, params, data)
        r = self.api.request(method, self.base_url + req_path, params=params, headers=self.headers, json=data)
        if r.ok:
            self.log.debug('API response: %s', r.text)
            return r.json()
        else:
            self.log.error('Request failed: %s', r.text)
            r.raise_for_status()

    def _get_zone_id(self, name):
        """Get the zone if for a domain name
        """
        self.log.debug('%s: Getting zone id ...', name)
        params = {'name': name}
        d = self._call_api('GET', '/zones', params)
        if 'result' in d and d['result']:
            return d['result'][0]['id']
        else:
            raise ValueError('{}: No such domain'.format(name))

    def _get_existing_rec(self, name, rec_type):
        """Get any existing record
        """
        self.log.debug('%s type:%s: Checking for existing record ...', name, rec_type)
        params = {'name': name, 'type': rec_type}
        d = self._call_api('GET', '/zones/{}/dns_records'.format(self.zone_id), params)
        if 'result' in d and d['result']:
            return d['result'][0]

    def get_external_ips(self, services):
        """Get the external IP address from any available web service
        """
        self.log.debug('Getting external IP addresses ...')
        ips = set()
        for s in services:
            try:
                ip = requests.get(s).text.strip()
                self.log.debug("%s: %s", s, ip)
                if ip:
                    ips.add(ip)
            except Exception as err:
                self.log.error('%s: %s', s, err)
        return ips

    def set_record(self, name, value, proxy):
        """Add or update a DNS record
        """
        req_path = '/zones/{}/dns_records'.format(self.zone_id)

        rt = self._get_record_type(value)
        if rt:
            data = {'name': name, 'type': rt, 'content': value, 'proxied': proxy}
            rec = self._get_existing_rec(name, rt)
            if rec:
                rec_id = rec['id']
                if rec['content'] == value and rec['proxied'] == proxy:
                    self.log.debug('Record exists: %s %s %s proxied:%s id:%s', name, rt, value, proxy, rec_id)
                else:
                    data['id'] = rec_id
                    self.log.info('Updating record: %s %s %s proxied:%s id:%s ...', name, rt, value, proxy, rec_id)
                    self._call_api('PUT', req_path + '/' + rec_id, None, data)
                    self.log.info('Success: %s %s %s proxied:%s id:%s', name, rt, value, proxy, rec_id)
            else:
                self.log.info('Adding new record: %s %s %s proxied:%s ...', name, rt, value, proxy)
                self._call_api('POST', req_path, None, data)
                self.log.info('Success: %s %s %s proxied:%s', name, rt, value, proxy)
        else:
            self.log.error('Getting record type failed for %s', value)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--name", required=True, help="Fully qualified domain name to set or update.")
    parser.add_argument("--proxy", action="store_true", help="Enable Cloudflare proxy.")
    parser.add_argument("--ip-services", nargs="+", help="URL(s) to obtain external IP address from.",
                        default=("https://api.ipify.org", "https://icanhazip.com"), metavar="URL")
    parser.add_argument("--log-level", default="INFO", help="Logging level.",
                        choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"),)
    args = parser.parse_args()

    if '.' in args.name:
        domain = '.'.join(args.name.split('.')[-2:])
    else:
        raise ValueError('{} is not a valid hostname'.format(args.name))

    cf = Cloudflare(domain, args.log_level)

    for ip in cf.get_external_ips(args.ip_services):
        cf.set_record(args.name, ip, args.proxy)
