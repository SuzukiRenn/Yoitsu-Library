import importlib.util,unittest,json
from pathlib import Path
spec=importlib.util.spec_from_file_location('ssh_access',Path('deploy/ssh_access.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class ConfigurationTests(unittest.TestCase):
 def test_only_loopback_https_is_published(self):
  config,nginx=module.configuration('a'*40)
  self.assertEqual(set(config['services']),{'app','proxy'})
  self.assertEqual(config['services']['proxy']['ports'],['127.0.0.1:8443:8443'])
  self.assertNotIn('ports',config['services']['app'])
  self.assertTrue(config['networks']['edge_backend']['internal'])
  self.assertIn('proxy_set_header Host $http_host;',nginx)
  self.assertIn('${BOOKLIST_RELEASE}',json.dumps(config))
  self.assertEqual(config['services']['app']['environment']['BOOKLIST_TRUSTED_PROXY_IPS'],'172.30.29.2')
  self.assertEqual(config['services']['app']['networks']['edge_backend']['ipv4_address'],'172.30.29.3')
  self.assertNotEqual(config['services']['app']['networks']['edge_backend']['ipv4_address'],config['services']['proxy']['networks']['edge_backend']['ipv4_address'])
 def test_invalid_release_is_refused(self):
  with self.assertRaises(ValueError):module.configuration('invalid')
if __name__=='__main__':unittest.main()
