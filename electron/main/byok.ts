import * as fs from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import { createHash, randomUUID } from 'crypto';

const FACTORY_DIR = path.join(os.homedir(), '.factory');
const OROIO_DIR = path.join(os.homedir(), '.oroio');
const SETTINGS_PATH = path.join(FACTORY_DIR, 'settings.json');
const LEGACY_PATH = path.join(FACTORY_DIR, 'config.json');
const STATE_PATH = path.join(OROIO_DIR, 'byok.json');
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 12_000;
const REASONING_EFFORTS = new Set([
  'default', 'none', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max',
]);
const MODEL_REASONING_PROFILES: Record<string, { efforts: string[]; defaultEffort: string }> = {
  'gpt-5-2025-08-07': { efforts: ['low', 'medium', 'high'], defaultEffort: 'medium' },
  'gpt-5-mini-2025-08-07': { efforts: ['low', 'medium', 'high'], defaultEffort: 'medium' },
  'gpt-5-nano-2025-08-07': { efforts: ['low', 'medium', 'high'], defaultEffort: 'medium' },
  'gpt-5-codex': { efforts: ['low', 'medium', 'high'], defaultEffort: 'medium' },
  'gpt-5.1': { efforts: ['none', 'low', 'medium', 'high'], defaultEffort: 'none' },
  'gpt-5.1-codex': { efforts: ['low', 'medium', 'high'], defaultEffort: 'medium' },
  'gpt-5.1-codex-max': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'medium' },
  'gpt-5.2': { efforts: ['off', 'low', 'medium', 'high', 'xhigh'], defaultEffort: 'low' },
  'gpt-5.2-codex': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'medium' },
  'gpt-5.3-codex': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'medium' },
  'gpt-5.3-codex-fast': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'medium' },
  'gpt-5.4': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'medium' },
  'gpt-5.4-fast': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'medium' },
  'gpt-5.4-mini': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'high' },
  'gpt-5.4-mini-fast': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'high' },
  'gpt-5.5': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'medium' },
  'gpt-5.5-fast': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'medium' },
  'gpt-5.5-pro': { efforts: ['medium', 'high', 'xhigh'], defaultEffort: 'medium' },
  'gpt-5.6-sol': { efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'], defaultEffort: 'medium' },
  'gpt-5.6-sol-fast': { efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'], defaultEffort: 'medium' },
  'gpt-5.6-terra': { efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'], defaultEffort: 'medium' },
  'gpt-5.6-terra-flex': { efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'], defaultEffort: 'medium' },
  'gpt-5.6-luna': { efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'], defaultEffort: 'medium' },
  'gpt-5.6-luna-flex': { efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'], defaultEffort: 'medium' },
  'grok-4.6': { efforts: ['low', 'medium', 'high', 'xhigh'], defaultEffort: 'high' },
  'glm-4.6': { efforts: ['none'], defaultEffort: 'none' },
  'glm-4.7': { efforts: ['none'], defaultEffort: 'none' },
  'glm-5': { efforts: ['none'], defaultEffort: 'none' },
  'glm-5.1': { efforts: ['off', 'high'], defaultEffort: 'high' },
  'glm-5.2': { efforts: ['off', 'high', 'max'], defaultEffort: 'high' },
  'glm-5.2-fast': { efforts: ['off', 'high', 'max'], defaultEffort: 'high' },
  'glm-5.3': { efforts: ['low', 'high', 'max'], defaultEffort: 'max' },
  'glm-5.3-flash': { efforts: ['low', 'high', 'max'], defaultEffort: 'high' },
  'kimi-k2.5': { efforts: ['off', 'high'], defaultEffort: 'high' },
  'kimi-k2.6': { efforts: ['off', 'high'], defaultEffort: 'high' },
  'kimi-k2.7-code': { efforts: ['off', 'high'], defaultEffort: 'high' },
  'kimi-k3': { efforts: ['off', 'low', 'high', 'max'], defaultEffort: 'high' },
  'deepseek-v4.1-flash': { efforts: ['off', 'low', 'high', 'max'], defaultEffort: 'high' },
  'deepseek-v4-flash-0731': { efforts: ['off', 'low', 'high', 'max'], defaultEffort: 'high' },
  'deepseek-v4-pro': { efforts: ['off', 'low', 'high', 'max'], defaultEffort: 'high' },
};

type Source = 'settings' | 'legacy';
type JsonObject = Record<string, unknown>;

interface RuntimeProvider {
  id: string;
  name: string;
  modelsUrl: string;
  baseUrl: string;
  droidProvider: 'anthropic' | 'generic-chat-completion-api';
  authMode?: 'bearer';
}

export interface TrustedProvider extends RuntimeProvider {
  id: 'glm' | 'deepseek' | 'kimi';
  description: string;
}

export interface ProviderStatus extends TrustedProvider {
  configured: boolean;
  managedModelIds: string[];
  unavailableModelIds: string[];
  lastDiscoveredAt?: string;
}

export interface DiscoveredModel {
  id: string;
  displayName: string;
  contextLength?: number;
  maxOutputTokens?: number;
  supportsImages?: boolean;
  supportsReasoning?: boolean;
  recommended?: boolean;
  created?: number;
  selected?: boolean;
  isNew?: boolean;
  unavailable?: boolean;
  reasoningEffort?: string;
  reasoningEfforts?: string[];
  defaultReasoningEffort?: string;
}

export interface DiscoveryResult {
  success: true;
  provider: string;
  configured: boolean;
  models: DiscoveredModel[];
  recommendedModelIds: string[];
  managedModelIds: string[];
}

export interface ApplyResult {
  success: true;
  provider: string;
  managedModelIds: string[];
  unavailableModelIds: string[];
  models?: DiscoveredModel[];
}

interface ManagedProviderState {
  managedModels?: string[];
  locations?: Record<string, Source>;
  knownModels?: DiscoveredModel[];
  lastDiscoveredAt?: string;
  unavailable?: string[];
  baseUrl?: string;
}

interface StateFile extends JsonObject {
  version: number;
  providers: Record<string, ManagedProviderState>;
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
  [key: string]: unknown;
}

export class ByokError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = 'ByokError';
    this.code = code;
  }
}

export const TRUSTED_PROVIDERS: Record<string, TrustedProvider> = {
  glm: {
    id: 'glm',
    name: 'GLM Coding Plan',
    description: 'Zhipu GLM models available to your Coding Plan key.',
    modelsUrl: 'https://open.bigmodel.cn/api/coding/paas/v4/models',
    baseUrl: 'https://open.bigmodel.cn/api/anthropic',
    droidProvider: 'anthropic',
    authMode: 'bearer',
  },
  deepseek: {
    id: 'deepseek',
    name: 'DeepSeek API',
    description: 'Models available from the official DeepSeek API.',
    modelsUrl: 'https://api.deepseek.com/models',
    baseUrl: 'https://api.deepseek.com/anthropic',
    droidProvider: 'anthropic',
    authMode: 'bearer',
  },
  kimi: {
    id: 'kimi',
    name: 'Kimi Code Plan',
    description: 'Moonshot Kimi models available to your Code Plan key.',
    modelsUrl: 'https://api.kimi.com/coding/v1/models',
    baseUrl: 'https://api.kimi.com/coding/v1',
    droidProvider: 'generic-chat-completion-api',
  },
};

function providerFor(providerId: string): TrustedProvider {
  const provider = TRUSTED_PROVIDERS[providerId.toLowerCase()];
  if (!provider) throw new ByokError('unknown_provider', 'Unknown provider. Choose glm, deepseek, or kimi.');
  return provider;
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function readJson(filePath: string, fallback: JsonObject): Promise<JsonObject> {
  try {
    const value: unknown = JSON.parse(await fs.readFile(filePath, 'utf-8'));
    if (!isObject(value)) throw new Error('not an object');
    return value;
  } catch (error) {
    const nodeError = error as NodeJS.ErrnoException;
    if (nodeError.code === 'ENOENT') return { ...fallback };
    throw new ByokError('invalid_config', `Cannot read valid JSON from ${filePath}.`);
  }
}

async function exists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function atomicWriteJson(filePath: string, value: JsonObject): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const tempPath = path.join(path.dirname(filePath), `.${path.basename(filePath)}.${randomUUID()}`);
  try {
    await fs.writeFile(tempPath, `${JSON.stringify(value, null, 2)}\n`, { encoding: 'utf-8', mode: 0o600 });
    await fs.rename(tempPath, filePath);
    if (process.platform !== 'win32') await fs.chmod(filePath, 0o600);
  } catch (error) {
    await fs.unlink(tempPath).catch(() => undefined);
    throw error;
  }
}

async function readState(): Promise<StateFile> {
  const raw = await readJson(STATE_PATH, { version: 1, providers: {} });
  return {
    ...raw,
    version: 1,
    providers: isObject(raw.providers) ? raw.providers as Record<string, ManagedProviderState> : {},
  } as StateFile;
}

function objectRows(config: JsonObject, key: string): JsonObject[] {
  const value = config[key];
  return Array.isArray(value) ? value.filter(isObject) : [];
}

function modelId(entry: JsonObject): string {
  return typeof entry.model === 'string' ? entry.model : '';
}

function entryBaseUrl(entry: JsonObject, source: Source): string {
  const value = entry[source === 'settings' ? 'baseUrl' : 'base_url'];
  return typeof value === 'string' ? value : '';
}

function entryApiKey(entry: JsonObject, source: Source): string {
  const value = entry[source === 'settings' ? 'apiKey' : 'api_key'];
  return typeof value === 'string' ? value : '';
}

function entryReasoningEffort(entry: JsonObject, source: Source): string | undefined {
  const direct = entry[source === 'settings' ? 'reasoningEffort' : 'reasoning_effort'];
  if (typeof direct === 'string' && REASONING_EFFORTS.has(direct) && direct !== 'default') return direct;
  const extraArgs = entry[source === 'settings' ? 'extraArgs' : 'extra_args'];
  if (!isObject(extraArgs) || typeof extraArgs.reasoning_effort !== 'string') return undefined;
  return REASONING_EFFORTS.has(extraArgs.reasoning_effort) && extraArgs.reasoning_effort !== 'default'
    ? extraArgs.reasoning_effort
    : undefined;
}

function baseModelId(modelIdValue: string): string | undefined {
  const normalized = modelIdValue.trim().toLowerCase();
  const familyId = normalized.split('/').at(-1)!;
  if (familyId === 'gpt-5.6' || familyId === 'gpt-5.6-latest') return 'gpt-5.6-sol';
  if (MODEL_REASONING_PROFILES[familyId]) return familyId;
  return Object.keys(MODEL_REASONING_PROFILES)
    .sort((left, right) => right.length - left.length)
    .find((candidate) => familyId.includes(candidate));
}

function reasoningProfile(modelIdValue: string): { efforts: string[]; defaultEffort: string; baseModelId?: string } | undefined {
  const normalized = modelIdValue.trim().toLowerCase();
  const familyId = normalized.split('/').at(-1)!;
  const base = baseModelId(normalized);
  const exact = MODEL_REASONING_PROFILES[base || familyId];
  return exact ? { ...exact, ...(base ? { baseModelId: base } : {}) } : undefined;
}

function managedIds(state: ManagedProviderState): string[] {
  return Array.isArray(state.managedModels) ? state.managedModels.filter((item): item is string => typeof item === 'string' && item.length > 0) : [];
}

function findManagedEntries(
  provider: RuntimeProvider,
  providerState: ManagedProviderState,
  settings: JsonObject,
  legacy: JsonObject,
): Map<string, [Source, JsonObject]> {
  const managed = new Set(managedIds(providerState));
  const found = new Map<string, [Source, JsonObject]>();
  const locations = isObject(providerState.locations) ? providerState.locations as Record<string, Source> : {};
  for (const [source, rows] of [
    ['settings' as Source, objectRows(settings, 'customModels')],
    ['legacy' as Source, objectRows(legacy, 'custom_models')],
  ] as const) {
    for (const entry of rows) {
      const id = modelId(entry);
      if (!managed.has(id) || found.has(id)) continue;
      if (locations[id] && locations[id] !== source) continue;
      if (entryBaseUrl(entry, source) === provider.baseUrl) found.set(id, [source, entry]);
    }
  }
  return found;
}

function positiveInt(value: unknown): number | undefined {
  if (typeof value === 'boolean' || value === null || value === undefined || value === '') return undefined;
  const result = Number(value);
  return Number.isInteger(result) && result > 0 ? result : undefined;
}

function first(raw: JsonObject, names: string[]): unknown {
  for (const name of names) if (raw[name] !== null && raw[name] !== undefined) return raw[name];
  return undefined;
}

function boolMetadata(raw: JsonObject, names: string[]): boolean | undefined {
  const direct = first(raw, names);
  if (typeof direct === 'boolean') return direct;
  if (isObject(raw.capabilities)) {
    const nested = first(raw.capabilities, names);
    if (typeof nested === 'boolean') return nested;
  }
  return undefined;
}

export function normalizeModels(payload: unknown): DiscoveredModel[] {
  let rows: unknown = payload;
  if (isObject(payload)) rows = Array.isArray(payload.data) ? payload.data : payload.models;
  if (!Array.isArray(rows)) throw new ByokError('invalid_response', 'The provider returned an invalid model list.');
  const seen = new Set<string>();
  const result: DiscoveredModel[] = [];
  for (const value of rows) {
    if (!isObject(value)) continue;
    const rawId = first(value, ['id', 'model', 'model_id']);
    if (typeof rawId !== 'string' || !rawId.trim() || seen.has(rawId.trim())) continue;
    const id = rawId.trim();
    seen.add(id);
    const rawDisplay = first(value, ['display_name', 'displayName', 'name']);
    const model: DiscoveredModel = {
      id,
      displayName: typeof rawDisplay === 'string' && rawDisplay.trim() ? rawDisplay.trim() : id,
    };
    const contextLength = positiveInt(first(value, ['context_length', 'contextLength', 'context_window', 'max_context_length', 'input_token_limit']));
    const maxOutputTokens = positiveInt(first(value, ['max_output_tokens', 'maxOutputTokens', 'output_token_limit']));
    if (contextLength) model.contextLength = contextLength;
    if (maxOutputTokens) model.maxOutputTokens = maxOutputTokens;
    let supportsImages = boolMetadata(value, ['supports_images', 'supportsImages', 'vision']);
    const modalities = first(value, ['input_modalities', 'inputModalities', 'modalities']);
    if (supportsImages === undefined && Array.isArray(modalities)) {
      supportsImages = modalities.some((item) => ['image', 'images', 'vision'].includes(String(item).toLowerCase()));
    }
    if (supportsImages !== undefined) model.supportsImages = supportsImages;
    const supportsReasoning = boolMetadata(value, ['supports_reasoning', 'supportsReasoning', 'reasoning']);
    if (supportsReasoning !== undefined) model.supportsReasoning = supportsReasoning;
    const recommended = boolMetadata(value, ['recommended', 'is_recommended', 'isRecommended', 'latest']);
    if (recommended !== undefined) model.recommended = recommended;
    const created = positiveInt(first(value, ['created', 'created_at', 'createdAt']));
    if (created) model.created = created;
    result.push(model);
  }
  if (!result.length) throw new ByokError('no_models', 'The provider returned no usable models for this key.');
  if (!result.some((model) => model.recommended)) {
    const dated = result.filter((model) => model.created !== undefined);
    const selected = dated.length
      ? dated.reduce((latest, model) => (model.created! > latest.created! ? model : latest))
      : result[0];
    selected.recommended = true;
  }
  return result;
}

async function readLimited(response: Response): Promise<Uint8Array> {
  const declared = Number(response.headers.get('content-length') || 0);
  if (declared > MAX_RESPONSE_BYTES) throw new ByokError('invalid_response', 'The provider response was too large.');
  if (!response.body) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for await (const chunk of response.body as unknown as AsyncIterable<Uint8Array>) {
    size += chunk.byteLength;
    if (size > MAX_RESPONSE_BYTES) throw new ByokError('invalid_response', 'The provider response was too large.');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, size);
}

async function downloadModels(provider: RuntimeProvider, apiKey: string, allowHttp = false): Promise<DiscoveredModel[]> {
  if (!apiKey.trim()) throw new ByokError('invalid_key', 'Enter an API key.');
  const modelsUrl = new URL(provider.modelsUrl);
  if (!['https:', ...(allowHttp ? ['http:'] : [])].includes(modelsUrl.protocol)) {
    throw new ByokError('invalid_provider', 'The provider endpoint is not trusted.');
  }
  let response: Response;
  try {
    response = await fetch(provider.modelsUrl, {
      method: 'GET',
      redirect: 'error',
      headers: { Authorization: `Bearer ${apiKey.trim()}`, Accept: 'application/json', 'User-Agent': 'oroio-byok/1' },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    const errorName = isObject(error) && typeof error.name === 'string' ? error.name : '';
    if (errorName === 'TimeoutError' || errorName === 'AbortError') {
      throw new ByokError('timeout', 'The provider request timed out.');
    }
    throw new ByokError('network_error', 'Could not connect to the provider.');
  }
  if (response.status === 401) throw new ByokError('invalid_key', 'The provider rejected this API key (401).');
  if (response.status === 403) throw new ByokError('forbidden', 'This API key cannot list models (403).');
  if (response.status === 429) throw new ByokError('rate_limited', 'The provider is rate limiting requests. Try again later.');
  if (!response.ok) throw new ByokError('provider_error', `The provider returned HTTP ${response.status}.`);
  let body: Uint8Array;
  try {
    body = await readLimited(response);
  } catch (error) {
    if (error instanceof ByokError) throw error;
    throw new ByokError('network_error', 'Could not read the provider response.');
  }
  try {
    return normalizeModels(JSON.parse(new TextDecoder().decode(body)) as unknown);
  } catch (error) {
    if (error instanceof ByokError) throw error;
    throw new ByokError('invalid_response', 'The provider returned invalid JSON.');
  }
}

function legacyView(entry: JsonObject, source: Source): CustomModel {
  if (source === 'legacy') return { ...entry } as CustomModel;
  const result: CustomModel = {
    model: typeof entry.model === 'string' ? entry.model : '',
    base_url: typeof entry.baseUrl === 'string' ? entry.baseUrl : '',
    api_key: typeof entry.apiKey === 'string' ? entry.apiKey : '',
    provider: (entry.provider || 'generic-chat-completion-api') as CustomModel['provider'],
  };
  if (typeof entry.displayName === 'string') result.model_display_name = entry.displayName;
  if (typeof entry.maxOutputTokens === 'number') result.max_tokens = entry.maxOutputTokens;
  if (typeof entry.reasoningEffort === 'string') result.reasoning_effort = entry.reasoningEffort;
  if (typeof entry.enableThinking === 'boolean') result.enable_thinking = entry.enableThinking;
  if (typeof entry.thinkingMaxTokens === 'number') result.thinking_max_tokens = entry.thinkingMaxTokens;
  if (typeof entry.baseModelId === 'string') result.base_model_id = entry.baseModelId;
  if (isObject(entry.extraArgs)) result.extra_args = entry.extraArgs;
  if (isObject(entry.extraHeaders)) result.extra_headers = entry.extraHeaders as Record<string, string>;
  if (typeof entry.noImageSupport === 'boolean') result.supports_images = !entry.noImageSupport;
  return result;
}

function currentView(model: CustomModel, previous: JsonObject = {}): JsonObject {
  const result = { ...previous };
  const pairs: Array<[keyof CustomModel, string]> = [
    ['model', 'model'], ['model_display_name', 'displayName'], ['base_url', 'baseUrl'],
    ['api_key', 'apiKey'], ['provider', 'provider'], ['max_tokens', 'maxOutputTokens'],
    ['reasoning_effort', 'reasoningEffort'], ['enable_thinking', 'enableThinking'],
    ['thinking_max_tokens', 'thinkingMaxTokens'], ['base_model_id', 'baseModelId'],
    ['extra_args', 'extraArgs'], ['extra_headers', 'extraHeaders'],
  ];
  for (const [legacy, current] of pairs) if (model[legacy] !== undefined) result[current] = model[legacy];
  if (model.supports_images !== undefined) result.noImageSupport = !model.supports_images;
  return result;
}

export async function listCustomModels(): Promise<CustomModel[]> {
  const [settings, legacy] = await Promise.all([readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {})]);
  const current = objectRows(settings, 'customModels');
  const currentIds = new Set(current.map(modelId));
  return [
    ...current.map((entry) => legacyView(entry, 'settings')),
    ...objectRows(legacy, 'custom_models').filter((entry) => !currentIds.has(modelId(entry))).map((entry) => legacyView(entry, 'legacy')),
  ];
}

function visibleEntries(settings: JsonObject, legacy: JsonObject): Array<[Source, JsonObject]> {
  const current = objectRows(settings, 'customModels');
  const currentIds = new Set(current.map(modelId));
  return [
    ...current.map((entry): [Source, JsonObject] => ['settings', entry]),
    ...objectRows(legacy, 'custom_models').filter((entry) => !currentIds.has(modelId(entry))).map((entry): [Source, JsonObject] => ['legacy', entry]),
  ];
}

export async function removeCustomModel(index: number): Promise<void> {
  const [settings, legacy] = await Promise.all([readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {})]);
  const visible = visibleEntries(settings, legacy);
  if (index < 0 || index >= visible.length) throw new ByokError('invalid_index', 'Model index is out of range.');
  const [source, target] = visible[index];
  if (source === 'settings') {
    settings.customModels = objectRows(settings, 'customModels').filter((entry) => entry !== target);
    await atomicWriteJson(SETTINGS_PATH, settings);
  } else {
    legacy.custom_models = objectRows(legacy, 'custom_models').filter((entry) => entry !== target);
    await atomicWriteJson(LEGACY_PATH, legacy);
  }
}

export async function updateCustomModel(index: number, model: CustomModel): Promise<void> {
  const [settings, legacy] = await Promise.all([readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {})]);
  const visible = visibleEntries(settings, legacy);
  if (index < 0) {
    settings.customModels = [...objectRows(settings, 'customModels'), currentView(model)];
    await atomicWriteJson(SETTINGS_PATH, settings);
    return;
  }
  if (index >= visible.length) throw new ByokError('invalid_index', 'Model index is out of range.');
  const [source, target] = visible[index];
  if (source === 'settings') {
    settings.customModels = objectRows(settings, 'customModels').map((entry) => entry === target ? currentView(model, target) : entry);
    await atomicWriteJson(SETTINGS_PATH, settings);
  } else {
    legacy.custom_models = objectRows(legacy, 'custom_models').map((entry) => entry === target ? { ...target, ...model } : entry);
    await atomicWriteJson(LEGACY_PATH, legacy);
  }
}

export async function listProviders(): Promise<ProviderStatus[]> {
  const [settings, legacy, state] = await Promise.all([readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {}), readState()]);
  return Object.values(TRUSTED_PROVIDERS).map((provider) => {
    const saved = state.providers[provider.id] || {};
    const managedModelIds = managedIds(saved);
    return {
      ...provider,
      configured: managedModelIds.length > 0 && findManagedEntries(provider, saved, settings, legacy).size > 0,
      managedModelIds,
      unavailableModelIds: Array.isArray(saved.unavailable) ? saved.unavailable : [],
      lastDiscoveredAt: saved.lastDiscoveredAt,
    };
  });
}

function mergeDiscovery(
  providerId: string,
  models: DiscoveredModel[],
  settings: JsonObject,
  legacy: JsonObject,
  state: StateFile,
  providerOverride?: RuntimeProvider,
): DiscoveryResult {
  const provider = providerOverride || providerFor(providerId);
  const saved = state.providers[providerId] || {};
  const managedModelIds = managedIds(saved);
  const found = findManagedEntries(provider, saved, settings, legacy);
  const configured = managedModelIds.length > 0 && found.size > 0;
  const currentIds = new Set(models.map((model) => model.id));
  const output = models.map((model) => {
    const previous = found.get(model.id);
    const profile = reasoningProfile(model.id);
    return {
      ...model,
      ...(profile ? {
        reasoningEfforts: profile.efforts,
        defaultReasoningEffort: profile.defaultEffort,
      } : {}),
      ...(previous ? { reasoningEffort: entryReasoningEffort(previous[1], previous[0]) } : {}),
      selected: configured ? managedModelIds.includes(model.id) : Boolean(model.recommended),
      isNew: configured && !managedModelIds.includes(model.id),
      unavailable: false,
    };
  });
  const known = new Map((saved.knownModels || []).map((model) => [model.id, model]));
  for (const id of managedModelIds) {
    if (currentIds.has(id)) continue;
    const previous = found.get(id);
    const view = previous ? legacyView(previous[1], previous[0]) : undefined;
    const profile = reasoningProfile(id);
    output.push({
      ...(known.get(id) || { id, displayName: id }),
      ...(profile ? {
        reasoningEfforts: profile.efforts,
        defaultReasoningEffort: profile.defaultEffort,
      } : {}),
      displayName: view?.model_display_name || known.get(id)?.displayName || id,
      ...(previous ? { reasoningEffort: entryReasoningEffort(previous[1], previous[0]) } : {}),
      selected: true,
      isNew: false,
      unavailable: true,
    });
  }
  return {
    success: true,
    provider: providerId,
    configured,
    models: output,
    recommendedModelIds: models.filter((model) => model.recommended).map((model) => model.id),
    managedModelIds,
  };
}

export async function discoverProvider(providerId: string, apiKey: string): Promise<DiscoveryResult> {
  const normalizedId = providerId.toLowerCase();
  const provider = providerFor(normalizedId);
  const models = await downloadModels(provider, apiKey);
  const [settings, legacy, state] = await Promise.all([readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {}), readState()]);
  return mergeDiscovery(normalizedId, models, settings, legacy, state);
}

