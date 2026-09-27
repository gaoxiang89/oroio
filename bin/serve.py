#!/usr/bin/env python3
import base64
import concurrent.futures
import hashlib
import http.server
import json
import os
import platform
import secrets
import subprocess
import sys
import threading
import urllib.request
import urllib.error
from urllib.parse import unquote, urlsplit, urlunsplit

SALT = b"oroio"
VALID_TOKENS = set()  # In-memory token storage
PIN_HASH = None  # Will be set on startup
ITERATIONS = 10000
IS_WINDOWS = platform.system() == 'Windows'

def _derive_key_iv(salt: bytes) -> tuple:
    """PBKDF2-SHA256 派生 key(32) 和 iv(16)"""
    derived = hashlib.pbkdf2_hmac('sha256', SALT, salt, ITERATIONS, dklen=48)
    return derived[:32], derived[32:48]

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    
    bcrypt = ctypes.windll.bcrypt
    BCRYPT_AES_ALGORITHM = "AES"
    BCRYPT_CHAIN_MODE_CBC = "ChainingModeCBC"
    BCRYPT_CHAINING_MODE = "ChainingMode"
    
    class AESCipher:
        def __init__(self, key: bytes, iv: bytes):
            self.hAlg = ctypes.c_void_p()
            self.hKey = ctypes.c_void_p()
            self.iv = (ctypes.c_ubyte * len(iv))(*iv)
            bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(self.hAlg), BCRYPT_AES_ALGORITHM, None, 0)
            mode = BCRYPT_CHAIN_MODE_CBC.encode('utf-16-le') + b'\x00\x00'
            bcrypt.BCryptSetProperty(self.hAlg, BCRYPT_CHAINING_MODE, mode, len(mode), 0)
            bcrypt.BCryptGenerateSymmetricKey(self.hAlg, ctypes.byref(self.hKey), None, 0, key, len(key), 0)
        
        def decrypt(self, ciphertext: bytes) -> bytes:
            out_len = wintypes.ULONG()
            iv_copy = (ctypes.c_ubyte * len(self.iv))(*self.iv)
            bcrypt.BCryptDecrypt(self.hKey, ciphertext, len(ciphertext), None, iv_copy, len(iv_copy), None, 0, ctypes.byref(out_len), 1)
            out_buf = (ctypes.c_ubyte * out_len.value)()
            iv_copy = (ctypes.c_ubyte * len(self.iv))(*self.iv)
            bcrypt.BCryptDecrypt(self.hKey, ciphertext, len(ciphertext), None, iv_copy, len(iv_copy), out_buf, out_len.value, ctypes.byref(out_len), 1)
            return bytes(out_buf[:out_len.value])
        
        def encrypt(self, plaintext: bytes) -> bytes:
            pad_len = 16 - (len(plaintext) % 16)
            plaintext = plaintext + bytes([pad_len] * pad_len)
            out_len = wintypes.ULONG()
            iv_copy = (ctypes.c_ubyte * len(self.iv))(*self.iv)
            bcrypt.BCryptEncrypt(self.hKey, plaintext, len(plaintext), None, iv_copy, len(iv_copy), None, 0, ctypes.byref(out_len), 0)
            out_buf = (ctypes.c_ubyte * out_len.value)()
            iv_copy = (ctypes.c_ubyte * len(self.iv))(*self.iv)
            bcrypt.BCryptEncrypt(self.hKey, plaintext, len(plaintext), None, iv_copy, len(iv_copy), out_buf, out_len.value, ctypes.byref(out_len), 0)
            return bytes(out_buf[:out_len.value])
        
        def __del__(self):
            if self.hKey: bcrypt.BCryptDestroyKey(self.hKey)
            if self.hAlg: bcrypt.BCryptCloseAlgorithmProvider(self.hAlg, 0)

def decrypt_keys(keys_file: str) -> list:
    """解密 keys.enc 文件，返回 key 列表"""
    if not os.path.isfile(keys_file):
        return []
    try:
        if IS_WINDOWS:
            with open(keys_file, 'rb') as f:
                data = f.read()
            if len(data) < 17 or data[:8] != b'Salted__':
                return []
            salt, ciphertext = data[8:16], data[16:]
            key, iv = _derive_key_iv(salt)
            cipher = AESCipher(key, iv)
            text = cipher.decrypt(ciphertext).decode('utf-8')
        else:
            result = subprocess.run(
                ['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-in', keys_file, '-pass', f'pass:{SALT.decode()}'],
                capture_output=True
            )
            if result.returncode != 0:
                return []
            text = result.stdout.decode('utf-8')
        keys = []
        for line in text.split('\n'):
            line = line.strip()
            if line:
                keys.append(line.split('\t')[0])
        return keys
    except Exception:
        return []

