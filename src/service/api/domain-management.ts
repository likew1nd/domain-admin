import { useAuthStore } from '@/store/modules/auth';
import { getAuthorization, handleExpiredRequest } from '../request/shared';
import type { RequestInstanceState } from '../request/type';

export interface DomainRecord {
  domain: string;
  deletion_status: string;
  creation_date: string;
  expiration_date: string;
  wechat_status: string;
  qq_status: string;
  pollution_status: string;
  blocked_status: string;
  blacklist_status: string;
  filing_nature: string;
  filing_info: string;
  source: string;
  joined_at: string;
  query_time?: string;
}

/** 列表统计卡片的维度，对应接口 with_stats=true 时返回的 stats */
export type DomainStatKey =
  | 'deletion_status'
  | 'wechat_status'
  | 'qq_status'
  | 'pollution_status'
  | 'blocked_status'
  | 'blacklist_status'
  | 'filing_nature'
  | 'source';

export type DomainListStats = Record<DomainStatKey, { label: string; count: number }[]>;

export interface DomainPage {
  records: DomainRecord[];
  current: number;
  size: number;
  total: number;
  stats?: DomainListStats;
}

export interface DomainSource {
  id: number;
  source_key: string;
  name: string;
  adapter: 'gname' | 'west_cn' | 'generic';
  base_url: string;
  page_path: string;
  download_template: string;
  parser: 'line' | 'csv';
  enabled: boolean | number;
  has_cookie: boolean;
  created_at: string;
  updated_at: string;
}

export interface ImportRun {
  id: number;
  source_id: number;
  source_key: string;
  requested_date: string;
  file_format: string;
  trigger_type: string;
  status: 'queued' | 'running' | 'success' | 'failure';
  total_count: number;
  inserted_count: number;
  updated_count: number;
  downloaded_filename: string;
  error: string;
  started_at: string;
  finished_at: string;
}

export interface Schedule {
  id: number;
  source_id: number;
  source_name: string;
  source_key: string;
  name: string;
  run_time: string;
  suffixes: string[];
  enabled: boolean | number;
  last_run_key: string;
  last_run_at: string;
  created_at: string;
  updated_at: string;
}

export interface DomainSuffix {
  suffix: string;
  count: number;
  group: 'two' | 'three' | 'four' | 'chinese' | 'other';
}

/** 域名组成的字符类别：英文、数字、中文、符号；多选表示只由所选类别组成，空数组表示不限 */
export type DomainCharClass = 'letter' | 'digit' | 'chinese' | 'symbol';

export interface QueryTask {
  id: number;
  name: string;
  domain_info_source: 'whois' | 'apihz';
  apihz_id: string;
  apihz_key_configured?: boolean;
  filters: {
    lengths: number[];
    suffixes: string[];
    exclude_chars: string[];
    domain_composition: DomainCharClass[];
    delete_type: 'all' | 'expired' | 'redemption' | 'pending_delete';
    expiration_start: string;
    expiration_end: string;
    registration_start: string;
    registration_end: string;
    exceptions: QueryExceptionSettings;
    intercept_checks?: InterceptCheckItem[];
  };
  proxy: { mode: string; endpoint: string; max_requests: number; stages?: string[] };
  threads: number;
  whois_retries: number;
  icp_retries: number;
  qq_retries: number;
  wechat_retries: number;
  douyin_retries: number;
  blocked_retries: number;
  pollution_retries: number;
  blacklist_retries: number;
  random_query: boolean | number;
  continuous: boolean | number;
  status: 'created' | 'running' | 'completed' | 'stopped' | 'failed';
  stop_requested: boolean | number;
  total_count: number;
  processed_count: number;
  qualified_count: number;
  unqualified_count: number;
  proxy_acquired_count: number;
  proxy_current_available: number;
  proxy_current_ip: string;
  current_domain: string;
  error: string;
  created_at: string;
  started_at: string;
  finished_at: string;
  updated_at: string;
}