function makeEntry(
  provider: RuntimeProvider,
  model: DiscoveredModel,
  apiKey: string,
  source: Source,
  previous?: JsonObject,
): JsonObject {
  const entry = { ...(previous || {}) };
  if (source === 'legacy') {
    delete entry.max_tokens;
    delete entry.supports_images;
    Object.assign(entry, {
      model: model.id,
      model_display_name: model.displayName,
      base_url: provider.baseUrl,
      api_key: apiKey,
      provider: provider.droidProvider,
    });
    if (model.maxOutputTokens !== undefined) entry.max_tokens = model.maxOutputTokens;
    if (model.supportsImages !== undefined) entry.supports_images = model.supportsImages;
    return entry;
  }
  delete entry.maxOutputTokens;
  delete entry.noImageSupport;
  Object.assign(entry, {
    model: model.id,
    displayName: model.displayName,
    baseUrl: provider.baseUrl,
    apiKey,
    provider: provider.droidProvider,
  });
  if (provider.authMode) entry.authMode = provider.authMode;
  if (model.maxOutputTokens !== undefined) entry.maxOutputTokens = model.maxOutputTokens;
  if (model.supportsImages === false) entry.noImageSupport = true;
  return entry;
}

async function syncProvider(
  providerId: string,
  apiKey: string,
  selectedModelIds: string[],
  discoveredModels: DiscoveredModel[],
  providerOverride?: RuntimeProvider,
): Promise<ApplyResult> {
  const provider = providerOverride || providerFor(providerId);
  const [settings, legacy, state, legacyExists] = await Promise.all([
    readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {}), readState(), exists(LEGACY_PATH),
  ]);
  const saved = state.providers[providerId] || {};
  const oldManaged = managedIds(saved);
  const found = findManagedEntries(provider, saved, settings, legacy);
  if (!Array.isArray(selectedModelIds) || selectedModelIds.length === 0) {
    throw new ByokError('invalid_selection', 'Select at least one model, or remove the provider.');
  }
  const selected = [...new Set(selectedModelIds.filter((id) => typeof id === 'string' && id.length > 0))];
  if (!selected.length) throw new ByokError('invalid_selection', 'Select at least one model, or remove the provider.');
  const discovered = new Map(discoveredModels.map((model) => [model.id, model]));
  if (selected.some((id) => !discovered.has(id) && !oldManaged.includes(id))) {
    throw new ByokError('invalid_selection', 'One or more selected models are not available from this provider.');
  }
  const oldSet = new Set(oldManaged);
  const settingsRows = objectRows(settings, 'customModels').filter(
    (entry) => !(oldSet.has(modelId(entry)) && entryBaseUrl(entry, 'settings') === provider.baseUrl),
  );
  const legacyRows = objectRows(legacy, 'custom_models').filter(
    (entry) => !(oldSet.has(modelId(entry)) && entryBaseUrl(entry, 'legacy') === provider.baseUrl),
  );
  const known = new Map((saved.knownModels || []).map((model) => [model.id, model]));
  const locations: Record<string, Source> = {};
  const unavailable: string[] = [];
  for (const id of selected) {
    const previous = found.get(id);
    const source = previous?.[0] || 'settings';
    const model = discovered.get(id) || known.get(id) || { id, displayName: id };
    if (!discovered.has(id)) unavailable.push(id);
    const entry = makeEntry(provider, model, apiKey.trim(), source, previous?.[1]);
    const profile = reasoningProfile(id);
    if (source === 'settings' && profile?.baseModelId) entry.baseModelId = profile.baseModelId;
    if (source === 'settings') settingsRows.push(entry); else legacyRows.push(entry);
    locations[id] = source;
  }
  settings.customModels = settingsRows;
  legacy.custom_models = legacyRows;
  const knownModels = [...discoveredModels, ...unavailable.map((id) => known.get(id) || { id, displayName: id })];
  state.providers[providerId] = {
    managedModels: selected,
    locations,
    knownModels,
    lastDiscoveredAt: new Date().toISOString(),
    unavailable,
    baseUrl: provider.baseUrl,
  };
  await atomicWriteJson(SETTINGS_PATH, settings);
  if (legacyExists || Object.values(locations).includes('legacy')) await atomicWriteJson(LEGACY_PATH, legacy);
  await atomicWriteJson(STATE_PATH, state);
  return { success: true, provider: providerId, managedModelIds: selected, unavailableModelIds: unavailable };
}

