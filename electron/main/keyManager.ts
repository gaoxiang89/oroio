import * as fs from 'fs/promises';
import * as path from 'path';
import * as os from 'os';
import * as crypto from 'crypto';

const OROIO_DIR = path.join(os.homedir(), '.oroio');
const KEYS_FILE = path.join(OROIO_DIR, 'keys.enc');
const CURRENT_FILE = path.join(OROIO_DIR, 'current');
const CACHE_FILE = path.join(OROIO_DIR, 'list_cache.b64');

const SALT = 'oroio';
const PROFILE_URL = 'https://api.factory.ai/api/app/auth/me';
const LIMITS_URL = 'https://app.factory.ai/api/billing/limits';
const USAGE_URL = 'https://app.factory.ai/api/organization/members/chat-usage';

export interface KeyUsage {
  balance: number | null;
  total: number | null;
  used: number | null;
  expires: string;
  raw: string;
  mode?: string;
  display?: string;
  orgId?: string;
  email?: string;
  extraCents?: number;
  coreUsed?: number;
  overagePref?: string;
}

export interface KeyInfo {
  key: string;
  index: number;
  isCurrent: boolean;
  usage: KeyUsage | null;
}

function deriveKeyAndIV(salt: Buffer): { key: Buffer; iv: Buffer } {
  const iterations = 10000;
  const keyLength = 32;
  const ivLength = 16;
  
  const derived = crypto.pbkdf2Sync(SALT, salt, iterations, keyLength + ivLength, 'sha256');
  
  return {
    key: derived.subarray(0, keyLength),
    iv: derived.subarray(keyLength, keyLength + ivLength),
  };
}

export async function decryptKeys(encryptedData: Buffer): Promise<string[]> {
  const header = encryptedData.subarray(0, 8).toString('utf8');
  if (header !== 'Salted__') {
    throw new Error('Invalid encrypted file format');
  }
  
  const salt = encryptedData.subarray(8, 16);
  const ciphertext = encryptedData.subarray(16);
  
  const { key, iv } = deriveKeyAndIV(salt);
  
  const decipher = crypto.createDecipheriv('aes-256-cbc', key, iv);
  const decrypted = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  
  const text = decrypted.toString('utf8');
  return text.split('\n').filter(line => line.trim()).map(line => line.split('\t')[0]);
}

function encryptKeys(keys: string[]): Buffer {
  const salt = crypto.randomBytes(8);
  const { key, iv } = deriveKeyAndIV(salt);
  
  const cipher = crypto.createCipheriv('aes-256-cbc', key, iv);
  const text = keys.map(k => `${k}\t`).join('\n');
  const encrypted = Buffer.concat([cipher.update(text, 'utf8'), cipher.final()]);
  
  return Buffer.concat([Buffer.from('Salted__'), salt, encrypted]);
}

async function ensureStore(): Promise<void> {
  await fs.mkdir(OROIO_DIR, { recursive: true });
  // 如果 keys.enc 不存在，创建空的加密文件
  try {
    await fs.access(KEYS_FILE);
  } catch {
    const encrypted = encryptKeys([]);
    await fs.writeFile(KEYS_FILE, encrypted);
  }
}

async function invalidateCache(): Promise<void> {
  try {
    await fs.unlink(CACHE_FILE);
  } catch {
    // ignore if not exists
  }
}

async function saveKeys(keys: string[]): Promise<void> {
  await ensureStore();
  const encrypted = encryptKeys(keys);
  await fs.writeFile(KEYS_FILE, encrypted);
  await invalidateCache();
}

async function readEncryptedKeys(): Promise<Buffer> {
  return fs.readFile(KEYS_FILE);
}

async function readCurrentIndex(): Promise<number> {
  try {
    const content = await fs.readFile(CURRENT_FILE, 'utf8');
    return parseInt(content.trim(), 10) || 1;
  } catch {
    return 1;
  }
}

function parseUsageInfo(text: string): KeyUsage {
  const lines = text.split('\n');
  const data: Record<string, string> = {};
  
  for (const line of lines) {
    const [key, ...valueParts] = line.split('=');
    if (key) {
      data[key] = valueParts.join('=');
    }
  }
  
  return {
    balance: data['BALANCE_NUM'] ? parseFloat(data['BALANCE_NUM']) : null,
    total: data['TOTAL'] ? parseFloat(data['TOTAL']) : null,
    used: data['USED'] ? parseFloat(data['USED']) : null,
    expires: data['EXPIRES'] || '?',
    raw: data['RAW'] || '',
    mode: data['MODE'] || '',
    display: data['DISPLAY'] || '',
    orgId: data['ORG_ID'] || '',
    email: data['EMAIL'] || '',
    extraCents: data['EXTRA_CENTS'] ? parseFloat(data['EXTRA_CENTS']) : 0,
    coreUsed: data['CORE_USED'] ? parseFloat(data['CORE_USED']) : 0,
    overagePref: data['OVERAGE_PREF'] || '',
  };
}