/** 拦截检测项：微信、QQ、污染、拦截（被墙）、黑名单 */
export type InterceptCheckItem = 'wechat' | 'qq' | 'pollution' | 'blocked' | 'blacklist';

export type QueryExceptionLogic = 'and' | 'or';

export interface QueryExceptionScheme {
  name: string;
  enabled: boolean;
  logic: QueryExceptionLogic;
  lengths: number[];
  suffixes: string[];
  patterns: string[];
  contains: string[];
  exclude_chars: string[];
}

export interface QueryExceptionSettings {
  /** Legacy single-scheme fields kept for old saved settings. */
  enabled?: boolean;
  lengths: number[];
  suffixes: string[];
  patterns: string[];
  contains: string[];
  schemes: QueryExceptionScheme[];
}

export interface QueryResult {
  domain: string;
  source: string;
  joined_at: string;
  query_time: string;
  result: 'qualified' | 'unqualified';
  reason: string;
  deletion_status: string;
  whois_status: string;
  expiration_date: string;
  creation_date: string;
  icp_found: boolean | number;
  qq_status: string;
  wechat_status: string;
  pollution_status: string;
  blocked_status: string;
  blacklist_status: string;
  filing_nature: string;
  filing_info: string;
  checked_at: string;
  last_checked_at: string;
}

export interface QueryResultPage {
  records: QueryResult[];
  current: number;
  size: number;
  total: number;
  stats?: DomainListStats;
}

export interface QueryLog {
  id: number;
  task_id: number;
  created_at: string;
  level: 'info' | 'warning' | 'error';
  stage: string;
  domain: string;
  message: string;
  detail: string;
}

interface ApiResponse<T> {
  code: string;
  data: T;
  msg: string;
}

/** 域名接口不经过 axios 封装，需自行携带令牌，并与 request 共用令牌续期状态 */
const tokenState: RequestInstanceState = { errMsgStack: [], refreshTokenPromise: null };

/** 处理令牌相关的业务码，返回 true 表示令牌已续期、应重发请求 */
async function handleTokenCode(code: string, retried: boolean) {
  if (!retried && import.meta.env.VITE_SERVICE_EXPIRED_TOKEN_CODES?.split(',').includes(code)) {
    return handleExpiredRequest(tokenState);
  }
  if (import.meta.env.VITE_SERVICE_LOGOUT_CODES?.split(',').includes(code)) {
    await useAuthStore().resetStore();
  }
  return false;
}

async function domainRequest<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const authorization = getAuthorization();
  if (authorization) headers.set('Authorization', authorization);
  const response = await fetch(`/api${path}`, {
    ...init,
    headers
  });
  const body = (await response.json()) as ApiResponse<T> | { detail?: string | { msg: string }[] };
  if ('code' in body && (await handleTokenCode(String(body.code), retried))) {
    return domainRequest<T>(path, init, true);
  }
  if (!response.ok || !('code' in body) || body.code !== '0000') {
    let message = '请求失败';
    // 参数校验失败（422）时 detail 为错误数组
    if ('detail' in body) {
      message = (Array.isArray(body.detail) ? body.detail.map(item => item.msg).join('；') : body.detail) || message;
    } else if ('msg' in body) message = body.msg || message;
    throw new Error(message);
  }
  return body.data;
}

export function fetchDomainPage(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') search.set(key, String(value));
  });
  return domainRequest<DomainPage>(`/domains?${search.toString()}`);
}

export function clearExpiredDomains() {
  return domainRequest<{ deleted: number }>('/domains', { method: 'DELETE' });
}

export function deleteSelectedDomains(domains: string[]) {
  return domainRequest<{ deleted: number }>('/domains/delete-selected', {
    method: 'POST',
    body: JSON.stringify({ domains })
  });
}

export function qualifySelectedDomains(domains: string[]) {
  return domainRequest<{ added: number; skipped: number }>('/domains/qualify-selected', {
    method: 'POST',
    body: JSON.stringify({ domains })
  });
}