export async function applyProvider(providerId: string, apiKey: string, modelIds: string[]): Promise<ApplyResult> {
  const normalizedId = providerId.toLowerCase();
  const provider = providerFor(normalizedId);
  const effectiveKey = apiKey.trim() ? apiKey : await savedKey(normalizedId);
  const models = await downloadModels(provider, effectiveKey);
  return syncProvider(normalizedId, effectiveKey, modelIds, models);
}

export function normalizeOpenAIBaseUrl(baseUrl: string): string {
  if (!baseUrl.trim()) throw new ByokError('invalid_base_url', 'Enter an OpenAI-compatible Base URL.');
  let parsed: URL;
  try {
    parsed = new URL(baseUrl.trim());
  } catch {
    throw new ByokError('invalid_base_url', 'Enter a valid OpenAI-compatible Base URL.');
  }
  if (
    !['http:', 'https:'].includes(parsed.protocol)
    || parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash
  ) {
    throw new ByokError(
      'invalid_base_url',
      'Base URL must be an HTTP(S) URL without credentials, query parameters, or fragments.',
    );
  }
  let pathname = parsed.pathname.replace(/\/+$/, '');
  if (pathname.endsWith('/models')) pathname = pathname.slice(0, -7).replace(/\/+$/, '');
  parsed.pathname = pathname;
  parsed.search = '';
  parsed.hash = '';
  return parsed.toString().replace(/\/$/, '');
}

