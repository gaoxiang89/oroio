import { contextBridge, ipcRenderer } from 'electron';

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
}

export interface CustomModel {
  model_display_name?: string;
  model: string;
  base_url: string;
  api_key: string;
  provider: 'anthropic' | 'openai' | 'generic-chat-completion-api';
  max_tokens?: number;
  supports_images?: boolean;
  reasoning_effort?: string;
  enable_thinking?: boolean;
  thinking_max_tokens?: number;
  base_model_id?: string;
  extra_args?: Record<string, unknown>;
  extra_headers?: Record<string, string>;
}

export interface ByokProvider {
  id: 'glm' | 'deepseek' | 'kimi';
  name: string;
  description: string;
  modelsUrl: string;
  baseUrl: string;
  droidProvider: 'anthropic' | 'generic-chat-completion-api';
  configured: boolean;
  managedModelIds: string[];
  unavailableModelIds: string[];
  lastDiscoveredAt?: string;
}

export interface ByokModel {
  id: string;
  displayName: string;
  contextLength?: number;
  maxOutputTokens?: number;
  supportsImages?: boolean;
  supportsReasoning?: boolean;
  recommended?: boolean;
  selected?: boolean;
  isNew?: boolean;
  unavailable?: boolean;
  reasoningEffort?: string;
  reasoningEfforts?: string[];
  defaultReasoningEffort?: string;
}

export interface ByokDiscovery {
  success: true;
  provider: string;
  configured: boolean;
  models: ByokModel[];
  recommendedModelIds: string[];
  managedModelIds: string[];
}

export interface ByokApplyResult {
  success: true;
  provider: string;
  managedModelIds: string[];
  unavailableModelIds: string[];
  models?: ByokModel[];
}

export interface ByokExportPayload {
  version: number;
  exportedAt: string;
  providers: Record<string, unknown>;
}

export interface ByokExportResult {
  success: true;
  payload: ByokExportPayload;
  providers: Record<string, { managedModelIds: string[] }>;
}

