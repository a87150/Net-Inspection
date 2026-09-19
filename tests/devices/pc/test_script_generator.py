import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
from unittest import TestCase
from net.scripts.generator import generate_pc_script
def config_of(script):
    return json.loads(base64.b64decode(script.content.split('# PC_CONFIG: ', 1)[1].splitlines()[0]))
class CaptureServer:
    def __init__(self, statuses):
        self.statuses, self.requests = list(statuses), []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.requests.append((self.path, self.headers.get('Authorization'), self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(owner.statuses.pop(0) if owner.statuses else 500); self.end_headers()
            def log_message(self, *args): pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    @property
    def endpoint(self): return f'http://127.0.0.1:{self.server.server_port}/api/pc/logs/'
    def __enter__(self): self.thread.start();return self
    def __exit__(self,*args): self.server.shutdown();self.thread.join()
class PcScriptGeneratorTests(TestCase):
    def setUp(self):
        saved=SimpleNamespace(adding=False)
        self.profile=SimpleNamespace(_state=saved,pk=1,kms_servers=['kms.example.invalid'])
        self.source=SimpleNamespace(_state=saved,pk=1,endpoint_url='https://monitor.example.invalid/api/pc/logs/',get_token=lambda:'test-token-with-at-least-thirty-two-characters')
    def test_saved_api_config(self):
        for platform in ('windows','macos'):
            script=generate_pc_script(self.profile,self.source,platform)
            self.assertEqual(config_of(script)['endpoint_url'],self.source.endpoint_url)
            self.assertEqual(config_of(script)['token'],self.source.get_token())
            self.assertIn('latest.json',script.content)
        self.assertEqual(config_of(generate_pc_script(self.profile,self.source,'windows'))['kms_servers'],['kms.example.invalid'])
    def test_rejects_invalid_api_config(self):
        for source in (SimpleNamespace(_state=SimpleNamespace(adding=True),pk=None,endpoint_url='https://ok.test/',get_token=lambda:'x'*32),SimpleNamespace(_state=SimpleNamespace(adding=False),pk=1,endpoint_url='bad',get_token=lambda:'x'*32),SimpleNamespace(_state=SimpleNamespace(adding=False),pk=1,endpoint_url='https://ok.test/',get_token=lambda:''),):
            with self.assertRaises(ValueError): generate_pc_script(self.profile,source,'windows')
    def test_windows_posts_exact_local_bytes_and_retries(self):
        functions=generate_pc_script(self.profile,self.source,'windows').content.split('# Collection entry point',1)[0]
        with CaptureServer([500,500,201]) as server,tempfile.TemporaryDirectory() as directory:
            harness=Path(directory)/'publisher.ps1'
            harness.write_text(functions+"""\nPublish-PCDaily $args[0] 'test-token-with-at-least-thirty-two-characters' $args[1] 'PC-TEST' { @{platform='windows';value='fixture'} }\n""",encoding='utf-8-sig')
            result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(harness),server.endpoint,directory],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            latest=Path(directory)/'latest.json';self.assertTrue(latest.exists());self.assertEqual(len(server.requests),3)
            self.assertEqual(server.requests[0][1],'Bearer test-token-with-at-least-thirty-two-characters')
            self.assertTrue(all(item[2]==latest.read_bytes() for item in server.requests))
    def test_macos_refuses_redirect_and_keeps_latest(self):
        script=generate_pc_script(self.profile,self.source,'macos')
        code=script.content.split("<<'PC_COLLECTOR'\n",1)[1].split('\nPC_COLLECTOR',1)[0];namespace={'__name__':'collector_test'};exec(compile(code,'<collector>','exec'),namespace)
        with CaptureServer([302,302,302]) as server,tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError): namespace['publish_latest'](server.endpoint,'test-token-with-at-least-thirty-two-characters',Path(directory),lambda:{'platform':'macos','value':'fixture'})
            latest=Path(directory)/'latest.json';self.assertTrue(latest.exists());self.assertEqual(len(server.requests),3);self.assertTrue(all(item[2]==latest.read_bytes() for item in server.requests))