function openAICompatibleProvider(baseUrl: string): RuntimeProvider {
  const normalized = normalizeOpenAIBaseUrl(baseUrl);
  const suffix = createHash('sha256').update(normalized).digest('hex').slice(0, 16);
  return {
    id: `openai-compatible:${suffix}`,
    name: 'OpenAI-compatible',
    modelsUrl: `${normalized}/models`,
    baseUrl: normalized,
    droidProvider: 'generic-chat-completion-api',
  };
}

export async function discoverOpenAICompatible(baseUrl: string, apiKey: string): Promise<DiscoveryResult & { baseUrl: string }> {
  const provider = openAICompatibleProvider(baseUrl);
  const models = await downloadModels(provider, apiKey, true);
  const [settings, legacy, state] = await Promise.all([readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {}), readState()]);
  return {
    ...mergeDiscovery(provider.id, models, settings, legacy, state, provider),
    baseUrl: provider.baseUrl,
  };
}

export async function applyOpenAICompatible(
  baseUrl: string,
  apiKey: string,
  modelIds: string[],
): Promise<ApplyResult & { baseUrl: string }> {
  const provider = openAICompatibleProvider(baseUrl);
  const models = await downloadModels(provider, apiKey, true);
  return {
    ...await syncProvider(provider.id, apiKey, modelIds, models, provider),
    baseUrl: provider.baseUrl,
  };
}