export function deleteFilteredDomains(filters: Record<string, string>) {
  return domainRequest<{ deleted: number }>('/domains/delete-filtered', {
    method: 'POST',
    body: JSON.stringify(filters)
  });
}

export function fetchDomainSuffixes() {
  return domainRequest<DomainSuffix[]>('/domains/suffixes');
}

export function importGeneratedDomains(domains: string[]) {
  return domainRequest<{ inserted: number; updated: number; total: number }>('/domains/generated', {
    method: 'POST',
    body: JSON.stringify({ domains })
  });
}

export function fetchQueryTasks() {
  return domainRequest<QueryTask[]>('/query-tasks');
}

export function fetchQuerySettings() {
  return domainRequest<{ settings: CreateQueryTaskPayload; updated_at: string } | null>('/query-settings');
}

export function previewQueryTask(payload: {
  lengths: number[];
  suffixes: string[];
  exclude_chars: string[];
  domain_composition: CreateQueryTaskPayload['domain_composition'];
}) {
  return domainRequest<{ total: number }>('/query-tasks/preview', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

export interface CreateQueryTaskPayload {
  domain_info_source: 'whois' | 'apihz';
  apihz_id: string;
  apihz_key: string;
  apihz_key_configured?: boolean;
  lengths: number[];
  suffixes: string[];
  exclude_chars: string[];
  domain_composition: DomainCharClass[];
  delete_type: 'all' | 'expired' | 'redemption' | 'pending_delete';
  expiration_start: string;
  expiration_end: string;
  registration_start: string;
  registration_end: string;
  exceptions: QueryExceptionSettings;
  threads: number;
  whois_retries: number;
  icp_retries: number;
  qq_retries: number;
  wechat_retries: number;
  douyin_retries: number;
  blocked_retries: number;
  pollution_retries: number;
  blacklist_retries: number;
  intercept_checks: InterceptCheckItem[];
  random_query: boolean;
  continuous: boolean;
  proxy_mode: 'direct' | 'tunnel' | 'api';
  proxy_endpoint: string;
  proxy_max_requests: number;
  proxy_stages: string[];
}

export function saveQuerySettings(payload: CreateQueryTaskPayload) {
  return domainRequest<{ settings: CreateQueryTaskPayload; updated_at: string }>('/query-settings', {
    method: 'PUT',
    body: JSON.stringify(payload)
  });
}

export function fetchInterceptKeyStatus() {
  return domainRequest<{ configured: boolean }>('/query-settings/intercept-key');
}

/** 传空字符串表示清除已保存的 Key */
export function saveInterceptKey(apiKey: string) {
  return domainRequest<{ configured: boolean }>('/query-settings/intercept-key', {
    method: 'PUT',
    body: JSON.stringify({ api_key: apiKey })
  });
}

export function createQueryTask(payload: CreateQueryTaskPayload) {
  return domainRequest<QueryTask>('/query-tasks', { method: 'POST', body: JSON.stringify(payload) });
}

export function stopQueryTask(taskId: number) {
  return domainRequest<QueryTask>(`/query-tasks/${taskId}/stop`, { method: 'POST' });
}

export function fetchQueryResults(
  result: 'qualified' | 'unqualified',
  options: {
    page?: number;
    pageSize?: number;
    withStats?: boolean;
    filters?: Record<string, string | undefined>;
  } = {}
) {
  const page = options.page || 1;
  const pageSize = options.pageSize || 20;
  const params = options.filters || {};
  const search = new URLSearchParams({ result, page: String(page), page_size: String(pageSize) });
  if (options.withStats) search.set('with_stats', 'true');
  Object.entries(params).forEach(([key, value]) => {
    if (value) search.set(key, value);
  });
  return domainRequest<QueryResultPage>(`/query-results?${search.toString()}`);
}

export function fetchQueryLogs(taskId?: number, limit = 500) {
  const search = new URLSearchParams({ limit: String(limit) });
  if (taskId) search.set('task_id', String(taskId));
  return domainRequest<QueryLog[]>(`/query-logs?${search.toString()}`);
}

export function clearQueryResults(result: 'qualified' | 'unqualified') {
  return domainRequest<{ deleted: number }>(`/query-results/${result}`, { method: 'DELETE' });
}

export function kickSelectedQueryResults(domains: string[]) {
  return domainRequest<{ kicked: number; skipped: number }>('/query-results/kick-selected', {
    method: 'POST',
    body: JSON.stringify({ domains })
  });
}

export interface RegistrarApi {
  id: number;
  name: string;
  adapter: 'http_json' | 'aliyun_intl' | 'dynadot' | 'gname' | 'godaddy';
  endpoint: string;
  headers_json: string;
  config_json: string;
  enabled: boolean;
  has_token: boolean;
  created_at: string;
  updated_at: string;
}

export interface RegistrarApiPayload {
  name: string;
  adapter: RegistrarApi['adapter'];
  endpoint: string;
  token?: string;
  headers_json: string;
  config_json: string;
  clear_token?: boolean;
  enabled: boolean;
}

export interface MonitorSettings {
  interval_seconds: number;
  concurrency: number;
  whois_retries: number;
  auto_register: boolean;
  auto_start: boolean;
  availability_api_id: number;
  api_ids: number[];
  updated_at?: string;
}

export interface MonitorStatus {
  id: number;
  status: 'stopped' | 'running' | 'stopping' | 'error';
  total_count: number;
  checked_count: number;
  available_count: number;
  registered_count: number;
  kicked_count: number;
  current_domain: string;
  last_error: string;
  started_at: string;
  updated_at: string;
}

export interface MonitorLog {
  id: number;
  created_at: string;
  level: 'info' | 'warning' | 'error';
  stage: string;
  domain: string;
  message: string;
  detail: string;
}

export interface KickedDomain {
  domain: string;
  deletion_status: string;
  creation_date: string;
  expiration_date: string;
  wechat_status: string;
  qq_status: string;
  pollution_status: string;
  blocked_status: string;
  blacklist_status: string;
  filing_nature: string;
  filing_info: string;
  reason: string;
  detail: string;
  source: string;
  kicked_at: string;
}

export interface KickedDomainPage {
  records: KickedDomain[];
  current: number;
  size: number;
  total: number;
  stats?: DomainListStats;
}

export interface RegisteredDomain {
  id: number;
  domain: string;
  deletion_status: string;
  creation_date: string;
  expiration_date: string;
  wechat_status: string;
  qq_status: string;
  pollution_status: string;
  blocked_status: string;
  blacklist_status: string;
  filing_nature: string;
  filing_info: string;
  source: string | null;
  registrar_name: string;
  response: string;
  registered_at: string;
}

export interface RegisteredDomainPage {
  records: RegisteredDomain[];
  current: number;
  size: number;
  total: number;
  stats?: DomainListStats;
}

export function fetchRegistrarApis() {
  return domainRequest<RegistrarApi[]>('/registrar-apis');
}

export function createRegistrarApi(payload: RegistrarApiPayload) {
  return domainRequest<RegistrarApi>('/registrar-apis', { method: 'POST', body: JSON.stringify(payload) });
}

export function updateRegistrarApi(id: number, payload: RegistrarApiPayload) {
  return domainRequest<RegistrarApi>(`/registrar-apis/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
}

export function deleteRegistrarApi(id: number) {
  return domainRequest<{ deleted: number }>(`/registrar-apis/${id}`, { method: 'DELETE' });
}

export function testRegistrarApi(id: number) {
  return domainRequest<{ success: boolean; status_code: number; response: string }>(`/registrar-apis/${id}/test`, {
    method: 'POST'
  });
}

export function fetchMonitorSettings() {
  return domainRequest<MonitorSettings>('/monitor/settings');
}

export function saveMonitorSettings(payload: MonitorSettings) {
  return domainRequest<MonitorSettings>('/monitor/settings', { method: 'PUT', body: JSON.stringify(payload) });
}

export function fetchMonitorStatus() {
  return domainRequest<MonitorStatus>('/monitor/status');
}

export function startMonitor() {
  return domainRequest<MonitorStatus>('/monitor/start', { method: 'POST' });
}

export function stopMonitor() {
  return domainRequest<MonitorStatus>('/monitor/stop', { method: 'POST' });
}

export function fetchMonitorLogs(limit = 500) {
  return domainRequest<MonitorLog[]>(`/monitor/logs?limit=${limit}`);
}

export function fetchKickedDomains(params: Record<string, string | number | undefined> = {}) {
  const search = new URLSearchParams({
    page: String(params.page || 1),
    page_size: String(params.pageSize || 20)
  });
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '' && key !== 'page' && key !== 'pageSize') search.set(key, String(value));
  });
  return domainRequest<KickedDomainPage>(`/kicked-domains?${search.toString()}`);
}

export function clearKickedDomains() {
  return domainRequest<{ deleted: number }>('/kicked-domains', { method: 'DELETE' });
}

export function fetchRegisteredDomains(params: Record<string, string | number | undefined> = {}) {
  const search = new URLSearchParams({
    page: String(params.page || 1),
    page_size: String(params.pageSize || 20)
  });
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '' && key !== 'page' && key !== 'pageSize') search.set(key, String(value));
  });
  return domainRequest<RegisteredDomainPage>(`/registered-domains?${search.toString()}`);
}

export function clearRegisteredDomains() {
  return domainRequest<{ deleted: number }>('/registered-domains', { method: 'DELETE' });
}

export function resetDomainQueryTime() {
  return domainRequest<{ reset: number; cleared_results: number }>('/domains/reset-query-time', { method: 'POST' });
}

export function fetchSources() {
  return domainRequest<DomainSource[]>('/sources');
}

export function saveSource(payload: Record<string, unknown>, sourceId?: number) {
  return domainRequest<DomainSource>(sourceId ? `/sources/${sourceId}` : '/sources', {
    method: sourceId ? 'PUT' : 'POST',
    body: JSON.stringify(payload)
  });
}

export function testSource(sourceId: number) {
  return domainRequest<{ authenticated: boolean; available_dates: string[] }>(`/sources/${sourceId}/test`, {
    method: 'POST'
  });
}

export function deleteSource(sourceId: number) {
  return domainRequest<{ deleted: number; removed_schedules: number }>(`/sources/${sourceId}`, { method: 'DELETE' });
}

export function queueCollection(payload: {
  source_id: number;
  requested_date: string;
  file_format: string;
  suffixes?: string[];
}) {
  return domainRequest<{ run_id: number }>('/collect', { method: 'POST', body: JSON.stringify(payload) });
}

export function queueLatest(sourceId: number, fileFormat = 'txt', suffixes: string[] = []) {
  const search = new URLSearchParams({ source_id: String(sourceId), file_format: fileFormat });
  suffixes.forEach(suffix => search.append('suffixes', suffix));
  return domainRequest<{ run_id: number }>(`/collect/latest?${search.toString()}`, {
    method: 'POST'
  });
}

export function queueWestSuffixes(payload: { source_id: number; suffixes: string[] }) {
  return domainRequest<{ run_ids: number[] }>('/collect/west', { method: 'POST', body: JSON.stringify(payload) });
}

export function uploadTxtCollection(payload: {
  sourceId: number;
  requestedDate: string;
  file: File;
  suffixes?: string[];
}) {
  const formData = new FormData();
  formData.append('file', payload.file);
  const search = new URLSearchParams({ source_id: String(payload.sourceId), requested_date: payload.requestedDate });
  payload.suffixes?.forEach(suffix => search.append('suffixes', suffix));
  return domainRequest<{ run_id: number }>(`/collect/upload?${search.toString()}`, { method: 'POST', body: formData });
}

export function fetchRuns(limit = 50, scope: 'all' | 'manual' | 'scheduled' = 'all') {
  return domainRequest<ImportRun[]>(`/runs?limit=${limit}&scope=${scope}`);
}

export function fetchSchedules() {
  return domainRequest<Schedule[]>('/schedules');
}

export function saveSchedule(
  payload: { source_id: number; name: string; run_time: string; suffixes: string[]; enabled: boolean },
  id?: number
) {
  return domainRequest<Schedule>(id ? `/schedules/${id}` : '/schedules', {
    method: id ? 'PUT' : 'POST',
    body: JSON.stringify(payload)
  });
}

export function deleteSchedule(id: number) {
  return domainRequest<{ deleted: number }>(`/schedules/${id}`, { method: 'DELETE' });
}

export function runSchedule(id: number) {
  return domainRequest<{ run_id: number; reused?: boolean }>(`/schedules/${id}/run`, { method: 'POST' });
}

export interface NameValue {
  name: string;
  value: number;
}

export interface DashboardStats {
  total: number;
  queried: number;
  pending: number;
  queryRate: number;
  suffixCount: number;
  todayJoined: number;
  yesterdayJoined: number;
  bySource: NameValue[];
  byKind: NameValue[];
  byLength: NameValue[];
  bySuffix: NameValue[];
  trend: { days: string[]; series: { name: string; data: number[] }[] };
  generatedAt: string;
}

export interface DashboardChecks {
  total: number;
  qualified: number;
  unqualified: number;
  kicked: number;
  qualifiedRate: number;
  todayChecked: number;
  todayQualified: number;
  reasons: NameValue[];
  deletionStatus: NameValue[];
  filingNature: NameValue[];
  risks: NameValue[];
  latestQualified: {
    domain: string;
    expiration_date: string;
    deletion_status: string;
    filing_nature: string;
    checked_at: string;
  }[];
}

export interface DashboardRuntime {
  queryTask: Pick<
    QueryTask,
    | 'id'
    | 'name'
    | 'status'
    | 'threads'
    | 'continuous'
    | 'total_count'
    | 'processed_count'
    | 'qualified_count'
    | 'unqualified_count'
    | 'current_domain'
    | 'error'
    | 'started_at'
    | 'finished_at'
  > | null;
  collect: {
    schedules: {
      id: number;
      name: string;
      run_time: string;
      enabled: number;
      last_run_key: string;
      last_run_at: string;
      source_name: string;
      next_run: string;
    }[];
    runs: Record<string, number>;
    todayRuns: number;
    todayInserted: number;
    todayDownloaded: number;
    latest: {
      id: number;
      status: ImportRun['status'];
      trigger_type: string;
      total_count: number;
      inserted_count: number;
      error: string;
      started_at: string;
      finished_at: string;
      source_name: string;
    } | null;
  };
  monitor: {
    status: MonitorStatus['status'];
    checked: number;
    available: number;
    registered: number;
    kicked: number;
    currentDomain: string;
    lastError: string;
    startedAt: string;
    domains: Record<string, number>;
  };
  registration: Record<string, number>;
  registrarApis: { total: number; enabled: number };
  sources: { id: number; name: string; enabled: boolean; hasCookie: boolean }[];
}

export interface DashboardAlert {
  level: 'warning' | 'error';
  title: string;
  desc: string;
  route: string;
}

export interface DashboardActivity {
  time: string;
  type: string;
  level: string;
  title: string;
  message: string;
}

export interface DashboardLive {
  stats: Pick<DashboardStats, 'total' | 'queried' | 'pending' | 'queryRate'>;
  checks: DashboardChecks;
  runtime: DashboardRuntime;
  alerts: DashboardAlert[];
  activities: DashboardActivity[];
}

export interface DashboardData extends DashboardLive {
  stats: DashboardStats;
}

/** 控制台完整数据；refresh 为 true 时强制重新统计全表 */
export function fetchDashboard(refresh = false) {
  return domainRequest<DashboardData>(`/dashboard${refresh ? '?refresh=true' : ''}`);
}

/** 控制台运行状态，用于定时刷新 */
export function fetchDashboardRuntime() {
  return domainRequest<DashboardLive>('/dashboard/runtime');
}
