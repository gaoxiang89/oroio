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
}

export interface KeyInfo {
  key: string;
  index: number;
  isCurrent: boolean;
  usage: KeyUsage | null;
}

// Detect if running in Electron
export const isElectron = typeof window !== 'undefined' && 'oroio' in window;

// Auth token management
const AUTH_TOKEN_KEY = 'oroio-auth-token';

export function getAuthToken(): string | null {
  return sessionStorage.getItem(AUTH_TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  sessionStorage.setItem(AUTH_TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
}

function getAuthHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { 'X-Auth-Token': token } : {};
}

// Auth API
export interface AuthCheckResult {
  required: boolean;
  authenticated: boolean;
}

export interface DkCheckResult {
  installed: boolean;
  installCmd: string;
  platform: string;
}

export interface DkConfig {
  ascii?: string;
  [key: string]: string | undefined;
}

export async function checkAuth(): Promise<AuthCheckResult> {
  if (isElectron) {
    return { required: false, authenticated: true };
  }
  const res = await fetch('/api/auth/check', {
    method: 'POST',
    headers: { ...getAuthHeaders() },
  });
  return res.json();
}

export async function authenticate(pin: string): Promise<{ success: boolean; token?: string; error?: string }> {
  if (isElectron) {
    return { success: true };
  }
  const res = await fetch('/api/auth', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pin }),
  });
  const data = await res.json();
  if (data.success && data.token) {
    setAuthToken(data.token);
  }
  return data;
}

export async function fetchEncryptedKeys(): Promise<ArrayBuffer> {
  if (isElectron) {
    const data = await window.oroio.data.read('keys.enc');
    if (!data) throw new Error('Failed to read keys.enc');
    return data;
  }
  const res = await fetch('/data/keys.enc');
  if (!res.ok) throw new Error('Failed to fetch keys.enc');
  return res.arrayBuffer();
}

export async function fetchCurrentIndex(): Promise<number> {
  if (isElectron) {
    const data = await window.oroio.data.read('current');
    if (!data) return 1;
    const text = new TextDecoder().decode(data);
    return parseInt(text.trim(), 10) || 1;
  }
  const res = await fetch('/data/current');
  if (!res.ok) return 1;
  const text = await res.text();
  return parseInt(text.trim(), 10) || 1;
}

export async function fetchCache(): Promise<Map<number, KeyUsage>> {
  let text: string;
  
  if (isElectron) {
    const data = await window.oroio.data.read('list_cache.b64');
    if (!data) return new Map();
    text = new TextDecoder().decode(data);
  } else {
    const res = await fetch('/data/list_cache.b64');
    if (!res.ok) return new Map();
    text = await res.text();
  }
  
  const lines = text.split('\n');
  if (lines.length < 3) return new Map();
  
  const result = new Map<number, KeyUsage>();
  
  for (let i = 2; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const [idxStr, b64] = line.split('\t');
    if (!idxStr || !b64) continue;
    
    try {
      const decoded = atob(b64);
      const usage = parseUsageInfo(decoded);
      result.set(parseInt(idxStr, 10), usage);
    } catch {
      // skip invalid entries
    }
  }
  
  return result;
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
  };
}

export async function addKey(key: string): Promise<{ success: boolean; message?: string; error?: string }> {
  if (isElectron) {
    return window.oroio.keys.add(key);
  }
  const res = await fetch('/api/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ key }),
  });
  return res.json();
}

export async function removeKey(index: number): Promise<{ success: boolean; message?: string; error?: string }> {
  if (isElectron) {
    return window.oroio.keys.remove(index);
  }
  const res = await fetch('/api/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ index }),
  });
  return res.json();
}

export async function useKey(index: number): Promise<{ success: boolean; message?: string; error?: string }> {
  if (isElectron) {
    return window.oroio.keys.use(index);
  }
  const res = await fetch('/api/use', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ index }),
  });
  return res.json();
}

export async function refreshCache(): Promise<{ success: boolean }> {
  if (isElectron) {
    return window.oroio.keys.refresh();
  }
  const res = await fetch('/api/refresh', { method: 'POST', headers: getAuthHeaders() });
  return res.json();
}