async function savedKey(providerId: string): Promise<string> {
  const provider = providerFor(providerId);
  const [settings, legacy, state] = await Promise.all([readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {}), readState()]);
  const saved = state.providers[providerId] || {};
  const found = findManagedEntries(provider, saved, settings, legacy);
  for (const id of managedIds(saved)) {
    const entry = found.get(id);
    if (entry) {
      const key = entryApiKey(entry[1], entry[0]);
      if (key) return key;
    }
  }
  throw new ByokError('not_configured', `${provider.name} is not configured.`);
}

export async function refreshProvider(providerId: string): Promise<ApplyResult> {
  const normalizedId = providerId.toLowerCase();
  const provider = providerFor(normalizedId);
  const apiKey = await savedKey(normalizedId);
  const models = await downloadModels(provider, apiKey);
  const stateBefore = await readState();
  const result = await syncProvider(normalizedId, apiKey, managedIds(stateBefore.providers[normalizedId] || {}), models);
  const [settings, legacy, state] = await Promise.all([readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {}), readState()]);
  result.models = mergeDiscovery(normalizedId, models, settings, legacy, state).models;
  return result;
}

export async function removeProvider(providerId: string): Promise<{ success: true; provider: string; removedModelIds: string[] }> {
  const normalizedId = providerId.toLowerCase();
  const provider = providerFor(normalizedId);
  const [settings, legacy, state, legacyExists] = await Promise.all([
    readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {}), readState(), exists(LEGACY_PATH),
  ]);
  const removedModelIds = managedIds(state.providers[normalizedId] || {});
  const managed = new Set(removedModelIds);
  if (managed.size) {
    settings.customModels = objectRows(settings, 'customModels').filter(
      (entry) => !(managed.has(modelId(entry)) && entryBaseUrl(entry, 'settings') === provider.baseUrl),
    );
    legacy.custom_models = objectRows(legacy, 'custom_models').filter(
      (entry) => !(managed.has(modelId(entry)) && entryBaseUrl(entry, 'legacy') === provider.baseUrl),
    );
    await atomicWriteJson(SETTINGS_PATH, settings);
    if (legacyExists) await atomicWriteJson(LEGACY_PATH, legacy);
  }
  delete state.providers[normalizedId];
  await atomicWriteJson(STATE_PATH, state);
  return { success: true, provider: normalizedId, removedModelIds };
}