async function readCache(): Promise<Map<number, KeyUsage>> {
  try {
    const content = await fs.readFile(CACHE_FILE, 'utf8');
    const lines = content.split('\n');
    if (lines.length < 3) return new Map();
    
    const result = new Map<number, KeyUsage>();
    
    for (let i = 2; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;
      const [idxStr, b64] = line.split('\t');
      if (!idxStr || !b64) continue;
      
      try {
        const decoded = Buffer.from(b64, 'base64').toString('utf8');
        const usage = parseUsageInfo(decoded);
        result.set(parseInt(idxStr, 10), usage);
      } catch {
        // skip invalid entries
      }
    }
    
    return result;
  } catch {
    return new Map();
  }
}

export async function getKeyList(): Promise<KeyInfo[]> {
  try {
    const [encryptedData, currentIndex, cache] = await Promise.all([
      readEncryptedKeys(),
      readCurrentIndex(),
      readCache(),
    ]);
    
    const keys = await decryptKeys(encryptedData);
    
    return keys.map((key, idx) => ({
      key,
      index: idx + 1,
      isCurrent: idx + 1 === currentIndex,
      usage: cache.get(idx) || null,
    }));
  } catch (error) {
    console.error('Failed to get key list:', error);
    return [];
  }
}

export async function getCurrentKey(): Promise<KeyInfo | null> {
  const keys = await getKeyList();
  return keys.find(k => k.isCurrent) || null;
}

const API_TIMEOUT = 8000;
const API_RETRIES = 3;

const WINDOW_KEYS: Array<[string, string]> = [
  ['fiveHour', '5h'],
  ['weekly', '7d'],
  ['monthly', '30d'],
];

function emptyUsage(): KeyUsage {
  return {
    balance: 0,
    total: 0,
    used: 0,
    expires: '?',
    raw: '',
    mode: '',
    display: '',
    orgId: '',
    email: '',
    extraCents: 0,
    coreUsed: 0,
    overagePref: '',
  };
}

function fmtPct(p: number): string {
  const r = Math.round(p);
  return Math.abs(p - r) < 0.05 ? String(r) : p.toFixed(1);
}

function parseIso(end: unknown): Date | null {
  if (!end) return null;
  const dt = new Date(String(end));
  return Number.isNaN(dt.getTime()) ? null : dt;
}

function windowPct(w: any, now: Date): { pct: number; rem: number | null; end: any } {
  if (!w || typeof w !== 'object') return { pct: 0, rem: null, end: null };
  let pct = Number(w.usedPercent ?? 0);
  if (!Number.isFinite(pct)) pct = 0;
  const rem = w.secondsRemaining == null ? null : Number(w.secondsRemaining);
  if (rem != null && Number.isFinite(rem) && rem <= 0) {
    return { pct: 0, rem, end: w.windowEnd };
  }
  if (rem == null) {
    const end = parseIso(w.windowEnd);
    if (end && end.getTime() <= now.getTime()) {
      return { pct: 0, rem, end: w.windowEnd };
    }
  }
  return { pct: Math.min(100, Math.max(0, pct)), rem, end: w.windowEnd };
}

function poolWindows(pool: any, now: Date) {
  return WINDOW_KEYS.map(([key, label]) => {
    const parsed = windowPct(pool?.[key], now);
    return { label, ...parsed };
  });
}

function bottleneck<T extends { pct: number; rem: number | null }>(windows: T[]): T {
  return [...windows].sort((a, b) => {
    if (b.pct !== a.pct) return b.pct - a.pct;
    const ar = a.rem ?? Number.POSITIVE_INFINITY;
    const br = b.rem ?? Number.POSITIVE_INFINITY;
    return ar - br;
  })[0];
}

