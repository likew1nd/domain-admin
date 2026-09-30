import type { App } from 'vue';
import { createI18n } from 'vue-i18n';
import { localStg } from '@/utils/storage';
import messages from './locale';

const i18n = createI18n({
  locale: localStg.get('lang') || 'zh-CN',
  fallbackLocale: 'en',
  messages,
  legacy: false
});

/**
 * Setup plugin i18n
 *
 * @param app
 */
export function setupI18n(app: App) {
  app.use(i18n);
}

export const $t = i18n.global.t as App.I18n.$T;

export function setLocale(locale: App.I18n.LangType) {
  i18n.global.locale.value = locale;
}

/** The system title is configured on the settings page and overrides the built-in text for every language */
export function setSystemTitle(title: string) {
  Object.keys(messages).forEach(locale => {
    i18n.global.mergeLocaleMessage(locale, { system: { title } });
  });
}

const cachedTitle = localStg.get('systemTitle');
if (cachedTitle) setSystemTitle(cachedTitle);