const OPENAI_COMPATIBLE_PREFIX = 'openai-compatible:';
const EXPORT_VERSION = 1;

export interface ByokExportProvider {
  name: string;
  baseUrl: string;
  managedModelIds: string[];
  models: JsonObject[];
  unavailableModelIds?: string[];
  lastDiscoveredAt?: string;
}

export interface ByokExportPayload extends JsonObject {
  version: number;
  exportedAt: string;
  providers: Record<string, ByokExportProvider>;
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

function entryCurrent(entry: JsonObject, source: Source): JsonObject {
  return source === 'settings' ? { ...entry } : currentView(entry as unknown as CustomModel);
}

function exportProviderPayload(
  providerId: string,
  provider: RuntimeProvider,
  saved: ManagedProviderState,
  found: Map<string, [Source, JsonObject]>,
): ByokExportProvider {
  const managed = managedIds(saved).filter((id) => found.has(id));
  const payload: ByokExportProvider = {
    name: provider.name || providerId,
    baseUrl: provider.baseUrl,
    managedModelIds: managed,
    models: managed.map((id) => {
      const [source, entry] = found.get(id)!;
      return entryCurrent(entry, source);
    }),
  };
  const unavailable = Array.isArray(saved.unavailable)
    ? saved.unavailable.filter((item): item is string => typeof item === 'string')
    : [];
  if (unavailable.length) payload.unavailableModelIds = unavailable;
  if (typeof saved.lastDiscoveredAt === 'string') payload.lastDiscoveredAt = saved.lastDiscoveredAt;
  return payload;
}

function collectExportProviders(
  settings: JsonObject,
  legacy: JsonObject,
  state: StateFile,
): Record<string, ByokExportProvider> {
  const rowsBySource: Array<[Source, JsonObject[]]> = [
    ['settings', objectRows(settings, 'customModels')],
    ['legacy', objectRows(legacy, 'custom_models')],
  ];
  const claimed = new Set<JsonObject>();
  const result: Record<string, ByokExportProvider> = {};
  const deferred: Array<[string, ManagedProviderState]> = [];

  const scan = (provider: RuntimeProvider, saved: ManagedProviderState): Map<string, [Source, JsonObject]> => {
    const managed = new Set(managedIds(saved));
    const locations = isObject(saved.locations) ? saved.locations as Record<string, Source> : {};
    const found = new Map<string, [Source, JsonObject]>();
    for (const [source, rows] of rowsBySource) {
      for (const entry of rows) {
        const id = modelId(entry);
        if (!managed.has(id) || found.has(id) || claimed.has(entry)) continue;
        if (locations[id] && locations[id] !== source) continue;
        if (entryBaseUrl(entry, source) === provider.baseUrl) found.set(id, [source, entry]);
      }
    }
    return found;
  };

  for (const [providerId, rawSaved] of Object.entries(state.providers)) {
    const saved = rawSaved || {};
    if (!managedIds(saved).length) continue;
    const official = TRUSTED_PROVIDERS[providerId.toLowerCase()];
    if (official) {
      const found = scan(official, saved);
      if (!found.size) continue;
      for (const [, entry] of found.values()) claimed.add(entry);
      result[providerId] = exportProviderPayload(providerId, official, saved, found);
      continue;
    }
    if (providerId.startsWith(OPENAI_COMPATIBLE_PREFIX)) {
      if (typeof saved.baseUrl === 'string' && saved.baseUrl) {
        const provider = openAICompatibleProvider(saved.baseUrl);
        const found = scan(provider, saved);
        if (!found.size) continue;
        for (const [, entry] of found.values()) claimed.add(entry);
        result[providerId] = exportProviderPayload(providerId, provider, saved, found);
      } else {
        deferred.push([providerId, saved]);
      }
    }
  }

  const officialUrls = new Set(Object.values(TRUSTED_PROVIDERS).map((provider) => provider.baseUrl));
  for (const [providerId, saved] of deferred) {
    const managed = new Set(managedIds(saved));
    let baseUrl = '';
    const found = new Map<string, [Source, JsonObject]>();
    for (const [source, rows] of rowsBySource) {
      for (const entry of rows) {
        const id = modelId(entry);
        if (!managed.has(id) || found.has(id) || claimed.has(entry)) continue;
        const url = entryBaseUrl(entry, source);
        if (!url || officialUrls.has(url)) continue;
        if (baseUrl && url !== baseUrl) continue;
        baseUrl = url;
        found.set(id, [source, entry]);
      }
    }
    if (!found.size) continue;
    const provider = openAICompatibleProvider(baseUrl);
    for (const [, entry] of found.values()) claimed.add(entry);
    result[providerId] = exportProviderPayload(providerId, provider, saved, found);
  }
  return result;
}

export async function exportByokConfig(): Promise<ByokExportResult> {
  const [settings, legacy, state] = await Promise.all([readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {}), readState()]);
  const providers = collectExportProviders(settings, legacy, state);
  if (!Object.keys(providers).length) throw new ByokError('not_configured', 'No BYOK provider is configured yet.');
  return {
    success: true,
    payload: { version: EXPORT_VERSION, exportedAt: new Date().toISOString(), providers },
    providers: Object.fromEntries(
      Object.entries(providers).map(([id, provider]) => [id, { managedModelIds: provider.managedModelIds }]),
    ),
  };
}