function parseRateLimits(data: any): KeyUsage {
  const now = new Date();
  const limits = data?.limits || {};
  const std = poolWindows(limits.standard, now);
  const core = poolWindows(limits.core, now);
  const bot = bottleneck(std);
  const used = Math.round(bot.pct);
  let expires = '?';
  const end = parseIso(bot.end);
  if (end) {
    expires = end.toISOString().split('T')[0];
  } else if (bot.rem != null && Number.isFinite(bot.rem)) {
    expires = new Date(now.getTime() + bot.rem * 1000).toISOString().split('T')[0];
  }
  return {
    mode: 'rate',
    display: std.map(w => `${w.label} ${fmtPct(w.pct)}%`).join(' '),
    total: 100,
    used,
    balance: 100 - used,
    expires,
    raw: '',
    extraCents: Number(data?.extraUsageBalanceCents || 0),
    coreUsed: Math.round(bottleneck(core).pct),
    overagePref: data?.overagePreference || '',
  };
}

function parseTokenUsage(data: any): KeyUsage {
  const result = emptyUsage();
  result.mode = 'tokens';
  const usage = data?.usage;
  if (!usage) {
    result.raw = 'no_usage';
    return result;
  }
  const section = usage.standard || usage.premium || usage.total || usage.main;
  if (section) {
    const total = section.totalAllowance ?? section.basicAllowance ?? section.allowance;
    const used = section.orgTotalTokensUsed ?? section.used ?? section.tokensUsed ?? 0;
    if (total != null) {
      result.total = total;
      result.used = used;
      result.balance = total - used;
    }
  }
  const expRaw = usage.endDate ?? usage.expire_at ?? usage.expires_at;
  if (expRaw != null) {
    if (typeof expRaw === 'number' || /^\d+$/.test(String(expRaw))) {
      result.expires = new Date(Number(expRaw)).toISOString().split('T')[0];
    } else {
      result.expires = String(expRaw);
    }
  }
  return result;
}

async function fetchJson(url: string, key: string, signal: AbortSignal): Promise<{ ok: boolean; status: number; data?: any }> {
  const response = await fetch(url, {
    headers: {
      'Authorization': `Bearer ${key}`,
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
      'Accept': 'application/json',
    },
    signal,
  });
  if (!response.ok) {
    return { ok: false, status: response.status };
  }
  return { ok: true, status: response.status, data: await response.json() };
}

function cleanIdentity(value: unknown): string {
  if (value == null || typeof value === 'object') return '';
  return String(value).replace(/[\r\n]+/g, ' ').trim();
}

