export const defaultDomainSuffixes = [
  '.com',
  '.cn',
  '.net',
  '.org',
  '.com.cn',
  '.net.cn',
  '.org.cn',
  '.gov.cn',
  '.ac.cn',
  '.cc',
  '.top',
  '.xyz',
  '.vip',
  '.shop',
  '.store',
  '.online',
  '.site',
  '.tech',
  '.app',
  '.dev',
  '.ai',
  '.io',
  '.co',
  '.me',
  '.tv',
  '.pro',
  '.info',
  '.biz',
  '.name',
  '.mobi',
  '.cloud',
  '.live',
  '.club',
  '.space',
  '.work',
  '.website',
  '.email',
  '.social',
  '.art',
  '.design',
  '.blog',
  '.digital',
  '.agency',
  '.center',
  '.company',
  '.network',
  '.solutions',
  '.services',
  '.systems',
  '.software',
  '.games',
  '.group',
  '.global',
  '.studio',
  '.media',
  '.zone',
  '.academy',
  '.education',
  '.events',
  '.finance',
  '.fund',
  '.capital',
  '.marketing',
  '.support',
  '.tools',
  '.link',
  '.click',
  '.download',
  '.fun',
  '.icu',
  '.中国',
  '.公司',
  '.网络',
  '.政务',
  '.公益',
  '.集团',
  '.商城',
  '.网址',
  '.在线',
  '.中文网'
];

const storageKey = 'domain-suffixes';

export function loadDomainSuffixes() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
    if (Array.isArray(saved))
      return [...new Set(saved.filter(item => typeof item === 'string' && item.startsWith('.')))] as string[];
  } catch {
    /* use defaults */
  }
  return [...defaultDomainSuffixes];
}

export function saveDomainSuffixes(suffixes: string[]) {
  localStorage.setItem(storageKey, JSON.stringify([...new Set(suffixes)]));
}