function importProviderFor(providerId: string, data: unknown): [string, RuntimeProvider] {
  if (!isObject(data)) throw new ByokError('invalid_import', 'Provider entries in the import file must be objects.');
  const official = TRUSTED_PROVIDERS[providerId.toLowerCase()];
  if (official) {
    if (data.baseUrl !== official.baseUrl) {
      throw new ByokError('invalid_import', `Provider ${official.id} was exported with a different Base URL.`);
    }
    return [official.id, official];
  }
  if (providerId.startsWith(OPENAI_COMPATIBLE_PREFIX)) {
    const provider = openAICompatibleProvider(typeof data.baseUrl === 'string' ? data.baseUrl : '');
    if (provider.id !== providerId) {
      throw new ByokError('invalid_import', 'OpenAI-compatible provider id does not match its Base URL.');
    }
    return [provider.id, provider];
  }
  throw new ByokError('invalid_import', `Unknown provider in the import file: ${providerId}`);
}

function sanitizeImportedEntry(raw: unknown, provider: RuntimeProvider): JsonObject {
  if (!isObject(raw)) throw new ByokError('invalid_import', 'Model entries in the import file must be objects.');
  const rawModel = raw.model;
  const rawKey = raw.apiKey;
  if (typeof rawModel !== 'string' || !rawModel.trim()) {
    throw new ByokError('invalid_import', 'A model entry in the import file has no model id.');
  }
  if (typeof rawKey !== 'string' || !rawKey.trim()) {
    throw new ByokError('invalid_import', `Model ${rawModel} has no API key.`);
  }
  const id = rawModel.trim();
  const entry: JsonObject = {
    model: id,
    displayName: typeof raw.displayName === 'string' && raw.displayName.trim() ? raw.displayName.trim() : id,
    baseUrl: provider.baseUrl,
    apiKey: rawKey,
    provider: provider.droidProvider,
  };
  if (provider.authMode) entry.authMode = provider.authMode;
  const maxOutputTokens = positiveInt(raw.maxOutputTokens);
  if (maxOutputTokens !== undefined) entry.maxOutputTokens = maxOutputTokens;
  if (typeof raw.noImageSupport === 'boolean') entry.noImageSupport = raw.noImageSupport;
  if (typeof raw.reasoningEffort === 'string' && REASONING_EFFORTS.has(raw.reasoningEffort) && raw.reasoningEffort !== 'default') {
    entry.reasoningEffort = raw.reasoningEffort;
  }
  if (typeof raw.enableThinking === 'boolean') entry.enableThinking = raw.enableThinking;
  const thinkingMaxTokens = positiveInt(raw.thinkingMaxTokens);
  if (thinkingMaxTokens !== undefined) entry.thinkingMaxTokens = thinkingMaxTokens;
  if (isObject(raw.extraArgs)) entry.extraArgs = raw.extraArgs;
  if (isObject(raw.extraHeaders)) entry.extraHeaders = raw.extraHeaders;
  const base = baseModelId(id);
  if (base) entry.baseModelId = base;
  return entry;
}

