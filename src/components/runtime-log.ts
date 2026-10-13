/** 日志按旧到新排列，所有运行日志统一交给 RuntimeLog 显示和处理滚动。 */
export interface RuntimeLogEntry {
  id: number;
  time: string;
  domain: string;
  stage: string;
  status: string;
  detail: string;
  tone: 'info' | 'success' | 'warning' | 'error';
}