export interface Skill {
  name: string;
  path: string;
}

export interface Command {
  name: string;
  path: string;
  description?: string;
  content?: string;
}

export interface Droid {
  name: string;
  path: string;
}

export interface McpServer {
  name: string;
  type?: 'stdio' | 'http';
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
  disabled?: boolean;
  alwaysAllow?: string[];
  [key: string]: unknown;
}

export interface CustomModel {
  model_display_name?: string;
  model: string;
  base_url: string;
  api_key: string;
  provider: 'anthropic' | 'openai' | 'generic-chat-completion-api';
  max_tokens?: number;
  supports_images?: boolean;
  extra_args?: Record<string, unknown>;
  extra_headers?: Record<string, string>;
}

// Skills API
export async function listSkills(): Promise<Skill[]> {
  if (isElectron) {
    return window.oroio.listSkills();
  }
  const res = await fetch('/api/skills/list', { method: 'POST', headers: getAuthHeaders() });
  return res.json();
}

export async function createSkill(name: string): Promise<void> {
  if (isElectron) {
    return window.oroio.createSkill(name);
  }
  const res = await fetch('/api/skills/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

export async function deleteSkill(name: string): Promise<void> {
  if (isElectron) {
    return window.oroio.deleteSkill(name);
  }
  const res = await fetch('/api/skills/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

// Commands API
export async function listCommands(): Promise<Command[]> {
  if (isElectron) {
    return window.oroio.listCommands();
  }
  const res = await fetch('/api/commands/list', { method: 'POST', headers: getAuthHeaders() });
  return res.json();
}

export async function createCommand(name: string): Promise<void> {
  if (isElectron) {
    return window.oroio.createCommand(name);
  }
  const res = await fetch('/api/commands/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

export async function deleteCommand(name: string): Promise<void> {
  if (isElectron) {
    return window.oroio.deleteCommand(name);
  }
  const res = await fetch('/api/commands/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

export async function getCommandContent(name: string): Promise<string> {
  if (isElectron) {
    return window.oroio.getCommandContent(name);
  }
  const res = await fetch('/api/commands/content', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data.content;
}

export async function updateCommand(name: string, content: string): Promise<void> {
  if (isElectron) {
    return window.oroio.updateCommand(name, content);
  }
  const res = await fetch('/api/commands/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name, content }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

export async function renameCommand(oldName: string, newName: string): Promise<void> {
  if (isElectron) {
    return window.oroio.renameCommand(oldName, newName);
  }
  const res = await fetch('/api/commands/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ oldName, newName }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

// Droids API
export async function listDroids(): Promise<Droid[]> {
  if (isElectron) {
    return window.oroio.listDroids();
  }
  const res = await fetch('/api/droids/list', { method: 'POST', headers: getAuthHeaders() });
  return res.json();
}

export async function createDroid(name: string): Promise<void> {
  if (isElectron) {
    return window.oroio.createDroid(name);
  }
  const res = await fetch('/api/droids/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

export async function deleteDroid(name: string): Promise<void> {
  if (isElectron) {
    return window.oroio.deleteDroid(name);
  }
  const res = await fetch('/api/droids/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

// MCP API
export async function listMcpServers(): Promise<McpServer[]> {
  if (isElectron) {
    return window.oroio.listMcpServers();
  }
  const res = await fetch('/api/mcp/list', { method: 'POST', headers: getAuthHeaders() });
  return res.json();
}

export async function addMcpServer(name: string, command: string, args: string[]): Promise<void> {
  if (isElectron) {
    return window.oroio.addMcpServer(name, command, args);
  }
  const res = await fetch('/api/mcp/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name, command, args }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

export async function removeMcpServer(name: string): Promise<void> {
  if (isElectron) {
    return window.oroio.removeMcpServer(name);
  }
  const res = await fetch('/api/mcp/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

export async function updateMcpServer(name: string, config: Omit<McpServer, 'name'>): Promise<void> {
  if (isElectron) {
    return window.oroio.updateMcpServer(name, config);
  }
  const res = await fetch('/api/mcp/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ name, config }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

// BYOK (Custom Models) API
export async function listCustomModels(): Promise<CustomModel[]> {
  if (isElectron) {
    return window.oroio.listCustomModels();
  }
  const res = await fetch('/api/byok/list', { method: 'POST', headers: getAuthHeaders() });
  return res.json();
}

export async function removeCustomModel(index: number): Promise<void> {
  if (isElectron) {
    return window.oroio.removeCustomModel(index);
  }
  const res = await fetch('/api/byok/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ index }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

export async function updateCustomModel(index: number, config: CustomModel): Promise<void> {
  if (isElectron) {
    return window.oroio.updateCustomModel(index, config);
  }
  const res = await fetch('/api/byok/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ index, config }),
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error);
}

// dk CLI check (Electron only)
export async function checkDk(): Promise<DkCheckResult | null> {
  if (!isElectron) {
    return null;
  }
  return window.oroio.checkDk();
}

// dk config
export async function getDkConfig(): Promise<DkConfig | null> {
  if (isElectron) {
    return window.oroio.getDkConfig();
  }
  // Use HTTP API
  try {
    const res = await fetch('/api/dk/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    });
    return await res.json();
  } catch {
    return null;
  }
}

export async function setDkConfig(config: Partial<DkConfig>): Promise<void> {
  if (isElectron) {
    return window.oroio.setDkConfig(config);
  }
  // Use HTTP API
  await fetch('/api/dk/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(config),
  });
}

export async function selectPath(type: 'file' | 'directory'): Promise<string | null> {
  if (isElectron) {
    return window.oroio.selectPath(type);
  }
  // In web mode, prompt user to enter path manually
  const label = type === 'directory' ? 'folder' : 'file';
  return prompt(`Enter ${label} path:`);
}

// Type declaration for Electron window
declare global {
  interface Window {
    oroio: {
      keys: {
        list: () => Promise<KeyInfo[]>;
        current: () => Promise<KeyInfo | null>;
        add: (key: string) => Promise<{ success: boolean; message?: string; error?: string }>;
        remove: (index: number) => Promise<{ success: boolean; message?: string; error?: string }>;
        use: (index: number) => Promise<{ success: boolean; message?: string; error?: string }>;
        refresh: () => Promise<{ success: boolean; error?: string }>;
      };
      data: {
        read: (filename: string) => Promise<ArrayBuffer | null>;
      };
      on: (channel: string, callback: (...args: unknown[]) => void) => () => void;
      // dk CLI check
      checkDk: () => Promise<DkCheckResult>;
      getDkConfig: () => Promise<DkConfig>;
      setDkConfig: (config: Partial<DkConfig>) => Promise<void>;
      selectPath: (type: 'file' | 'directory') => Promise<string | null>;
      // Skills
      listSkills: () => Promise<Skill[]>;
      createSkill: (name: string) => Promise<void>;
      deleteSkill: (name: string) => Promise<void>;
      // Commands
      listCommands: () => Promise<Command[]>;
      createCommand: (name: string) => Promise<void>;
      deleteCommand: (name: string) => Promise<void>;
      getCommandContent: (name: string) => Promise<string>;
      updateCommand: (name: string, content: string) => Promise<void>;
      renameCommand: (oldName: string, newName: string) => Promise<void>;
      // Droids
      listDroids: () => Promise<Droid[]>;
      createDroid: (name: string) => Promise<void>;
      deleteDroid: (name: string) => Promise<void>;
      // MCP
      listMcpServers: () => Promise<McpServer[]>;
      addMcpServer: (name: string, command: string, args: string[]) => Promise<void>;
      removeMcpServer: (name: string) => Promise<void>;
      updateMcpServer: (name: string, config: Omit<McpServer, 'name'>) => Promise<void>;
      openMcpConfig: () => Promise<void>;
      // BYOK (Custom Models)
      listCustomModels: () => Promise<CustomModel[]>;
      removeCustomModel: (index: number) => Promise<void>;
      updateCustomModel: (index: number, config: CustomModel) => Promise<void>;
      // Utilities
      openPath: (path: string) => Promise<void>;
    };
  }
}
