#!/usr/bin/env python3
"""Patch the EXISTING Compose, preserve MQTT/Ledger settings; rotate existing DB accounts.
Run locally on Pi after backing up DB. Never prints secret values.
"""
import argparse
import datetime
import os
from pathlib import Path
import re
import secrets
import subprocess


def patch_compose(text):
    lines = text.splitlines(True)
    service = None
    mysql_ports = False
    found = set()
    out = []
    for line in lines:
        m = re.match(r'^  ([\w-]+):\s*$', line.rstrip())
        if m:
            service = m[1]
            mysql_ports = False
        if re.match(r'^version\s*:', line):
            continue
        if service == 'mysql' and re.match(r'^    ports:\s*$', line.rstrip()):
            mysql_ports = True
            continue
        if mysql_ports:
            if not line.strip() or line.lstrip().startswith('#') or re.match(r'^\s{6,}', line):
                continue
            mysql_ports = False
        key = re.match(r'^(\s+-\s+)(MYSQL_ROOT_PASSWORD|MYSQL_PASSWORD|DB_PASS|DB_PASSWORD)\s*=', line)
        if key:
            name = key[2]
            if service == 'mysql' and name in ('MYSQL_ROOT_PASSWORD','MYSQL_PASSWORD'):
                variable = name
            elif name in ('DB_PASS','DB_PASSWORD'):
                variable = 'MYSQL_PASSWORD'
            else:
                raise ValueError('Unexpected password setting; review manually')
            line = f'{key[1]}{name}=${{{variable}:?Set {variable} in .env}}\n'
            found.add((service, name))
        if service == 'mysql' and re.match(r'^      test:', line):
            line = '      test: ["CMD-SHELL", \'MYSQL_PWD="$$MYSQL_PASSWORD" mysql -h 127.0.0.1 -u"$$MYSQL_USER" -D "$$MYSQL_DATABASE" -Nse "SELECT 1" >/dev/null\']\n'
            found.add(('mysql','healthcheck'))
        out.append(line)
    for item in [('mysql','MYSQL_ROOT_PASSWORD'),('mysql','MYSQL_PASSWORD'),('mysql','healthcheck')]:
        if item not in found:
            raise ValueError('Expected list-style Compose settings not found: '+str(item))
    for svc in ('api','api-mqtt-bridge'):
        if not any(s == svc and k in ('DB_PASS','DB_PASSWORD') for s,k in found):
            raise ValueError('Missing DB password for '+svc)
    return ''.join(out)


def env_update(text, values):
    lines = [line for line in text.splitlines() if not re.match(r'^\s*(?:export\s+)?(?:MYSQL_ROOT_PASSWORD|MYSQL_PASSWORD|DB_PASS|DB_PASSWORD)\s*=', line)]
    return '\n'.join(lines).rstrip()+'\n'+''.join(k+'='+v+'\n' for k,v in values.items())


def mysql(sql):
    result = subprocess.run(['docker','compose','exec','-T','mysql','sh','-c',
                             'export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql -uroot --batch --skip-column-names'],
                            input=sql, text=True, capture_output=True)
    if result.returncode:
        # MySQL error strings may contain user-controlled data. Do not echo credentials or SQL.
        raise RuntimeError('MySQL command failed; SQL/secrets suppressed. Check account/configuration locally.')
    return result.stdout


