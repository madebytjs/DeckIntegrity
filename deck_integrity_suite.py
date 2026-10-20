#!/usr/bin/env python3
"""Deck Integrity Suite v0.1 — non-destructive Linux/SteamOS diagnostics.
Python 3 standard library only. Optional: smartctl, nvme, vulkaninfo, glxinfo.
"""
import argparse, datetime, hashlib, json, os, pathlib, platform, re, shutil, subprocess, sys, tempfile, time, traceback

EXPECTED_ZERO_2G = 'a7c744c13cc101ed66c29f672f92455547889cc586ce6d44fe76ae824958ea51'
MIB = 1024**2
GIB = 1024**3

class Suite:
    def __init__(self, args):
        self.args = args
        self.started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.home = pathlib.Path.home()
        self.out = pathlib.Path(args.output).expanduser().resolve() if args.output else self.home / 'deck-integrity-reports' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.out.mkdir(parents=True, exist_ok=True)
        self.logfile = self.out / 'report.txt'
        self.results = []
        self.work = None
        self.log('Deck Integrity Suite v0.1 | ' + self.started)
        self.log('Non-destructive: only writes its own temporary test files. No root required.')
        self.log('CAUTION: Avoid running during important downloads or low-battery conditions.')
    def log(self, msg=''):
        s = str(msg)
        print(s, flush=True)
        with self.logfile.open('a', encoding='utf-8', errors='replace') as f: f.write(s+'\n')
    def record(self, name, status, details=''):
        self.results.append({'test': name, 'status': status, 'details': str(details)})
        self.log(f'[{status}] {name}: {details}')
    def cmd(self, name, command, timeout=30, redact=True):
        if not shutil.which(command[0]):
            self.record(name, 'SKIP', command[0]+' not installed'); return
        try:
            p = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', timeout=timeout)
            data = p.stdout[:16000]
            if redact: data = self.redact(data)
            self.log(f'\n--- {name} (exit {p.returncode}) ---\n{data}')
            self.record(name, 'INFO' if p.returncode == 0 else 'WARN', 'exit='+str(p.returncode))
        except Exception as e: self.record(name, 'WARN', repr(e))
    def redact(self, s):
        s = s.replace(str(self.home), '$HOME')
        for v in [os.environ.get('USER',''), os.environ.get('LOGNAME','')]:
            if v and len(v)>2: s = re.sub(r'(?<![\w])'+re.escape(v)+r'(?![\w])', '<user>', s)
        s = re.sub(r'(?im)^.*(?:serial number|serial_number|wwn|eui64|nguid|mac address|host name|hostname).*$', '<identifier redacted>', s)
        return s
    def hash_file(self, path, direct=False):
        if direct:
            dd = subprocess.Popen(['dd', 'if='+str(path), 'iflag=direct', 'bs=4M', 'status=none'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            h = subprocess.Popen(['sha256sum'], stdin=dd.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            dd.stdout.close()
            out, err = h.communicate(timeout=300)
            _, derr = dd.communicate(timeout=10)
            if dd.returncode or h.returncode: raise RuntimeError(f'dd={dd.returncode} sha256sum={h.returncode}: {derr.decode(errors="replace")[:500]} {err[:500]}')
            return out.split()[0]
        p = subprocess.run(['sha256sum', str(path)], capture_output=True, text=True, timeout=300)
        if p.returncode: raise RuntimeError(p.stderr[:500])
        return p.stdout.split()[0]
    def hashes(self, label, path, passes, direct=False, reference=None):
        observed=[]
        for i in range(passes):
            try:
                val=self.hash_file(path, direct=direct)
                observed.append(val)
                self.log(f'  {label} pass {i+1}/{passes}: {val}')
            except Exception as e:
                self.record(label, 'FAIL', f'pass {i+1}: {e}'); return
        if len(set(observed))!=1:
            self.record(label, 'FAIL', f'INCONSISTENT: {len(set(observed))} distinct hashes across {passes} passes')
        elif reference and observed[0]!=reference:
            self.record(label, 'FAIL', f'MISMATCH: expected {reference}, got {observed[0]}')
        else: self.record(label, 'PASS', f'{passes}/{passes} identical; sha256={observed[0]}')
    def zero_stream(self, passes):
        for i in range(passes):
            # Pipe 2 GiB of zeros through the OS tools, without SSD file reads.
            dd = subprocess.Popen(['dd', 'if=/dev/zero', 'bs=4M', 'count=512', 'status=none'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            sha = subprocess.Popen(['sha256sum'], stdin=dd.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            dd.stdout.close()
            out, err=sha.communicate(timeout=300)
            _, derr=dd.communicate(timeout=10)
            value=out.split()[0] if out.strip() else '<none>'
            ok=dd.returncode==0 and sha.returncode==0 and value==EXPECTED_ZERO_2G
            self.log(f'  zero stream pass {i+1}/{passes}: {value} (expected {EXPECTED_ZERO_2G})')
            self.record(f'2GiB /dev/zero pass {i+1}', 'PASS' if ok else 'FAIL', f'dd_exit={dd.returncode} sha_exit={sha.returncode}; sha256={value}; {derr.decode(errors="replace")[:150]} {err[:150]}')
    def create_random(self, path, size_mib):
        self.log(f'Generating {size_mib} MiB random SSD test file: {path.name}')
        p=subprocess.run(['dd', 'if=/dev/urandom', 'of='+str(path), 'bs=1M', 'count='+str(size_mib), 'conv=fsync', 'status=none'], capture_output=True, text=True, timeout=900)
        if p.returncode: raise RuntimeError(p.stderr)
        os.sync()
        return self.hash_file(path)
    def ram_test(self):
        shm=pathlib.Path('/dev/shm')
        if not shm.is_dir() or not os.access(shm, os.W_OK):
            self.record('RAM-backed file', 'SKIP', '/dev/shm unavailable'); return
        free=shutil.disk_usage(shm).free
        size=min(self.args.ram_mib, max(0, (free//MIB)-64))
        if size<32:
            self.record('RAM-backed file', 'SKIP', 'insufficient /dev/shm space'); return
        fd,name=tempfile.mkstemp(prefix='deck-integrity-', suffix='.bin', dir=shm)
        os.close(fd)
        try:
            with open(name,'wb') as f:
                for _ in range(size): f.write(os.urandom(MIB))
            self.hashes('RAM-backed buffered reads', pathlib.Path(name), self.args.passes)
        finally:
            pathlib.Path(name).unlink(missing_ok=True)
    def system_info(self):
        self.log('\n=== ENVIRONMENT ===')
        self.log('OS: '+platform.platform())
        self.log('Kernel: '+platform.release())
        self.log('Python: '+platform.python_version())
        self.log('Machine: '+platform.machine())
        for filename in ['/etc/os-release','/proc/meminfo']:
            try:
                content=pathlib.Path(filename).read_text(errors='replace')
                if filename.endswith('meminfo'): content='\n'.join(content.splitlines()[:8])
                self.log(f'{filename}:\n{content[:3000]}')
            except Exception: pass
        self.cmd('Storage layout', ['lsblk','-o','NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL','-e','7'], timeout=15)
        self.cmd('Filesystem capacity', ['df','-hT',str(self.home)], timeout=15)
        self.cmd('CPU information', ['lscpu'], timeout=15)
        self.cmd('PCI GPU and controllers', ['lspci','-nnk'], timeout=15)
        self.cmd('Graphics GL renderer', ['glxinfo','-B'], timeout=15)
        self.cmd('Vulkan summary', ['vulkaninfo','--summary'], timeout=20)
        self.cmd('GPU DRM sysfs', ['ls','-l','/sys/class/drm'], timeout=15)
        self.cmd('NVMe device list', ['nvme','list'], timeout=20)
        self.cmd('SMART device list', ['smartctl','--scan'], timeout=20)
        self.cmd('Journal kernel warnings (current boot)', ['journalctl','-k','-b','-p','warning','--no-pager','-n','150'], timeout=25)
    def run(self):
        self.system_info()
        self.log('\n=== INTEGRITY TESTS ===')
        if not all(shutil.which(x) for x in ['dd','sha256sum']):
            self.record('Core utilities','FAIL','dd and sha256sum required'); return
        self.zero_stream(self.args.passes)
        self.ram_test()
        home_free=shutil.disk_usage(self.home).free
        needed=(self.args.file_mib+1024)*MIB
        if home_free<needed:
            self.record('SSD test', 'SKIP', f'need {needed//MIB} MiB free; have {home_free//MIB} MiB'); return
        self.work=pathlib.Path(tempfile.mkdtemp(prefix='deck-integrity-',dir=self.home))
        try:
            target=self.work/'test.bin'
            reference=self.create_random(target,self.args.file_mib)
            self.log('  initial SSD reference hash: '+reference)
            self.hashes('SSD buffered reads',target,self.args.passes,reference=reference)
            self.hashes('SSD direct reads',target,self.args.passes,direct=True,reference=reference)
            self.hashes('SSD buffered reads (repeat)',target,self.args.passes,reference=reference)
        except Exception as e:
            self.record('SSD tests', 'FAIL', repr(e))
            self.log(traceback.format_exc())
        finally:
            if self.work and not self.args.keep_file:
                shutil.rmtree(self.work,ignore_errors=True)
                self.log('Temporary SSD test files deleted.')
            elif self.work:
                self.log('Test file retained at '+str(self.work))
    def finish(self):
        self.log('\n=== SUMMARY ===')
        counts={k:sum(r['status']==k for r in self.results) for k in ['PASS','FAIL','WARN','SKIP','INFO']}
        self.log(json.dumps(counts,indent=2))
        self.log('Note: PASS only means no fault was observed in this run. Intermittent failures may be missed.')
        self.log('This version inventories GPU/CPU/drivers; it does not perform a GPU rendering stress test or exhaustive RAM stress test.')
        self.log('Before sharing publicly, review report for any remaining personal identifiers.')
        (self.out/'results.json').write_text(json.dumps({'version':'0.1','started_utc':self.started,'counts':counts,'tests':self.results},indent=2))
        self.log('Reports: '+str(self.out))
        return 1 if counts['FAIL'] else 0

def main():
    p=argparse.ArgumentParser(description='Non-destructive SteamOS/Linux integrity tests with logged results')
    p.add_argument('--passes',type=int,default=5,help='repetitions per test (default: 5)')
    p.add_argument('--file-mib',type=int,default=2048,help='SSD random test file MiB (default: 2048)')
    p.add_argument('--ram-mib',type=int,default=256,help='RAM-backed test file MiB (default: 256)')
    p.add_argument('--output',help='output directory (default: ~/deck-integrity-reports/<timestamp>)')
    p.add_argument('--keep-file',action='store_true',help='retain random SSD test file for later investigations')
    a=p.parse_args()
    if a.passes<1 or a.passes>100 or a.file_mib<32 or a.file_mib>16384 or a.ram_mib<32 or a.ram_mib>4096: p.error('invalid test size or pass count')
    s=Suite(a)
    try: s.run()
    except KeyboardInterrupt: s.record('Suite','WARN','interrupted by user')
    except Exception as e: s.record('Suite','FAIL',repr(e)); s.log(traceback.format_exc())
    finally: return s.finish()
if __name__=='__main__': sys.exit(main())