export async function importByokConfig(payloadValue: unknown, force = false): Promise<ByokImportResult> {
  if (
    !isObject(payloadValue)
    || payloadValue.version !== EXPORT_VERSION
    || !isObject(payloadValue.providers)
    || !Object.keys(payloadValue.providers).length
  ) {
    throw new ByokError('invalid_import', 'The import file is not a valid BYOK export.');
  }
  const [settings, legacy, state, legacyExists] = await Promise.all([
    readJson(SETTINGS_PATH, {}), readJson(LEGACY_PATH, {}), readState(), exists(LEGACY_PATH),
  ]);
  let settingsRows = objectRows(settings, 'customModels');
  let legacyRows = objectRows(legacy, 'custom_models');
  const imported: string[] = [];
  const skipped: string[] = [];

  for (const [rawId, rawData] of Object.entries(payloadValue.providers as Record<string, unknown>)) {
    const [providerId, provider] = importProviderFor(rawId, rawData);
    const data = rawData as ByokExportProvider;
    if (!Array.isArray(data.models) || !data.models.length) {
      throw new ByokError('invalid_import', `Provider ${rawId} has no importable models.`);
    }
    const byId = new Map<string, JsonObject>();
    for (const item of data.models) {
      const entry = sanitizeImportedEntry(item, provider);
      if (!byId.has(entry.model as string)) byId.set(entry.model as string, entry);
    }
    const unavailable = Array.isArray(data.unavailableModelIds)
      ? data.unavailableModelIds.filter((item): item is string => typeof item === 'string' && byId.has(item))
      : [];

    const saved = state.providers[providerId] || {};
    const oldManaged = managedIds(saved);
    if (oldManaged.length && findManagedEntries(provider, saved, settings, legacy).size && !force) {
      skipped.push(providerId);
      continue;
    }
    const oldSet = new Set(oldManaged);
    settingsRows = settingsRows.filter(
      (entry) => !(oldSet.has(modelId(entry)) && entryBaseUrl(entry, 'settings') === provider.baseUrl),
    );
    legacyRows = legacyRows.filter(
      (entry) => !(oldSet.has(modelId(entry)) && entryBaseUrl(entry, 'legacy') === provider.baseUrl),
    );
    const knownModels: DiscoveredModel[] = [];
    for (const [id, entry] of byId) {
      settingsRows.push(entry);
      const known: DiscoveredModel = { id, displayName: entry.displayName as string };
      if (entry.maxOutputTokens !== undefined) known.maxOutputTokens = entry.maxOutputTokens as number;
      if (entry.noImageSupport !== undefined) known.supportsImages = !entry.noImageSupport;
      knownModels.push(known);
    }
    state.providers[providerId] = {
      managedModels: [...byId.keys()],
      locations: Object.fromEntries([...byId.keys()].map((id) => [id, 'settings' as Source])),
      knownModels,
      lastDiscoveredAt: typeof data.lastDiscoveredAt === 'string' ? data.lastDiscoveredAt : new Date().toISOString(),
      unavailable,
      baseUrl: provider.baseUrl,
    };
    imported.push(providerId);
  }

  if (imported.length) {
    settings.customModels = settingsRows;
    await atomicWriteJson(SETTINGS_PATH, settings);
    if (legacyExists) {
      legacy.custom_models = legacyRows;
      await atomicWriteJson(LEGACY_PATH, legacy);
    }
    await atomicWriteJson(STATE_PATH, state);
  }
  return { success: true, importedProviders: imported, skippedProviders: skipped };
}