def protected_write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Apply changes and rotate DB passwords after backup')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    if not (root/'API/common/audit_log_service.py').exists():
        raise RuntimeError('Missing common audit writer; install/inspect it before changing create_family')
    compose = root/'docker-compose.yml'
    old_compose = compose.read_text()
    new_compose = patch_compose(old_compose)
    # Refuse hidden Compose files/overrides: deployment must preserve a known effective configuration.
    for name in ('docker-compose.override.yml','docker-compose.override.yaml','compose.override.yml','compose.override.yaml'):
        if (root/name).exists():
            raise RuntimeError('Compose override found; review its DB ports/password settings manually first: '+name)
    if any(os.environ.get(k) for k in ('MYSQL_PASSWORD','MYSQL_ROOT_PASSWORD','COMPOSE_ENV_FILES')):
        raise RuntimeError('Shell overrides MYSQL passwords/Compose env files; unset those variables locally before applying')
    if any((root/name).exists() for name in ('compose.yml','compose.yaml','docker-compose.yaml')):
        raise RuntimeError('Alternative Compose filename found; review which file is active before applying')
    if os.environ.get('COMPOSE_FILE'):
        raise RuntimeError('COMPOSE_FILE is set; review effective configuration before applying')
    if not args.apply:
        print('Preflight passed. No files or accounts changed. Read APPLY_ON_PI.md, then use --apply.')
        return
    if (root/'security-db-rotation.pending').exists():
        raise RuntimeError('Previous rotation pending. Resolve with retained backup and .env; do not rerun blindly.')
    # Check existing account names and all root hosts BEFORE changing configuration.
    accounts = mysql("SELECT User,Host FROM mysql.user WHERE User IN ('root','apiuser');\n")
    pairs = [line.split('\t') for line in accounts.splitlines() if '\t' in line]
    if ['apiuser','%'] not in pairs or ['root','localhost'] not in pairs:
        raise RuntimeError('Unexpected account hosts; review manually. No changes made.')
    for user,host in pairs:
        if not re.fullmatch(r'[\w.%:-]+',host):
            raise RuntimeError('Unexpected host value')
    # Validate the targeted Python edits before stopping services or changing accounts.
    edits = []
    for rel in ('API/alert_service/alert_service.py','API/generate_guest_qr/generate_guest_qr.py','API/get_family_context/get_family_context.py'):
        path = root/rel
        if not path.exists():
            continue  # Pi's deployment may not include alert-service.
        before = path.read_text()
        after = re.sub(r'''os\.getenv\((['"])DB_PASS\1\s*,\s*(['"])[^'"\n]*\2\)''',
                       '''(os.getenv('DB_PASS') or os.environ['DB_PASSWORD'])''', before)
        compile(after, str(path), 'exec')
        if after != before: edits.append((path,before,after))
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    ignore = root/'.gitignore'
    content = ignore.read_text() if ignore.exists() else ''
    content += '\n# Local DB credentials and recovery files\n/.env\n/.ledger.env\n/security-backup-*/\n/security-db-rotation.pending\n'
    ignore.write_text(content)
    backup = root/('security-backup-'+stamp)
    backup.mkdir(mode=0o700)
    protected_write(backup/'docker-compose.yml',old_compose)
    env = root/'.env'
    old_env = env.read_text() if env.exists() else ''
    protected_write(backup/'env-before',old_env)
    ledger = root/'.ledger.env'
    old_ledger = ledger.read_text() if ledger.exists() else None
    if old_ledger is not None: protected_write(backup/'ledger-env-before',old_ledger)
    passwords = {'MYSQL_ROOT_PASSWORD':secrets.token_hex(32),'MYSQL_PASSWORD':secrets.token_hex(32)}
    # Persist recovery credentials before issuing nontransactional ALTER USER.
    protected_write(env, env_update(old_env,passwords))
    protected_write(root/'security-db-rotation.pending',str(backup)+'\n')
    subprocess.run(['docker','compose','stop','api','api-mqtt-bridge'],check=True)
    # Stop optional SQL writer only when present.
    services = subprocess.run(['docker','compose','config','--services'],capture_output=True,text=True,check=True).stdout.splitlines()
    if 'alert-service' in services:
        subprocess.run(['docker','compose','stop','alert-service'],check=True)
    # Existing mysql container retains OLD environment until recreated.
    statements = ["ALTER USER 'apiuser'@'%' IDENTIFIED BY '"+passwords['MYSQL_PASSWORD']+"';"]
    for user,host in pairs:
        if user == 'root': statements.append("ALTER USER 'root'@'"+host+"' IDENTIFIED BY '"+passwords['MYSQL_ROOT_PASSWORD']+"';")
    mysql('\n'.join(statements)+'\n')
    protected_write(compose,new_compose)
    for path,before,after in edits:
        dest = backup/path.relative_to(root)
        dest.parent.mkdir(parents=True,exist_ok=True)
        protected_write(dest,before)
        path.write_text(after)
    if old_ledger is not None:
        # Drop duplicate DB password definitions so Compose environment wins.
        cleaned = '\n'.join(line for line in old_ledger.splitlines() if not re.match(r'^\s*(?:export\s+)?(?:DB_PASS|DB_PASSWORD|MYSQL_PASSWORD|MYSQL_ROOT_PASSWORD)\s*=',line))+'\n'
        protected_write(ledger,cleaned)
    subprocess.run(['docker','compose','config','--quiet'],check=True)
    (root/'security-db-rotation.pending').unlink()
    print('Passwords rotated; current Compose patched without changing MQTT/ports other than MySQL.')
    print('Protected recovery backup:', backup)
    print('API writers remain stopped. Follow APPLY_ON_PI.md to recreate mysql and API containers.')


if __name__ == '__main__':
    main()
