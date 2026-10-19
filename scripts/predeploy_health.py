"""Container-local HTTP health check; this does not stand in for a public TLS check."""
import json
import urllib.request


def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request('http://127.0.0.1:8000/health/', headers={'Host':'localhost', 'X-Forwarded-Proto':'https'})
    with opener.open(request, timeout=8) as response:
        result = json.load(response)
        if response.status != 200 or result.get('status') != 'ok':
            raise RuntimeError('health_not_ready')
    print('private_health_ok')


if __name__ == '__main__':
    main()
