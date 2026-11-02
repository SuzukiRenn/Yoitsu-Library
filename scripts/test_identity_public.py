import copy
import unittest
from deploy.identity_public import DOMAIN, extend
from scripts.prepare_https import render
import json


class IdentityPublicTests(unittest.TestCase):
    def fixture(self):
        files = render('sharedbooklist.xyz', 'a' * 40)
        return json.loads(files['compose.https.json']), files['nginx.https.conf']

    def test_disabled_does_not_change_existing_entry(self):
        config, nginx = self.fixture()
        expected = copy.deepcopy(config)
        self.assertEqual(extend(config, nginx, None), (expected, nginx))

    def test_challenge_does_not_publish_identity_https_early(self):
        config, nginx = extend(*self.fixture(), 'challenge')
        self.assertIn('server_name ' + DOMAIN, nginx)
        self.assertNotIn('identity_edge', config['networks'])
        self.assertNotIn('/etc/nginx/identity-tls/', nginx)
        self.assertIn('/srv/acme', nginx)

    def test_enabled_keeps_private_apps_and_separates_static_and_tls(self):
        config, nginx = extend(*self.fixture(), 'enabled')
        proxy = config['services']['proxy']
        self.assertEqual(proxy['networks']['identity_edge']['ipv4_address'], '172.30.30.4')
        self.assertEqual(config['networks']['identity_edge']['name'], 'shared-booklist-identity_edge')
        self.assertNotIn('ports', config['services']['app'])
        self.assertEqual(nginx.count('listen 8443 ssl default_server;'), 1)
        self.assertIn('proxy_pass http://172.30.30.3:8000;', nginx)
        self.assertIn('proxy_set_header Host ' + DOMAIN + ';', nginx)
        self.assertIn('proxy_set_header X-Forwarded-For $remote_addr;', nginx)
        self.assertIn('/srv/identity-static/public-current/', nginx)
        self.assertIn('/etc/nginx/identity-tls/fullchain.pem', nginx)
        self.assertIn('proxy_pass http://172.30.29.3:8000;', nginx)
        self.assertNotIn('proxy_pass http://app:8000;', nginx)


if __name__ == '__main__':
    unittest.main()
