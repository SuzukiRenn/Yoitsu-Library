import os
from config.proxy import trusted_proxy_ips

bind = '0.0.0.0:8000'
workers = 1
threads = 2
timeout = 30
graceful_timeout = 30
accesslog = None  # Do not persist search strings, identities, or passwords.
errorlog = '-'
capture_output = False
forwarded_allow_ips = ','.join(dict.fromkeys(('127.0.0.1', '::1', *trusted_proxy_ips(os.environ.get('BOOKLIST_TRUSTED_PROXY_IPS', '')))))
secure_scheme_headers = {'X-FORWARDED-PROTO': 'https'}
