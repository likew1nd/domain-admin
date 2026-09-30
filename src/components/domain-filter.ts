import type { DomainCharClass } from '@/service/api';

/** 域名组成可选的字符类别，可自由组合 */
export const compositionOptions: readonly { value: DomainCharClass; label: string }[] = [
  { value: 'letter', label: '英文' },
  { value: 'digit', label: '数字' },
  { value: 'chinese', label: '中文' },
  { value: 'symbol', label: '符号' }
];

/** 以“英文+数字”形式展示所选组成 */
export function compositionText(value: readonly DomainCharClass[]) {
  return compositionOptions
    .filter(item => value.includes(item.value))
    .map(item => item.label)
    .join('+');
}

export type DateRange = string[] | null;

export interface DomainFilterModel {
  keyword: string;
  suffix: string;
  length: string;
  /** 字符类别组合，为空表示全部 */
  domain_composition: DomainCharClass[];
  deletion_status: string;
  filing_nature: string;
  wechat_status: string;
  qq_status: string;
  pollution_status: string;
  blocked_status: string;
  blacklist_status: string;
  /** 各时间范围，键为 DateField.key */
  ranges: Record<string, DateRange>;
}

export interface DateField {
  key: string;
  label: string;
  /** 提交给接口的开始、结束参数名 */
  params: readonly [string, string];
}

export function createDomainFilter(dateFields: readonly DateField[]): DomainFilterModel {
  return {
    keyword: '',
    suffix: '',
    length: '',
    domain_composition: [],
    deletion_status: '',
    filing_nature: '',
    wechat_status: '',
    qq_status: '',
    pollution_status: '',
    blocked_status: '',
    blacklist_status: '',
    ranges: Object.fromEntries(dateFields.map(field => [field.key, null]))
  };
}

/** 将筛选模型转换为接口查询参数 */
export function toFilterParams(model: DomainFilterModel, dateFields: readonly DateField[]) {
  const { ranges, domain_composition, ...fields } = model;
  const params: Record<string, string> = { ...fields, domain_composition: domain_composition.join(',') };
  dateFields.forEach(({ key, params: [start, end] }) => {
    params[start] = ranges[key]?.[0] || '';
    params[end] = ranges[key]?.[1] || '';
  });
  return params;
}