def encrypt_keys(keys: list, keys_file: str):
    """加密 key 列表并写入文件"""
    text = '\n'.join(f"{k}\t" for k in keys)
    if IS_WINDOWS:
        import secrets
        salt = secrets.token_bytes(8)
        key, iv = _derive_key_iv(salt)
        cipher = AESCipher(key, iv)
        ciphertext = cipher.encrypt(text.encode('utf-8'))
        with open(keys_file, 'wb') as f:
            f.write(b'Salted__' + salt + ciphertext)
    else:
        subprocess.run(
            ['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-salt', '-out', keys_file, '-pass', f'pass:{SALT.decode()}'],
            input=text.encode('utf-8'),
            check=True
        )

LIMITS_URL = 'https://app.factory.ai/api/billing/limits'
USAGE_URL = 'https://app.factory.ai/api/organization/members/chat-usage'
API_TIMEOUT = 8
API_RETRIES = 3
FACTORY_DIR = os.path.join(os.path.expanduser('~'), '.factory')
WINDOW_KEYS = (('fiveHour', '5h'), ('weekly', '7d'), ('monthly', '30d'))

def _factory_headers(key: str) -> dict:
    return {
        'Authorization': f'Bearer {key}',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }

def _empty_usage() -> dict:
    return {
        'MODE': '',
        'DISPLAY': '',
        'BALANCE': 0,
        'BALANCE_NUM': 0,
        'TOTAL': 0,
        'USED': 0,
        'EXPIRES': '?',
        'EXTRA_CENTS': 0,
        'CORE_USED': 0,
        'OVERAGE_PREF': '',
        'RAW': '',
    }

def _fmt_pct(p) -> str:
    p = float(p)
    r = round(p)
    if abs(p - r) < 0.05:
        return str(int(r))
    return f'{p:.1f}'

def _parse_iso(end):
    if not end:
        return None
    try:
        from datetime import datetime, timezone
        s = str(end).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _window_pct(w, now) -> float:
    if not isinstance(w, dict):
        return 0.0
    try:
        pct = float(w.get('usedPercent') or 0)
    except (TypeError, ValueError):
        pct = 0.0
    rem = w.get('secondsRemaining')
    if rem is not None:
        try:
            if float(rem) <= 0:
                return 0.0
        except (TypeError, ValueError):
            pass
    else:
        dt = _parse_iso(w.get('windowEnd'))
        if dt is not None and dt <= now:
            return 0.0
    return min(100.0, max(0.0, pct))

def _pool_windows(pool, now):
    pool = pool if isinstance(pool, dict) else {}
    out = []
    for key, label in WINDOW_KEYS:
        w = pool.get(key)
        if not isinstance(w, dict):
            w = {}
        rem = w.get('secondsRemaining')
        try:
            rem_n = float(rem) if rem is not None else None
        except (TypeError, ValueError):
            rem_n = None
        out.append({'pct': _window_pct(w, now), 'label': label, 'w': w, 'rem': rem_n})
    return out

def _bottleneck(windows):
    def sort_key(item):
        rem = item['rem'] if item['rem'] is not None else 1e18
        return (-item['pct'], rem)
    return sorted(windows, key=sort_key)[0]

def parse_rate_limits(data: dict) -> dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    limits = data.get('limits') or {}
    std = _pool_windows(limits.get('standard'), now)
    core = _pool_windows(limits.get('core'), now)
    bot = _bottleneck(std)
    used = int(round(bot['pct']))
    remain = 100 - used
    display = ' '.join(f"{w['label']} {_fmt_pct(w['pct'])}%" for w in std)
    dt = _parse_iso(bot['w'].get('windowEnd'))
    if dt is None and bot['rem'] is not None:
        try:
            dt = datetime.fromtimestamp(now.timestamp() + float(bot['rem']), tz=timezone.utc)
        except Exception:
            dt = None
    result = _empty_usage()
    result['MODE'] = 'rate'
    result['DISPLAY'] = display
    result['TOTAL'] = 100
    result['USED'] = used
    result['BALANCE_NUM'] = remain
    result['BALANCE'] = remain
    result['EXPIRES'] = dt.strftime('%Y-%m-%d') if dt else '?'
    result['EXTRA_CENTS'] = int(data.get('extraUsageBalanceCents') or 0)
    result['CORE_USED'] = int(round(_bottleneck(core)['pct'])) if core else 0
    result['OVERAGE_PREF'] = data.get('overagePreference') or ''
    return result

