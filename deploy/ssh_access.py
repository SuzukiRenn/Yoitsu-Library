"""Loopback-only HTTPS entry for SSH forwarding; never open public web ports."""
import ipaddress,json,os,re,shutil,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.prepare_https import render
DOMAIN='sharedbooklist.xyz'
DIRECTORY=Path('/etc/shared-booklist/ssh-access')
SUBNET='172.30.29.0/28'

def run(*args):
    return subprocess.check_output(args,text=True).strip()

def configuration(release):
    rendered=render(DOMAIN,release)
    overlay=json.loads(rendered['compose.https.json'])
    overlay['services']['app']['networks']['edge_backend']={'ipv4_address':'172.30.29.3'}
    overlay['services'].pop('certbot')
    proxy=overlay['services']['proxy']
    proxy['ports']=['127.0.0.1:8443:8443']
    proxy['volumes']=[str(DIRECTORY)+'/nginx.conf:/etc/nginx/nginx.conf:ro',str(DIRECTORY)+'/tls:/etc/nginx/tls:ro','/var/lib/shared-booklist/predeploy/static/${BOOKLIST_RELEASE}:/srv/static:ro']
    nginx=rendered['nginx.https.conf'].replace('proxy_set_header Host '+DOMAIN+';','proxy_set_header Host $http_host;')
    return overlay,nginx

def compose(release,overlay=True):
    command=['docker','compose','-p','shared-booklist-predeploy','--env-file','/etc/shared-booklist/predeploy.env','-f','/opt/shared-booklist/releases/'+release+'/deploy/compose.yaml']
    if overlay:command+=['-f',str(DIRECTORY/'compose.json')]
    return command

def prepare(release):
    assert re.fullmatch('[a-f0-9]{40}',release)
    assert int(run('docker','version','--format','{{.Server.Version}}').split('.')[0])>=28,'Docker 28+ required for loopback publication'
    subnet=ipaddress.ip_network(SUBNET)
    identifiers=run('docker','network','ls','-q').split()
    owned=False
    if identifiers:
        for network in json.loads(run('docker','network','inspect',*identifiers)):
            for item in network['IPAM'].get('Config',[]) or []:
                if not item.get('Subnet'):continue
                existing=ipaddress.ip_network(item['Subnet'])
                if existing.version==4 and subnet.overlaps(existing):
                    assert network['Name']=='shared-booklist-predeploy_edge_backend' and str(existing)==SUBNET and network['Internal'],'Subnet collision'
                    owned=True
    for route in json.loads(run('ip','-j','-4','route')):
        destination=route.get('dst','default')
        if destination=='default':continue
        network=ipaddress.ip_network(destination,strict=False)
        assert not subnet.overlaps(network) or (owned and str(network)==SUBNET),'Host route collision'
    for path in [DIRECTORY,*DIRECTORY.parents]:assert not path.is_symlink(),'Linked configuration path'
    DIRECTORY.mkdir(mode=0o750,exist_ok=True)
    os.chown(DIRECTORY,0,10001);DIRECTORY.chmod(0o750)
    tls=DIRECTORY/'tls';tls.mkdir(mode=0o750,exist_ok=True);os.chown(tls,0,10001);tls.chmod(0o750)
    cert=Path('/etc/shared-booklist/acme-dns-production/live/sharedbooklist-xyz-production')
    run('openssl','x509','-in',str(cert/'fullchain.pem'),'-noout','-checkhost',DOMAIN)
    run('openssl','x509','-in',str(cert/'fullchain.pem'),'-noout','-checkend','1209600')
    for name in ['fullchain.pem','privkey.pem']:
        target=tls/name;assert not target.is_symlink();shutil.copyfile(cert/name,target);os.chown(target,0,10001);target.chmod(0o640)
    overlay,nginx=configuration(release)
    for name,content in [('compose.json',json.dumps(overlay,indent=2)),('nginx.conf',nginx)]:
        target=DIRECTORY/name;assert not target.is_symlink();target.write_text(content,encoding='utf-8');os.chown(target,0,10001);target.chmod(0o640)
    static=Path('/var/lib/shared-booklist/predeploy/static')/release
    assert not static.is_symlink()
    static.mkdir(parents=True,exist_ok=True)
    container=run('docker','create','--network','none','shared-booklist:'+release)
    try:subprocess.run(['docker','cp',container+':/app/staticfiles/.',str(static)],check=True)
    finally:run('docker','rm',container)
    for target in [static,*static.rglob('*')]:
        assert not target.is_symlink();target.chmod(0o755 if target.is_dir() else 0o644)
    os.environ['BOOKLIST_RELEASE']=release
    subprocess.run(compose(release)+['config','--quiet'],check=True)
    # Syntax and matching certificate/key are checked before touching the application.
    subprocess.run(['docker','run','--rm','--network','none','--read-only','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges:true','--tmpfs','/tmp:size=32m','--add-host','app:127.0.0.1','-v',str(DIRECTORY/'nginx.conf')+':/etc/nginx/nginx.conf:ro','-v',str(tls)+':/etc/nginx/tls:ro','--entrypoint','nginx',overlay['services']['proxy']['image'],'-t'],check=True)

def verify(release):
    output=run('curl','--noproxy','*','--fail','--silent','--show-error','--resolve',DOMAIN+':8443:127.0.0.1','https://'+DOMAIN+':8443/health/')
    assert json.loads(output)['status']=='ok'
    for asset,kind in [('catalog.css','text/css'),('public.mjs','text/javascript')]:
        output=run('curl','--noproxy','*','--fail','--silent','--show-error','--resolve',DOMAIN+':8443:127.0.0.1','-o','/dev/null','-w','%{http_code} %{content_type}','https://'+DOMAIN+':8443/static/'+asset)
        assert output=='200 '+kind,output
    ids=run(*compose(release),'ps','-q','proxy').split();assert len(ids)==1
    info=json.loads(run('docker','inspect',ids[0]))[0]
    assert info['HostConfig']['PortBindings']=={'8443/tcp':[{'HostIp':'127.0.0.1','HostPort':'8443'}]}
    assert not json.loads(run('docker','inspect',run(*compose(release),'ps','-q','app')))[0]['HostConfig']['PortBindings']
    receipt={'success':True,'release':release,'bind':'127.0.0.1:8443','public_ports_opened':False,'tls_system_ca_verified':True,'health_and_static_mime_verified':True}
    (DIRECTORY/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt

def activate(release):
    was_active=(DIRECTORY/'active').is_file()
    prepare(release)
    try:
        subprocess.run(compose(release)+['up','-d','--no-build','--wait','--wait-timeout','120','app','proxy'],check=True)
        time.sleep(1)
        receipt=verify(release)
        (DIRECTORY/'active').write_text('loopback HTTPS for SSH only\n')
        print('SSH_ENTRY_SUCCESS '+json.dumps(receipt),flush=True)
    except BaseException:
        if not was_active:
            subprocess.run(compose(release)+['stop','proxy'],check=False)
            subprocess.run(compose(release,False)+['up','-d','--no-build','--wait','--wait-timeout','120','app'],check=True)
        raise

if __name__=='__main__':
    assert os.geteuid()==0
    {'prepare':prepare,'activate':activate,'verify':lambda r:print(json.dumps(verify(r)))}[sys.argv[1]](sys.argv[2])