async function fetchIdentity(key: string): Promise<Pick<KeyUsage, 'orgId' | 'email'>> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 6000);
  try {
    const profile = await fetchJson(PROFILE_URL, key, controller.signal);
    if (!profile.ok) return { orgId: '', email: '' };
    const data = profile.data || {};
    const organization = data.organization || {};
    const user = data.userProfile || data.user || {};
    return {
      orgId: cleanIdentity(organization.id || data.organizationId || data.organization_id),
      email: cleanIdentity(user.email || data.email),
    };
  } catch {
    return { orgId: '', email: '' };
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchUsage(key: string): Promise<KeyUsage> {
  const identity = await fetchIdentity(key);
  const result = { ...emptyUsage(), ...identity };

  for (let attempt = 1; attempt <= API_RETRIES; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), API_TIMEOUT);
    try {
      const limits = await fetchJson(LIMITS_URL, key, controller.signal);
      clearTimeout(timeout);
      if (limits.ok) {
        if (limits.data?.usesTokenRateLimitsBilling && limits.data?.limits != null) {
          return { ...parseRateLimits(limits.data), ...identity };
        }
        break;
      }
      if (limits.status === 404) break;
      if (limits.status >= 400 && limits.status < 500) {
        result.raw = `http_${limits.status}`;
        result.expires = 'Invalid key';
        return result;
      }
      if (attempt < API_RETRIES) {
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      break;
    } catch {
      clearTimeout(timeout);
      if (attempt < API_RETRIES) {
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      break;
    }
  }

  for (let attempt = 1; attempt <= API_RETRIES; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), API_TIMEOUT);
    try {
      const usage = await fetchJson(USAGE_URL, key, controller.signal);
      clearTimeout(timeout);
      if (usage.ok) {
        return { ...parseTokenUsage(usage.data), ...identity };
      }
      if (usage.status >= 400 && usage.status < 500) {
        result.raw = `http_${usage.status}`;
        result.expires = 'Invalid key';
        return result;
      }
      if (attempt < API_RETRIES) {
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      result.raw = `http_${usage.status}`;
      result.expires = 'Error';
      return result;
    } catch {
      clearTimeout(timeout);
      if (attempt < API_RETRIES) {
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      result.raw = 'fetch_error';
      result.expires = 'Error';
    }
  }

  return result;
}

async function writeCache(keys: string[], usages: KeyUsage[]): Promise<void> {
  await ensureStore();
  const now = Math.floor(Date.now() / 1000);
  const keysHash = crypto.createHash('sha1').update(await fs.readFile(KEYS_FILE)).digest('hex');
  
  const lines = [String(now), keysHash];
  for (let i = 0; i < usages.length; i++) {
    const u = usages[i];
    const info = [
      `ORG_ID=${u.orgId ?? ''}`,
      `EMAIL=${u.email ?? ''}`,
      `MODE=${u.mode ?? ''}`,
      `DISPLAY=${u.display ?? ''}`,
      `BALANCE=${u.balance ?? 0}`,
      `BALANCE_NUM=${u.balance ?? 0}`,
      `TOTAL=${u.total ?? 0}`,
      `USED=${u.used ?? 0}`,
      `EXPIRES=${u.expires}`,
      `EXTRA_CENTS=${u.extraCents ?? 0}`,
      `CORE_USED=${u.coreUsed ?? 0}`,
      `OVERAGE_PREF=${u.overagePref ?? ''}`,
      `RAW=${u.raw}`,
    ].join('\n');
    const b64 = Buffer.from(info).toString('base64');
    lines.push(`${i}\t${b64}`);
  }
  
  await fs.writeFile(CACHE_FILE, lines.join('\n'));
}

export async function addKey(key: string): Promise<{ success: boolean; message?: string; error?: string }> {
  try {
    await ensureStore();
    let existingKeys: string[] = [];
    let existingCache = new Map<number, KeyUsage>();
    
    try {
      const data = await readEncryptedKeys();
      existingKeys = await decryptKeys(data);
      existingCache = await readCache();
    } catch {
      // no existing keys
    }
    
    const newKeys = [...existingKeys, key];
    
    // Encrypt and save without invalidating cache
    const encrypted = encryptKeys(newKeys);
    await fs.writeFile(KEYS_FILE, encrypted);
    
    // Fetch usage for new key and append to cache
    const newUsage = await fetchUsage(key);
    const usages: KeyUsage[] = [];
    for (let i = 0; i < existingKeys.length; i++) {
      usages.push(existingCache.get(i) || { balance: null, total: null, used: null, expires: '?', raw: '' });
    }
    usages.push(newUsage);
    await writeCache(newKeys, usages);
    
    return { success: true, message: `已添加。当前共有 ${newKeys.length} 个key。` };
  } catch (error: any) {
    return { success: false, error: error.message || 'Failed to add key' };
  }
}

export async function removeKey(index: number): Promise<{ success: boolean; message?: string; error?: string }> {
  try {
    const data = await readEncryptedKeys();
    const keys = await decryptKeys(data);
    
    if (index < 1 || index > keys.length) {
      return { success: false, error: '序号超出范围' };
    }
    
    const newKeys = keys.filter((_, i) => i + 1 !== index);
    await saveKeys(newKeys);
    await fs.writeFile(CURRENT_FILE, '1');
    
    return { success: true, message: `已删除，剩余 ${newKeys.length} 个key。` };
  } catch (error: any) {
    return { success: false, error: error.message || 'Failed to remove key' };
  }
}

export async function useKey(index: number): Promise<{ success: boolean; message?: string; error?: string }> {
  try {
    const data = await readEncryptedKeys();
    const keys = await decryptKeys(data);
    
    if (index < 1 || index > keys.length) {
      return { success: false, error: '序号超出范围' };
    }
    
    await ensureStore();
    await fs.writeFile(CURRENT_FILE, String(index));
    
    return { success: true, message: `已切换到序号 ${index}` };
  } catch (error: any) {
    return { success: false, error: error.message || 'Failed to switch key' };
  }
}

export async function refreshCache(): Promise<{ success: boolean; error?: string }> {
  try {
    const data = await readEncryptedKeys();
    const keys = await decryptKeys(data);
    
    const usages = await Promise.all(keys.map(k => fetchUsage(k)));
    await writeCache(keys, usages);
    
    return { success: true };
  } catch (error: any) {
    return { success: false, error: error.message || 'Failed to refresh' };
  }
}

export function maskKey(key: string): string {
  const prefix = key.slice(0, 6).padEnd(6, 'x');
  const suffix = key.length > 10 ? key.slice(-4) : 'xxxx';
  return prefix + '...' + suffix;
}