export interface ByokImportResult {
  success: true;
  importedProviders: string[];
  skippedProviders: string[];
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

export interface OroioAPI {
  keys: {
    list: () => Promise<any[]>;
    current: () => Promise<any | null>;
    add: (key: string) => Promise<{ success: boolean; message?: string; error?: string }>;
    remove: (index: number) => Promise<{ success: boolean; message?: string; error?: string }>;
    use: (index: number) => Promise<{ success: boolean; message?: string; error?: string }>;
    refresh: () => Promise<{ success: boolean; error?: string }>;
  };
  data: {
    read: (filename: string) => Promise<ArrayBuffer | null>;
  };
  on: (channel: string, callback: (...args: any[]) => void) => () => void;
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
  listByokProviders: () => Promise<ByokProvider[]>;
  discoverByok: (provider: string, apiKey: string) => Promise<ByokDiscovery>;
  applyByok: (provider: string, apiKey: string, modelIds: string[]) => Promise<ByokApplyResult>;
  refreshByokProvider: (provider: string) => Promise<ByokApplyResult>;
  removeByokProvider: (provider: string) => Promise<{ success: true; provider: string; removedModelIds: string[] }>;
  discoverOpenAICompatible: (baseUrl: string, apiKey: string) => Promise<ByokDiscovery & { baseUrl: string }>;
  applyOpenAICompatible: (baseUrl: string, apiKey: string, modelIds: string[]) => Promise<ByokApplyResult & { baseUrl: string }>;
  exportByokConfig: () => Promise<ByokExportResult>;
  importByokConfig: (payload: unknown, force: boolean) => Promise<ByokImportResult>;
  // Utilities
  openPath: (path: string) => Promise<void>;
}

const api: OroioAPI = {
  keys: {
    list: () => ipcRenderer.invoke('keys:list'),
    current: () => ipcRenderer.invoke('keys:current'),
    add: (key: string) => ipcRenderer.invoke('keys:add', key),
    remove: (index: number) => ipcRenderer.invoke('keys:remove', index),
    use: (index: number) => ipcRenderer.invoke('keys:use', index),
    refresh: () => ipcRenderer.invoke('keys:refresh'),
  },
  data: {
    read: async (filename: string): Promise<ArrayBuffer | null> => {
      const buffer = await ipcRenderer.invoke('data:read', filename);
      if (buffer) {
        return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
      }
      return null;
    },
  },
  on: (channel: string, callback: (...args: any[]) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, ...args: any[]) => callback(...args);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  },
  // dk CLI check
  checkDk: () => ipcRenderer.invoke('dk:check'),
  getDkConfig: () => ipcRenderer.invoke('dk:getConfig'),
  setDkConfig: (config: Partial<DkConfig>) => ipcRenderer.invoke('dk:setConfig', config),
  selectPath: (type: 'file' | 'directory') => ipcRenderer.invoke('dk:selectPath', type),
  // Skills
  listSkills: () => ipcRenderer.invoke('skills:list'),
  createSkill: (name: string) => ipcRenderer.invoke('skills:create', name),
  deleteSkill: (name: string) => ipcRenderer.invoke('skills:delete', name),
  // Commands
  listCommands: () => ipcRenderer.invoke('commands:list'),
  createCommand: (name: string) => ipcRenderer.invoke('commands:create', name),
  deleteCommand: (name: string) => ipcRenderer.invoke('commands:delete', name),
  getCommandContent: (name: string) => ipcRenderer.invoke('commands:content', name),
  updateCommand: (name: string, content: string) => ipcRenderer.invoke('commands:update', name, content),
  renameCommand: (oldName: string, newName: string) => ipcRenderer.invoke('commands:rename', oldName, newName),
  // Droids
  listDroids: () => ipcRenderer.invoke('droids:list'),
  createDroid: (name: string) => ipcRenderer.invoke('droids:create', name),
  deleteDroid: (name: string) => ipcRenderer.invoke('droids:delete', name),
  // MCP
  listMcpServers: () => ipcRenderer.invoke('mcp:list'),
  addMcpServer: (name: string, command: string, args: string[]) => ipcRenderer.invoke('mcp:add', name, command, args),
  removeMcpServer: (name: string) => ipcRenderer.invoke('mcp:remove', name),
  updateMcpServer: (name: string, config: Omit<McpServer, 'name'>) => ipcRenderer.invoke('mcp:update', name, config),
  openMcpConfig: () => ipcRenderer.invoke('mcp:openConfig'),
  // BYOK (Custom Models)
  listCustomModels: () => ipcRenderer.invoke('byok:list'),
  removeCustomModel: (index: number) => ipcRenderer.invoke('byok:remove', index),
  updateCustomModel: (index: number, config: CustomModel) => ipcRenderer.invoke('byok:update', index, config),
  listByokProviders: () => ipcRenderer.invoke('byok:providers'),
  discoverByok: (provider: string, apiKey: string) => ipcRenderer.invoke('byok:discover', provider, apiKey),
  applyByok: (provider: string, apiKey: string, modelIds: string[]) => ipcRenderer.invoke('byok:apply', provider, apiKey, modelIds),
  refreshByokProvider: (provider: string) => ipcRenderer.invoke('byok:refresh-provider', provider),
  removeByokProvider: (provider: string) => ipcRenderer.invoke('byok:remove-provider', provider),
  discoverOpenAICompatible: (baseUrl: string, apiKey: string) => ipcRenderer.invoke('byok:openai-compatible:discover', baseUrl, apiKey),
  applyOpenAICompatible: (baseUrl: string, apiKey: string, modelIds: string[]) => ipcRenderer.invoke('byok:openai-compatible:apply', baseUrl, apiKey, modelIds),
  exportByokConfig: () => ipcRenderer.invoke('byok:export'),
  importByokConfig: (payload: unknown, force: boolean) => ipcRenderer.invoke('byok:import', payload, force),
  // Utilities
  openPath: (p: string) => ipcRenderer.invoke('util:openPath', p),
};

contextBridge.exposeInMainWorld('oroio', api);

declare global {
  interface Window {
    oroio: OroioAPI;
  }
}
