import importlib.util,unittest,json,tempfile
from unittest.mock import patch
from pathlib import Path
spec=importlib.util.spec_from_file_location('ssh_access',Path('deploy/ssh_access.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class ConfigurationTests(unittest.TestCase):
 def test_only_loopback_https_is_published(self):
  config,nginx=module.configuration('a'*40)
  self.assertEqual(set(config['services']),{'app','proxy'})
  self.assertEqual(config['services']['proxy']['ports'],['127.0.0.1:8443:8443'])
  self.assertNotIn('ports',config['services']['app'])
  self.assertTrue(config['networks']['edge_backend']['internal'])
  self.assertTrue(any(not config['networks'][name].get('internal',False) for name in config['services']['proxy']['networks']))
  self.assertIn('proxy_set_header Host $http_host;',nginx)
  self.assertIn('image/svg+xml svg;',nginx)
  self.assertIn('image/png png;',nginx)
  icon_location=nginx.split('location = /static/favicon.svg {',1)[1].split('}',1)[0]
  self.assertIn('default_type image/svg+xml;',icon_location)
  self.assertIn('etag off;',icon_location)
  self.assertIn('if_modified_since off;',icon_location)
  self.assertIn('Cache-Control "no-store"',icon_location)
  self.assertIn('${BOOKLIST_RELEASE}',json.dumps(config))
  self.assertEqual(config['services']['app']['environment']['BOOKLIST_TRUSTED_PROXY_IPS'],'172.30.29.2')
  self.assertEqual(config['services']['app']['networks']['edge_backend']['ipv4_address'],'172.30.29.3')
  self.assertNotEqual(config['services']['app']['networks']['edge_backend']['ipv4_address'],config['services']['proxy']['networks']['edge_backend']['ipv4_address'])
 def test_invalid_release_is_refused(self):
  with self.assertRaises(ValueError):module.configuration('invalid')
 def test_public_mode_keeps_private_probe_and_exposes_only_web_ports(self):
  config,nginx=module.configuration('a'*40,public=True)
  self.assertEqual(config['services']['proxy']['ports'],['127.0.0.1:8443:8443','0.0.0.0:80:8080','0.0.0.0:443:8443'])
  self.assertNotIn('ports',config['services']['app'])
  self.assertIn('return 308 https://sharedbooklist.xyz$request_uri;',nginx)
  self.assertIn('if ($host != sharedbooklist.xyz) { return 444; }',nginx)
  self.assertIn('proxy_set_header X-Forwarded-For $remote_addr;',nginx)
 def test_public_mode_requires_exact_approved_marker_and_persists_across_releases(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(module,'DIRECTORY',Path(folder)):
   self.assertFalse(module.public_enabled())
   marker=Path(folder)/'public-domain.json'
   marker.write_text(json.dumps({'domain':module.DOMAIN,'icp_record':'鲁ICP备2026066257号-1'}),encoding='utf-8')
   for release in ['a'*40,'b'*40]:
    self.assertTrue(module.public_enabled())
    config,_=module.configuration(release,module.public_enabled())
    self.assertIn('0.0.0.0:443:8443',config['services']['proxy']['ports'])
   marker.write_text(json.dumps({'domain':'other.example','icp_record':'鲁ICP备2026066257号-1'}),encoding='utf-8')
   with self.assertRaises(ValueError):module.public_enabled()
if __name__=='__main__':unittest.main()