def parse_token_usage(data: dict) -> dict:
    result = _empty_usage()
    result['MODE'] = 'tokens'
    usage = data.get('usage')
    if not usage:
        result['RAW'] = 'no_usage'
        return result
    section = usage.get('standard') or usage.get('premium') or usage.get('total') or usage.get('main')
    if section:
        total = section.get('totalAllowance') or section.get('basicAllowance') or section.get('allowance')
        # orgOverageUsed is already included in orgTotalTokensUsed
        used = section.get('orgTotalTokensUsed') or section.get('used') or section.get('tokensUsed') or 0
        if total is not None:
            result['TOTAL'] = int(total)
            result['USED'] = int(used)
            result['BALANCE_NUM'] = int(total - used)
            result['BALANCE'] = result['BALANCE_NUM']
    exp_raw = usage.get('endDate') or usage.get('expire_at') or usage.get('expires_at')
    if exp_raw is not None:
        if isinstance(exp_raw, (int, float)) or (isinstance(exp_raw, str) and str(exp_raw).isdigit()):
            from datetime import datetime
            result['EXPIRES'] = datetime.utcfromtimestamp(int(exp_raw) / 1000).strftime('%Y-%m-%d')
        else:
            result['EXPIRES'] = str(exp_raw)
    return result

def _get_json(url: str, key: str):
    req = urllib.request.Request(url, headers=_factory_headers(key))
    with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
        return json.loads(resp.read().decode('utf-8'))

def fetch_usage(key: str) -> dict:
    """获取单个 key 的用量：优先 Factory 滚动限额，否则回退到旧 token 额度。"""
    import time
    result = _empty_usage()
    last_error = None

    for attempt in range(API_RETRIES):
        try:
            data = _get_json(LIMITS_URL, key)
            if data.get('usesTokenRateLimitsBilling') and data.get('limits') is not None:
                return parse_rate_limits(data)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                break
            if 400 <= e.code < 500:
                result['RAW'] = f'http_{e.code}'
                result['EXPIRES'] = 'Invalid key'
                return result
            last_error = e
            if attempt < API_RETRIES - 1:
                time.sleep(0.5)
                continue
            break
        except Exception as e:
            last_error = e
            if attempt < API_RETRIES - 1:
                time.sleep(0.5)
                continue
            break

    for attempt in range(API_RETRIES):
        try:
            data = _get_json(USAGE_URL, key)
            return parse_token_usage(data)
        except urllib.error.HTTPError as e:
            result['RAW'] = f'http_{e.code}'
            result['EXPIRES'] = 'Invalid key'
            return result
        except Exception as e:
            last_error = e
            if attempt < API_RETRIES - 1:
                time.sleep(0.5)
                continue

    result['RAW'] = 'http_error'
    result['EXPIRES'] = 'Invalid key'
    return result

def fetch_all_usages(keys: list) -> list:
    """并发获取所有 key 的用量"""
    max_workers = min(6, len(keys)) if keys else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(fetch_usage, keys))

def write_cache(keys_file: str, cache_file: str, keys: list, usages: list):
    """写入缓存文件，格式与 dk/dk.ps1 兼容"""
    import time
    now = int(time.time())
    keys_hash = hashlib.sha1(open(keys_file, 'rb').read()).hexdigest()
    lines = [str(now), keys_hash]
    for i, u in enumerate(usages):
        info = '\n'.join([
            f"MODE={u.get('MODE', '')}",
            f"DISPLAY={u.get('DISPLAY', '')}",
            f"BALANCE={u.get('BALANCE', 0)}",
            f"BALANCE_NUM={u.get('BALANCE_NUM', 0)}",
            f"TOTAL={u.get('TOTAL', 0)}",
            f"USED={u.get('USED', 0)}",
            f"EXPIRES={u.get('EXPIRES', '?')}",
            f"EXTRA_CENTS={u.get('EXTRA_CENTS', 0)}",
            f"CORE_USED={u.get('CORE_USED', 0)}",
            f"OVERAGE_PREF={u.get('OVERAGE_PREF', '')}",
            f"RAW={u.get('RAW', '')}"
        ])
        b64 = base64.b64encode(info.encode('utf-8')).decode('ascii')
        lines.append(f"{i}\t{b64}")
    with open(cache_file, 'w') as f:
        f.write('\n'.join(lines))

class OroioHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, oroio_dir=None, dk_path=None, **kwargs):
        self.oroio_dir = oroio_dir
        self.dk_path = dk_path
        self.keys_file = os.path.join(oroio_dir, 'keys.enc')
        self.current_file = os.path.join(oroio_dir, 'current')
        self.cache_file = os.path.join(oroio_dir, 'list_cache.b64')
        super().__init__(*args, **kwargs)
    
    def _check_auth(self) -> bool:
        """Check if request has valid auth token. Returns True if PIN is not set or token is valid."""
        global PIN_HASH, VALID_TOKENS
        if PIN_HASH is None:
            return True
        token = self.headers.get('X-Auth-Token', '')
        return token in VALID_TOKENS
    
    def _send_unauthorized(self):
        """Send 401 Unauthorized response"""
        self.send_response(401)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode('utf-8'))
    
    def _invalidate_cache(self):
        """删除缓存文件"""
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
        except:
            pass
    
    def _get_current_index(self) -> int:
        try:
            with open(self.current_file, 'r') as f:
                return max(1, int(f.read().strip()))
        except:
            return 1
    
    def _set_current_index(self, idx: int):
        with open(self.current_file, 'w') as f:
            f.write(str(idx))
    
    def do_GET(self):
        path = unquote(self.path)
        if path.startswith('/data/'):
            self.serve_oroio_file(path[6:])
        else:
            self.serve_static_with_etag(path)
    
    def serve_static_with_etag(self, path):
        """Serve static files with ETag support"""
        filepath = self.translate_path(self.path)
        if os.path.isdir(filepath):
            # 与 SimpleHTTPRequestHandler 保持一致：目录请求缺少尾斜杠时先 301，确保后续相对资源以目录为基准解析
            parsed = urlsplit(self.path)
            if not parsed.path.endswith('/'):
                new_path = parsed._replace(path=parsed.path + '/')
                self.send_response(301)
                self.send_header('Location', urlunsplit(new_path))
                self.end_headers()
                return
            filepath = os.path.join(filepath, 'index.html')
        if not os.path.isfile(filepath):
            super().do_GET()
            return
        
        try:
            stat = os.stat(filepath)
            etag = f'"{stat.st_size}-{int(stat.st_mtime)}"'
            
            if_none_match = self.headers.get('If-None-Match')
            if if_none_match == etag:
                self.send_response(304)
                self.send_header('ETag', etag)
                self.end_headers()
                return
            
            with open(filepath, 'rb') as f:
                content = f.read()
            
            self.send_response(200)
            self.send_header('Content-Type', self.guess_type(filepath))
            self.send_header('Content-Length', len(content))
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            super().do_GET()
    
    def do_POST(self):
        path = unquote(self.path)
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else ''
        
        try:
            data = json.loads(body) if body else {}
        except:
            data = {}
        
        # Auth endpoints (no auth required)
        if path == '/api/auth':
            self.handle_auth(data)
            return
        elif path == '/api/auth/check':
            self.handle_auth_check()
            return
        
        # All other endpoints require auth
        if not self._check_auth():
            self._send_unauthorized()
            return
        
        if path == '/api/add':
            self.handle_add_key(data)
        elif path == '/api/remove':
            self.handle_remove_key(data)
        elif path == '/api/use':
            self.handle_use_key(data)
        elif path == '/api/refresh':
            self.handle_refresh()
        # Skills
        elif path == '/api/skills/list':
            self.handle_list_skills()
        elif path == '/api/skills/create':
            self.handle_create_skill(data)
        elif path == '/api/skills/delete':
            self.handle_delete_skill(data)
        # Commands
        elif path == '/api/commands/list':
            self.handle_list_commands()
        elif path == '/api/commands/create':
            self.handle_create_command(data)
        elif path == '/api/commands/delete':
            self.handle_delete_command(data)
        elif path == '/api/commands/content':
            self.handle_command_content(data)
        elif path == '/api/commands/update':
            self.handle_update_command(data)
        elif path == '/api/commands/rename':
            self.handle_rename_command(data)
        # Droids
        elif path == '/api/droids/list':
            self.handle_list_droids()
        elif path == '/api/droids/create':
            self.handle_create_droid(data)
        elif path == '/api/droids/delete':
            self.handle_delete_droid(data)
        # MCP
        elif path == '/api/mcp/list':
            self.handle_list_mcp()
        elif path == '/api/mcp/add':
            self.handle_add_mcp(data)
        elif path == '/api/mcp/remove':
            self.handle_remove_mcp(data)
        elif path == '/api/mcp/update':
            self.handle_update_mcp(data)
        # BYOK (Custom Models)
        elif path == '/api/byok/list':
            self.handle_list_byok()
        elif path == '/api/byok/remove':
            self.handle_remove_byok(data)
        elif path == '/api/byok/update':
            self.handle_update_byok(data)
        # DK config
        elif path == '/api/dk/config':
            self.handle_dk_config(data)
        else:
            self.send_error(404, 'Not Found')
    
    def handle_auth(self, data):
        """Handle PIN authentication"""
        global PIN_HASH, VALID_TOKENS
        pin = data.get('pin', '')
        if PIN_HASH is None:
            # No PIN set, generate token anyway
            token = secrets.token_hex(32)
            if len(VALID_TOKENS) > 100:
                VALID_TOKENS.pop()
            VALID_TOKENS.add(token)
            self.send_json({'success': True, 'token': token, 'required': False})
            return
        if hashlib.sha256(pin.encode()).hexdigest() == PIN_HASH:
            token = secrets.token_hex(32)
            if len(VALID_TOKENS) > 100:
                VALID_TOKENS.pop()
            VALID_TOKENS.add(token)
            self.send_json({'success': True, 'token': token})
        else:
            self.send_json({'success': False, 'error': 'Invalid PIN'})
    
    def handle_auth_check(self):
        """Check if PIN is required and if current token is valid"""
        global PIN_HASH
        if PIN_HASH is None:
            self.send_json({'required': False, 'authenticated': True})
        elif self._check_auth():
            self.send_json({'required': True, 'authenticated': True})
        else:
            self.send_json({'required': True, 'authenticated': False})
    
    def serve_oroio_file(self, filename):
        if '..' in filename or filename.startswith('/'):
            self.send_error(403, 'Forbidden')
            return
        
        allowed_files = ['keys.enc', 'current', 'list_cache.b64']
        if filename not in allowed_files:
            self.send_error(404, 'Not Found')
            return
        
        filepath = os.path.join(self.oroio_dir, filename)
        
        if not os.path.isfile(filepath):
            if filename == 'list_cache.b64':
                # 首次启动缓存可能不存在，返回空内容避免 404 噪声
                content = b''
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', len(content))
                self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Expires', '0')
                self.end_headers()
                return
            self.send_error(404, 'Not Found')
            return
        
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', len(content))
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))
    
    def handle_add_key(self, data):
        key = data.get('key', '').strip()
        if not key:
            self.send_json({'success': False, 'error': 'Key is required'})
            return
        try:
            keys = decrypt_keys(self.keys_file)
            keys.append(key)
            encrypt_keys(keys, self.keys_file)
            self._invalidate_cache()
            self.send_json({'success': True, 'message': f'已添加。当前共有 {len(keys)} 个key。'})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_remove_key(self, data):
        index = data.get('index')
        if not index:
            self.send_json({'success': False, 'error': 'Index is required'})
            return
        try:
            idx = int(index)
            keys = decrypt_keys(self.keys_file)
            if idx < 1 or idx > len(keys):
                self.send_json({'success': False, 'error': '序号超出范围'})
                return
            keys.pop(idx - 1)
            encrypt_keys(keys, self.keys_file)
            self._set_current_index(1)
            self._invalidate_cache()
            self.send_json({'success': True, 'message': f'已删除，剩余 {len(keys)} 个key。'})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_use_key(self, data):
        index = data.get('index')
        if not index:
            self.send_json({'success': False, 'error': 'Index is required'})
            return
        try:
            idx = int(index)
            keys = decrypt_keys(self.keys_file)
            if idx < 1 or idx > len(keys):
                self.send_json({'success': False, 'error': '序号超出范围'})
                return
            self._set_current_index(idx)
            self.send_json({'success': True, 'message': f'已切换到序号 {idx}'})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_refresh(self):
        try:
            keys = decrypt_keys(self.keys_file)
            if not keys:
                self.send_json({'success': True})
                return
            usages = fetch_all_usages(keys)
            write_cache(self.keys_file, self.cache_file, keys, usages)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def send_json(self, data):
        body = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)
    
    # Skills handlers
    def handle_list_skills(self):
        skills_dir = os.path.join(FACTORY_DIR, 'skills')
        skills = []
        try:
            real_dir = os.path.realpath(skills_dir)
            for entry in os.listdir(real_dir):
                entry_path = os.path.join(real_dir, entry)
                if os.path.isdir(entry_path):
                    skill_file = os.path.join(entry_path, 'SKILL.md')
                    if os.path.isfile(skill_file):
                        skills.append({'name': entry, 'path': skill_file})
        except:
            pass
        self.send_json(skills)
    
    def handle_create_skill(self, data):
        name = data.get('name', '').strip()
        if not name:
            self.send_json({'success': False, 'error': 'Name is required'})
            return
        try:
            skill_dir = os.path.join(FACTORY_DIR, 'skills', name)
            os.makedirs(skill_dir, exist_ok=True)
            skill_file = os.path.join(skill_dir, 'SKILL.md')
            with open(skill_file, 'w') as f:
                f.write(f'# {name}\n\nDescribe your skill instructions here.\n')
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_delete_skill(self, data):
        name = data.get('name', '').strip()
        if not name:
            self.send_json({'success': False, 'error': 'Name is required'})
            return
        try:
            import shutil
            skill_dir = os.path.join(FACTORY_DIR, 'skills', name)
            shutil.rmtree(skill_dir)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    # Commands handlers
    def _parse_frontmatter(self, content):
        """解析 frontmatter 中的 description"""
        import re
        match = re.match(r'^---\s*\n([\s\S]*?)\n---', content)
        if match:
            for line in match.group(1).split('\n'):
                if line.startswith('description:'):
                    return line[12:].strip()
        return None

    def handle_list_commands(self):
        commands_dir = os.path.join(FACTORY_DIR, 'commands')
        commands = []
        try:
            real_dir = os.path.realpath(commands_dir)
            for entry in os.listdir(real_dir):
                if entry.endswith('.md'):
                    full_path = os.path.join(real_dir, entry)
                    if os.path.isfile(full_path):
                        with open(full_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        desc = self._parse_frontmatter(content)
                        commands.append({
                            'name': entry[:-3],
                            'path': full_path,
                            'description': desc,
                            'content': content
                        })
        except:
            pass
        commands.sort(key=lambda x: x['name'].lower())
        self.send_json(commands)
    
    def handle_create_command(self, data):
        name = data.get('name', '').strip()
        if not name:
            self.send_json({'success': False, 'error': 'Name is required'})
            return
        try:
            commands_dir = os.path.join(FACTORY_DIR, 'commands')
            os.makedirs(commands_dir, exist_ok=True)
            cmd_file = os.path.join(commands_dir, f'{name}.md')
            with open(cmd_file, 'w') as f:
                f.write(f'# /{name}\n\nCommand instructions here.\n')
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_delete_command(self, data):
        name = data.get('name', '').strip()
        if not name:
            self.send_json({'success': False, 'error': 'Name is required'})
            return
        try:
            cmd_file = os.path.join(FACTORY_DIR, 'commands', f'{name}.md')
            os.remove(cmd_file)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_command_content(self, data):
        name = data.get('name', '').strip()
        if not name:
            self.send_json({'error': 'Name is required'})
            return
        try:
            commands_dir = os.path.join(FACTORY_DIR, 'commands')
            real_dir = os.path.realpath(commands_dir)
            with open(os.path.join(real_dir, f'{name}.md'), 'r', encoding='utf-8') as f:
                content = f.read()
            self.send_json({'content': content})
        except Exception as e:
            self.send_json({'error': str(e)})
    
    def handle_update_command(self, data):
        name = data.get('name', '').strip()
        content = data.get('content', '')
        if not name:
            self.send_json({'success': False, 'error': 'Name is required'})
            return
        try:
            commands_dir = os.path.join(FACTORY_DIR, 'commands')
            real_dir = os.path.realpath(commands_dir)
            with open(os.path.join(real_dir, f'{name}.md'), 'w', encoding='utf-8') as f:
                f.write(content)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_rename_command(self, data):
        old_name = data.get('oldName', '').strip()
        new_name = data.get('newName', '').strip()
        if not old_name or not new_name:
            self.send_json({'success': False, 'error': 'Both old and new names are required'})
            return
        if old_name == new_name:
            self.send_json({'success': True})
            return
        try:
            commands_dir = os.path.join(FACTORY_DIR, 'commands')
            real_dir = os.path.realpath(commands_dir)
            old_path = os.path.join(real_dir, f'{old_name}.md')
            new_path = os.path.join(real_dir, f'{new_name}.md')
            if os.path.exists(new_path):
                self.send_json({'success': False, 'error': f'Command "{new_name}" already exists'})
                return
            os.rename(old_path, new_path)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    # Droids handlers
    def handle_list_droids(self):
        droids_dir = os.path.join(FACTORY_DIR, 'droids')
        droids = []
        try:
            real_dir = os.path.realpath(droids_dir)
            for entry in os.listdir(real_dir):
                if entry.endswith('.md'):
                    full_path = os.path.join(real_dir, entry)
                    if os.path.isfile(full_path):
                        droids.append({'name': entry[:-3], 'path': full_path})
        except:
            pass
        self.send_json(droids)
    
    def handle_create_droid(self, data):
        name = data.get('name', '').strip()
        if not name:
            self.send_json({'success': False, 'error': 'Name is required'})
            return
        try:
            droids_dir = os.path.join(FACTORY_DIR, 'droids')
            os.makedirs(droids_dir, exist_ok=True)
            droid_file = os.path.join(droids_dir, f'{name}.md')
            with open(droid_file, 'w') as f:
                f.write(f'---\nname: {name}\ndescription: A custom droid\n---\n\n# {name}\n\nDroid instructions here.\n')
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_delete_droid(self, data):
        name = data.get('name', '').strip()
        if not name:
            self.send_json({'success': False, 'error': 'Name is required'})
            return
        try:
            droid_file = os.path.join(FACTORY_DIR, 'droids', f'{name}.md')
            os.remove(droid_file)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    # MCP handlers
    def handle_list_mcp(self):
        mcp_file = os.path.join(FACTORY_DIR, 'mcp.json')
        servers = []
        try:
            with open(mcp_file, 'r') as f:
                config = json.load(f)
            if 'mcpServers' in config:
                for name, server in config['mcpServers'].items():
                    item = {'name': name, **server}
                    servers.append(item)
        except:
            pass
        self.send_json(servers)
    
    def handle_add_mcp(self, data):
        name = data.get('name', '').strip()
        command = data.get('command', '').strip()
        args = data.get('args', [])
        if not name or not command:
            self.send_json({'success': False, 'error': 'Name and command are required'})
            return
        try:
            mcp_file = os.path.join(FACTORY_DIR, 'mcp.json')
            config = {'mcpServers': {}}
            try:
                with open(mcp_file, 'r') as f:
                    config = json.load(f)
                if 'mcpServers' not in config:
                    config['mcpServers'] = {}
            except:
                pass
            config['mcpServers'][name] = {'command': command, 'args': args}
            os.makedirs(FACTORY_DIR, exist_ok=True)
            with open(mcp_file, 'w') as f:
                json.dump(config, f, indent=2)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_remove_mcp(self, data):
        name = data.get('name', '').strip()
        if not name:
            self.send_json({'success': False, 'error': 'Name is required'})
            return
        try:
            mcp_file = os.path.join(FACTORY_DIR, 'mcp.json')
            with open(mcp_file, 'r') as f:
                config = json.load(f)
            if 'mcpServers' in config and name in config['mcpServers']:
                del config['mcpServers'][name]
                with open(mcp_file, 'w') as f:
                    json.dump(config, f, indent=2)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_update_mcp(self, data):
        name = data.get('name', '').strip()
        server_config = data.get('config', {})
        if not name:
            self.send_json({'success': False, 'error': 'Name is required'})
            return
        try:
            mcp_file = os.path.join(FACTORY_DIR, 'mcp.json')
            config = {'mcpServers': {}}
            try:
                with open(mcp_file, 'r') as f:
                    config = json.load(f)
                if 'mcpServers' not in config:
                    config['mcpServers'] = {}
            except:
                pass
            config['mcpServers'][name] = server_config
            os.makedirs(FACTORY_DIR, exist_ok=True)
            with open(mcp_file, 'w') as f:
                json.dump(config, f, indent=2)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    # BYOK (Custom Models) handlers
    def _get_factory_config(self):
        """Read ~/.factory/config.json"""
        config_file = os.path.join(FACTORY_DIR, 'config.json')
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except:
            return {}
    
    def _save_factory_config(self, config):
        """Write ~/.factory/config.json"""
        config_file = os.path.join(FACTORY_DIR, 'config.json')
        os.makedirs(FACTORY_DIR, exist_ok=True)
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
    
    def handle_list_byok(self):
        config = self._get_factory_config()
        models = config.get('custom_models', [])
        self.send_json(models)
    
    def handle_remove_byok(self, data):
        index = data.get('index')
        if index is None:
            self.send_json({'success': False, 'error': 'Index is required'})
            return
        try:
            config = self._get_factory_config()
            models = config.get('custom_models', [])
            idx = int(index)
            if idx < 0 or idx >= len(models):
                self.send_json({'success': False, 'error': 'Index out of range'})
                return
            models.pop(idx)
            config['custom_models'] = models
            self._save_factory_config(config)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_update_byok(self, data):
        index = data.get('index')
        model_config = data.get('config')
        if model_config is None:
            self.send_json({'success': False, 'error': 'Config is required'})
            return
        try:
            config = self._get_factory_config()
            models = config.get('custom_models', [])
            if index is None or int(index) < 0:
                # Add new model (index is None or -1)
                models.append(model_config)
            else:
                idx = int(index)
                if idx >= len(models):
                    self.send_json({'success': False, 'error': 'Index out of range'})
                    return
                models[idx] = model_config
            config['custom_models'] = models
            self._save_factory_config(config)
            self.send_json({'success': True})
        except Exception as e:
            self.send_json({'success': False, 'error': str(e)})
    
    def handle_dk_config(self, data):
        """Get or set dk config (key=value format, same as dk CLI)"""
        config_file = os.path.join(self.oroio_dir, 'config')
        
        def parse_config(content):
            config = {}
            for line in content.split('\n'):
                if '=' in line:
                    key, val = line.split('=', 1)
                    config[key] = val
            return config
        
        def serialize_config(config):
            return '\n'.join(f'{k}={v}' for k, v in config.items())
        
        # If data is provided, it's a SET operation
        if data:
            try:
                existing = {}
                try:
                    with open(config_file, 'r') as f:
                        existing = parse_config(f.read())
                except:
                    pass
                existing.update(data)
                os.makedirs(self.oroio_dir, exist_ok=True)
                with open(config_file, 'w') as f:
                    f.write(serialize_config(existing))
                self.send_json({'success': True, 'config': existing})
            except Exception as e:
                self.send_json({'success': False, 'error': str(e)})
        else:
            # GET operation
            try:
                config = {}
                try:
                    with open(config_file, 'r') as f:
                        config = parse_config(f.read())
                except:
                    pass
                self.send_json(config)
            except Exception as e:
                self.send_json({'error': str(e)})
    
    def log_message(self, format, *args):
        pass

def run(port, web_dir, oroio_dir, dk_path, pin_hash=None):
    global PIN_HASH
    PIN_HASH = pin_hash
    os.chdir(web_dir)
    
    handler = lambda *args, **kwargs: OroioHandler(
        *args, oroio_dir=oroio_dir, dk_path=dk_path, **kwargs
    )
    
    with http.server.ThreadingHTTPServer(('0.0.0.0', port), handler) as httpd:
        httpd.serve_forever()

if __name__ == '__main__':
    if len(sys.argv) < 5:
        print('Usage: serve.py <port> <web_dir> <oroio_dir> <dk_path> [pin_hash]')
        sys.exit(1)
    
    port = int(sys.argv[1])
    web_dir = sys.argv[2]
    oroio_dir = sys.argv[3]
    dk_path = sys.argv[4]
    pin_hash = sys.argv[5] if len(sys.argv) > 5 else None
    run(port, web_dir, oroio_dir, dk_path, pin_hash)
